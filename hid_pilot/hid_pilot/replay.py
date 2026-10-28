"""
Ограниченный видеобуфер OBS и независимое сохранение после события.
"""

import base64
import hashlib
import json
import logging
import threading
import time
import tomllib
import uuid
from collections import deque
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, SecretStr

logger = logging.getLogger("hid_pilot.replay")
REPLAY_SECONDS = 120
DEATH_TAIL_SECONDS = 15


class ReplayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: int = Field(default=4455, ge=1, le=65535)
    password: SecretStr = SecretStr("")


class OBSReplay:
    def __init__(self, config: ReplayConfig) -> None:
        # Импорт ленивый: при выключенной записи OBS и websocket не требуются.
        from websocket import create_connection

        self.socket = create_connection(f"ws://127.0.0.1:{config.port}", timeout=5)
        self.saved: str | None = None
        self.owned = False
        try:
            hello = self.receive()
            if hello["op"] != 0:
                raise ValueError("OBS не прислал Hello")
            identify = {"rpcVersion": 1, "eventSubscriptions": 64}
            auth = hello["d"].get("authentication")
            if auth:
                secret = base64.b64encode(hashlib.sha256(
                    (config.password.get_secret_value() + auth["salt"]).encode()).digest()).decode()
                identify["authentication"] = base64.b64encode(hashlib.sha256(
                    (secret + auth["challenge"]).encode()).digest()).decode()
            self.socket.send(json.dumps({"op": 1, "d": identify}))
            if self.receive()["op"] != 2:
                raise ValueError("OBS не подтвердил авторизацию")
        except Exception:
            self.close()
            raise

    def receive(self) -> dict:
        message = json.loads(self.socket.recv())
        if message.get("op") == 5 and message["d"].get("eventType") == "ReplayBufferSaved":
            self.saved = message["d"]["eventData"]["savedReplayPath"]
        return message

    def request(self, name: str, **data: object) -> dict:
        request_id = uuid.uuid4().hex
        self.socket.send(json.dumps({"op": 6, "d": {
            "requestType": name, "requestId": request_id, "requestData": data}}))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            message = self.receive()
            if message.get("op") == 7 and message["d"].get("requestId") == request_id:
                response = message["d"]
                if not response["requestStatus"]["result"]:
                    raise ValueError(f"OBS отклонил {name}")
                return response.get("responseData", {})
        raise TimeoutError("Нет ответа OBS")

    def start(self) -> None:
        if self.request("GetReplayBufferStatus")["outputActive"]:
            raise ValueError("Replay Buffer уже занят; остановите его в OBS")
        mode = self.request("GetProfileParameter", parameterCategory="Output",
                            parameterName="Mode")["parameterValue"]
        category = "AdvOut" if mode == "Advanced" else "SimpleOutput"
        for name, expected in (("RecRBTime", str(REPLAY_SECONDS)), ("RecRBSize", "512")):
            value = self.request("GetProfileParameter", parameterCategory=category,
                                 parameterName=name)["parameterValue"]
            if str(value) != expected:
                raise ValueError(f"В OBS задайте {name} = {expected}")
        self.request("StartReplayBuffer")
        self.owned = True
        # Ответ OBS подтверждает запрос; NVENC запускается асинхронно.
        deadline = time.monotonic() + 10
        while not self.request("GetReplayBufferStatus")["outputActive"]:
            if time.monotonic() >= deadline:
                raise ValueError("Replay Buffer не включился")
            time.sleep(0.1)

    def save(self) -> Path:
        self.saved = None
        self.request("SaveReplayBuffer")
        deadline = time.monotonic() + 15
        while self.saved is None and time.monotonic() < deadline:
            self.receive()
        if self.saved is None:
            raise TimeoutError("OBS не прислал ReplayBufferSaved")
        path = Path(self.saved)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("Файл Replay Buffer отсутствует или пуст")
        return path

    def stop(self) -> None:
        self.request("StopReplayBuffer")

    def close(self) -> None:
        self.socket.close()


class ReplayLog(logging.Handler):
    def __init__(self, clock: Callable[[], float]) -> None:
        super().__init__()
        self.clock = clock
        self.rows: deque[tuple[float, str]] = deque(maxlen=10000)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        now = self.clock()
        self.rows.append((now, self.format(record)[:2000]))
        while self.rows and self.rows[0][0] < now - REPLAY_SECONDS:
            self.rows.popleft()


class FarmReplay:
    def __init__(self, config_path: Path, status: Callable[[str], None],
                 factory: Callable = OBSReplay, clock: Callable[[], float] = time.monotonic) -> None:
        self.config_path = config_path
        self.status = status
        self.factory = factory
        self.clock = clock
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.worker: threading.Thread | None = None
        self.busy = False
        self.ready = False
        self.closing = False
        self.incident: dict | None = None
        self.client = None
        self.log = ReplayLog(clock)
        self.started = 0.0
        self.ended = False
        self.discard = False
        self.completions: list[Future[Path | None]] = []
        self.queued_incidents: deque[tuple[dict, float, Future[Path | None]]] = deque()

    def prepare(self) -> None:
        with self.lock:
            if self.busy or self.closing:
                raise ValueError("Дождитесь сохранения видео предыдущего запуска")
            self.busy = True
            self.ready = False
            self.worker = None
            self.incident = None
            self.ended = False
            self.discard = False
            self.completions = []
            self.queued_incidents.clear()
            self.wake.clear()
            self.client = None
        try:
            if not self.config_path.exists():
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                self.config_path.write_text('[obs]\nport = 4455\npassword = ""\n', encoding="utf-8")
            config = ReplayConfig.model_validate(tomllib.loads(
                self.config_path.read_text("utf-8-sig")).get("obs", {}))
            self.client = self.factory(config)
            self.client.start()
            self.started = self.clock()
            self.session = uuid.uuid4().hex
            self.log.rows.clear()
            logging.getLogger("hid_pilot").addHandler(self.log)
            self.status("Буфер включён")
            logger.info("Replay Buffer включён для сессии %s", self.session)
            with self.lock:
                self.ready = True
                # STOP мог прийти во время подключения или запуска NVENC.
                if self.discard:
                    self.worker = threading.Thread(target=self._save, name="FarmReplay", daemon=True)
                    self.worker.start()
        except Exception:
            if self.client is not None:
                if self.client.owned:
                    try:
                        self.client.stop()
                    except Exception:
                        logger.error("Не удалось освободить Replay Buffer после ошибки старта")
                try:
                    self.client.close()
                except Exception:
                    logger.error("Не удалось закрыть соединение OBS после ошибки старта")
                finally:
                    self.client = None
            self.busy = False
            # Текст сетевого исключения может содержать секреты.
            raise ValueError("OBS недоступен или Replay Buffer не настроен; проверьте obs-replay.toml и OBS") from None

    def trigger(self, reason: str, *, final: bool = False, death: bool = False,
                preserve_death: bool = False, completion: Future[Path | None] | None = None) -> None:
        with self.lock:
            self.ended = self.ended or final
            if not self.busy:
                if completion is not None:
                    completion.set_result(None)
                return
            if final and not death:
                if preserve_death and self.incident is not None and not self.discard:
                    # Штатный конец добивания сохраняет уже запрошенный ролик смерти.
                    return
                # STOP отменяет ожидающий ролик смерти и не создаёт новый.
                self.discard = True
                self.wake.set()
                if self.ready and self.worker is None:
                    self.worker = threading.Thread(target=self._save, name="FarmReplay", daemon=True)
                    self.worker.start()
                return
            if not self.ready or not death or self.discard:
                if completion is not None:
                    completion.set_result(None)
                return
            event_clock = self.clock()
            incident = {"session": self.session, "reason": reason,
                        "event_at": datetime.now(timezone.utc).isoformat(),
                        "event_elapsed": max(0, event_clock - self.started),
                        "requested_tail_seconds": DEATH_TAIL_SECONDS}
            if self.incident is not None:
                # У второго алерта свой момент смерти и собственный 15-секундный хвост.
                if completion is not None:
                    if len(self.queued_incidents) < 8:
                        self.queued_incidents.append((incident, event_clock, completion))
                    else:
                        completion.set_result(None)
                        logger.warning("Очередь роликов смерти заполнена; дополнительный ролик пропущен")
                return
            if completion is not None:
                self.completions.append(completion)
            self.tail_seconds = DEATH_TAIL_SECONDS
            self.incident = incident
            self.event_clock = event_clock
            self.worker = threading.Thread(target=self._save, name="FarmReplay", daemon=True)
            self.worker.start()
        logger.info("Запрошен видеохвост для сессии %s: %s", self.session, reason[:2000])
        self.status(f"Дописать после события: {self.tail_seconds} секунд")

    def _save(self) -> None:
        if not self.discard:
            self.wake.wait(max(0, self.event_clock + self.tail_seconds - self.clock()))
        success = False
        path = None
        try:
            if self.discard:
                return
            path = self.client.save()
            if self.discard:
                return
            elapsed = max(0, self.clock() - self.started)
            metadata = {**self.incident, "saved_at": datetime.now(timezone.utc).isoformat(),
                        "video": str(path), "available_seconds_estimate": min(REPLAY_SECONDS, elapsed),
                        "requested_history_seconds": REPLAY_SECONDS - self.tail_seconds,
                        "post_event_seconds": max(0, self.clock() - self.event_clock),
                        "early_close": self.closing}
            path.with_suffix(path.suffix + ".json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log.acquire()
            try:
                lines = [line for stamp, line in self.log.rows if stamp >= self.clock() - REPLAY_SECONDS]
            finally:
                self.log.release()
            path.with_suffix(path.suffix + ".log").write_text("\n".join(lines), encoding="utf-8")
            self.status(f"Сохранено: {path}")
            logger.info("Видео фарма сохранено: %s", path)
            success = True
        except Exception:
            self.status("Ошибка записи OBS: сохранение видео не подтверждено")
            logger.error("Ошибка записи OBS: сохранение видео не подтверждено")
        finally:
            # После смерти фарм может продолжиться: оставляем кольцо для новых событий.
            with self.lock:
                completions, self.completions = self.completions, []
                completed_path = path if success and not self.discard else None
                if success and not self.discard and not self.closing and self.queued_incidents:
                    self.incident, self.event_clock, completion = self.queued_incidents.popleft()
                    self.completions = [completion]
                    continuing = True
                    self.worker = threading.Thread(target=self._save, name="FarmReplay", daemon=True)
                    self.worker.start()
                else:
                    completions.extend(item[2] for item in self.queued_incidents)
                    self.queued_incidents.clear()
                    continuing = success and not self.ended and not self.closing
                if continuing and not self.completions:
                    self.incident = None
                    self.worker = None
            for completion in completions:
                if not completion.done():
                    completion.set_result(completed_path)
            if not continuing:
                try:
                    self.client.stop()
                    if self.discard:
                        self.status("Буфер остановлен без сохранения")
                        logger.info("Видеобуфер остановлен без сохранения")
                except Exception:
                    self.status("Ошибка OBS: не удалось остановить принадлежащий Pilot буфер")
                    logger.error("Не удалось остановить Replay Buffer")
                finally:
                    try:
                        self.client.close()
                    except Exception:
                        logger.error("Не удалось закрыть соединение OBS")
                    finally:
                        logging.getLogger("hid_pilot").removeHandler(self.log)
                        with self.lock:
                            self.busy = False
                            self.ready = False

    def close(self) -> bool:
        self.closing = True
        self.trigger("Закрытие Pilot", final=True)
        self.wake.set()
        return self.worker is None or not self.worker.is_alive()
