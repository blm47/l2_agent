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
from dataclasses import dataclass
from pathlib import Path

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
timeout_seconds = 5
"""


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: bool = True
    token: SecretStr = SecretStr("")
    chat_id: str = ""
    notify_on_stop: bool = True
    notify_on_death: bool = True
    timeout_seconds: float = Field(default=5, ge=1, le=15)

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

    def message(self) -> str:
        return (f"HID Pilot — персонаж умер\nСценарий: {self.script[:300]}\n"
                f"Окно: {self.window[:500]}\nСобственное HP = 0. Ожидание воскрешения.")


class TelegramNotifier:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.pending: queue.Queue[ScriptStopped | PlayerDied] = queue.Queue(maxsize=8)
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

    def notify_death(self, event: PlayerDied) -> None:
        self._enqueue(event)

    def _enqueue(self, event: ScriptStopped | PlayerDied) -> None:
        with self.lock:
            try:
                self.pending.put_nowait(event)
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
                    event = self.pending.get_nowait()
                except queue.Empty:
                    self.worker = None
                    return
            try:
                self._send(event)
            except Exception:
                # Исключения HTTP содержат URL с токеном: не выводим их текст и traceback.
                logger.warning("Не удалось отправить уведомление Telegram; проверьте сеть и конфиг")
            finally:
                self.pending.task_done()

    def _send(self, event: ScriptStopped | PlayerDied) -> None:
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
