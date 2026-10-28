"""
Фоновые уведомления Telegram без ожидания сети в контроллере и GUI.
"""

import json
import logging
import queue
import re
import threading
import tomllib
import urllib.request
import uuid
from collections.abc import Iterator
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from hid_pilot.telegram_video import PLAYBACK_SPEED, prepare_video

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

logger = logging.getLogger("hid_pilot.telegram")

DEFAULT_CONFIG = """
[telegram]
# Заполните token и chat_id. Отправка включается после заполнения обоих полей.
enabled = true
token = ""
chat_id = ""
notify_on_stop = true
notify_on_death = true
send_death_video = true
timeout_seconds = 5
video_timeout_seconds = 120
"""


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: bool = True
    token: SecretStr = SecretStr("")
    chat_id: str = ""
    notify_on_stop: bool = True
    notify_on_death: bool = True
    send_death_video: bool = True
    timeout_seconds: float = Field(default=5, ge=1, le=15)
    video_timeout_seconds: float = Field(default=120, ge=15, le=300)

    @field_validator("chat_id", mode="before")
    @classmethod
    def text_chat_id(cls, value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError("Нужен числовой chat_id или имя канала")
        text = str(value).strip()
        if text and re.fullmatch(r"-?\d+|@[A-Za-z0-9_]+", text) is None:
            raise ValueError("Некорректный chat_id")
        return text


@dataclass(frozen=True)
class ScriptStopped:
    script: str
    window: str
    reason: str
    duration: float

    def message(self) -> str:
        elapsed = max(0, int(self.duration))
        return (f"HID Pilot — скрипт остановлен\nСценарий: {self.script[:300]}\n"
                f"Окно: {self.window[:500]}\nПричина: {self.reason[:2000]}\n"
                f"Длительность: {elapsed // 3600:02d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}")


@dataclass(frozen=True)
class PlayerDied:
    script: str
    window: str
    role: str = ""

    def message(self) -> str:
        subject = self.role if self.role else "персонаж"
        if self.role == "Саппорт":
            evidence = "Смерть подтверждена по HP=0 или кнопке To Village."
        elif self.role == "МА":
            evidence = "Собственное HP = 0."
        else:
            evidence = "Собственное HP = 0. Ожидание воскрешения."
        return (f"HID Pilot — {subject} умер\nСценарий: {self.script[:300]}\n"
                f"Окно: {self.window[:500]}\n{evidence}")


@dataclass(frozen=True)
class _Notification:
    event: ScriptStopped | PlayerDied
    video: Future[Path | None] | None = None


class _VideoUpload:
    def __init__(self, path: Path, fields: dict[str, str]) -> None:
        self.path = path
        self.boundary = uuid.uuid4().hex
        self.prefix = b"".join(
            f'--{self.boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
            for name, value in fields.items())
        self.prefix += (f'--{self.boundary}\r\nContent-Disposition: form-data; name="video"; '
                        'filename="death.mp4"\r\nContent-Type: video/mp4\r\n\r\n').encode("ascii")
        self.suffix = f"\r\n--{self.boundary}--\r\n".encode("ascii")
        self.size = len(self.prefix) + path.stat().st_size + len(self.suffix)

    def __iter__(self) -> Iterator[bytes]:
        yield self.prefix
        # Видео передаётся частями: весь ролик не копируется в память процесса.
        with self.path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                yield chunk
        yield self.suffix


class TelegramNotifier:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.pending: queue.Queue[_Notification] = queue.Queue(maxsize=8)
        self.lock = threading.Lock()
        self.worker: threading.Thread | None = None
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with config_path.open("x", encoding="utf-8") as output:
                output.write(DEFAULT_CONFIG.lstrip())
        except FileExistsError:
            pass
        except OSError:
            logger.warning("Не удалось создать конфиг Telegram; уведомления не настроены")

    def notify_stopped(self, event: ScriptStopped) -> None:
        self._enqueue(event)

    def notify_death(self, event: PlayerDied, *, video: Future[Path | None] | None = None) -> None:
        self._enqueue(event, video)

    def _enqueue(self, event: ScriptStopped | PlayerDied, video: Future[Path | None] | None = None) -> None:
        with self.lock:
            try:
                self.pending.put_nowait(_Notification(event, video))
            except queue.Full:
                logger.warning("Очередь Telegram заполнена; уведомление пропущено")
                return
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, name="TelegramNotify", daemon=True)
                try:
                    self.worker.start()
                except RuntimeError:
                    self.worker = None
                    logger.warning("Не удалось запустить отправку Telegram; уведомление отложено")

    def _run(self) -> None:
        while True:
            with self.lock:
                try:
                    notification = self.pending.get_nowait()
                except queue.Empty:
                    self.worker = None
                    return
            receipt = None
            try:
                receipt = self._send(notification.event)
                if notification.video is not None and receipt is not None:
                    config, message_id = receipt
                    if config.send_death_video:
                        if message_id is None:
                            logger.warning("Telegram не вернул ID алерта; видео не отправлено")
                        else:
                            # Следующее событие не обгоняет видео: алерт, reply с видео, затем остановка.
                            path = notification.video.result(timeout=60)
                            if path is not None:
                                self._send_video(config, message_id, path)
            except Exception:
                # Исключения HTTP содержат URL с токеном: не выводим их текст и traceback.
                if receipt is not None and notification.video is not None:
                    logger.warning("Не удалось отправить видео Telegram; алерт уже отправлен, "
                                   "проверьте сеть, FFmpeg и локальную запись")
                else:
                    logger.warning("Не удалось отправить уведомление Telegram; проверьте сеть и конфиг")
            finally:
                self.pending.task_done()

    def _send(self, event: ScriptStopped | PlayerDied) -> tuple[TelegramConfig, int | None] | None:
        try:
            with self.config_path.open("rb") as source:
                raw = source.read(65_537)
            if len(raw) > 65_536:
                raise ValueError("Конфиг слишком большой")
            config = TelegramConfig.model_validate(tomllib.loads(raw.decode("utf-8-sig")).get("telegram", {}))
        except (OSError, ValueError):
            logger.warning("Некорректный или недоступный конфиг Telegram; уведомление пропущено")
            return
        allowed = config.notify_on_death if isinstance(event, PlayerDied) else config.notify_on_stop
        if not config.enabled or not allowed or not config.chat_id or not config.token.get_secret_value():
            return
        token = config.token.get_secret_value().strip()
        if re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token) is None:
            logger.warning("Некорректный формат токена Telegram; уведомление пропущено")
            return
        data = json.dumps({"chat_id": config.chat_id, "text": event.message().replace(token, "[скрыто]")},
                          ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data,
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            body = response.read(16_385)
        if len(body) > 16_384 or json.loads(body).get("ok") is not True:
            logger.warning("Telegram не подтвердил доставку уведомления")
            return
        logger.info("Уведомление %s отправлено в Telegram",
                    "о смерти персонажа" if isinstance(event, PlayerDied) else "об остановке скрипта")
        message_id = json.loads(body).get("result", {}).get("message_id")
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
            message_id = None
        return config, message_id

    def _send_video(self, config: TelegramConfig, message_id: int, path: Path) -> None:
        token = config.token.get_secret_value().strip()
        with TemporaryDirectory(prefix="hidpilot-telegram-") as directory:
            video = Path(directory) / "death.mp4"
            prepare_video(path, video)
            upload = _VideoUpload(video, {
                "chat_id": config.chat_id, "supports_streaming": "true",
                "reply_parameters": json.dumps({"message_id": message_id,
                                                 "allow_sending_without_reply": False}),
                "caption": f"Запись события смерти персонажа · ускорено ×{PLAYBACK_SPEED}",
            })
            request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendVideo", data=upload,
                                             headers={"Content-Type": f"multipart/form-data; boundary={upload.boundary}",
                                                      "Content-Length": str(upload.size)}, method="POST")
            with urllib.request.urlopen(request, timeout=config.video_timeout_seconds) as response:
                body = response.read(16_385)
            if len(body) > 16_384 or json.loads(body).get("ok") is not True:
                logger.warning("Telegram не подтвердил доставку видео; исходный ролик сохранён локально")
                return
        logger.info("Видео смерти отправлено в Telegram ответом на алерт %s", message_id)
