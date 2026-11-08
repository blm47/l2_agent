import base64
import hashlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2
import numpy as np
import win32gui
from pydantic import BaseModel, ConfigDict, Field

from hid_pilot.api_controller import ApiActionController
from hid_pilot.controller import Stopped
from hid_pilot.desktop import ActivationDenied, Desktop, FocusLost, Target, activate, snapshot
from hid_pilot.duo import DuoConfig, DuoFarmer, load_duo_party, parse_duo_settings, prepare_duo
from hid_pilot.support_life import SupportLifeObserver
from hid_pilot.transport import PicoTransport
from hid_pilot.window_activation import request_activation

logger = logging.getLogger("hid_pilot.api")


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_id: str = Field(pattern=r"^[A-Za-z0-9_-]+\.duo$")


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class CameraRequest(SessionRequest):
    frame_id: int = Field(gt=0)
    target_epoch: int = Field(ge=0)
    direction: str = Field(pattern=r"^(left|right|up|down)$")


class AlignmentRequest(SessionRequest):
    frame_id: int = Field(gt=0)
    target_epoch: int = Field(ge=0)
    operation: str = Field(pattern=r"^(left|right|up|down|zoom_in|zoom_out)$")
    fine: bool = False


class ApiDuoFarmer(DuoFarmer):
    """
    Телеметрия поверх прежних алгоритмов; решения и проверки базового фарма сохраняются.
    """

    api: "PilotApiRuntime"

    def begin_target_selection(self, source: str) -> None:
        self.api.selection()
        super().begin_target_selection(source)

    def on_observation(self, image: np.ndarray, hp: float | None,
                       mp: float | None, target: float | None) -> None:
        super().on_observation(image, hp, mp, target)
        if hasattr(self, "api"):
            identity = self.last_target
            fresh = bool(identity is not None and 0 <= self.clock() - identity.at <= 1
                         and identity.dead is not True)
            self.api.observation({
                "hp": hp, "mp": mp, "target_hp": target,
                "target_kind": identity.kind if fresh and identity is not None else "unknown",
                "target_name": identity.name if fresh and identity is not None else None,
                "critical": bool(hp is None or mp is None or hp < self.config.hp_threshold
                                 or self.critical_party() or self.support_lost or self.buffing),
            })


class PilotApiRuntime:
    def __init__(self, scripts: Path, profiles: Path, farmer: Target, support: Target,
                 port: str, hard_stop_ready: Callable[[], bool], *,
                 transport_factory: Callable[..., PicoTransport] = PicoTransport,
                 controller_factory: Callable[..., ApiActionController] = ApiActionController) -> None:
        self.scripts, self.profiles = scripts.resolve(), profiles.resolve()
        self.farmer, self.support, self.port = farmer, support, port
        self.hard_stop_ready = hard_stop_ready
        self.transport_factory, self.controller_factory = transport_factory, controller_factory
        self.lock = threading.RLock()
        self.worker: threading.Thread | None = None
        self.controller: ApiActionController | None = None
        self.cancel = threading.Event()
        self.phase, self.error = "idle", ""
        self.session_id = ""
        self.target_epoch = 0
        self.attack_at: float | None = None
        self.observed_at = 0.
        self.observed: dict[str, Any] = {}
        self.frame_id = 0
        self.last_frame: dict[str, Any] | None = None
        self.camera_turns = 0
        self.alignment_steps = 0
        self.last_camera_frame = 0
        self.heartbeat_at = time.monotonic()

    def catalogue(self) -> list[str]:
        return sorted(p.stem for p in self.scripts.glob("*.duo.pilot")
                      if p.read_text("utf-8").startswith("# agent-input-policy: clicks-only\n"))

    def status(self) -> dict:
        with self.lock:
            controller = self.controller
            return {"protocol": 1, "session_id": self.session_id, "phase": self.phase,
                    "running": self.worker is not None and self.worker.is_alive(), "error": self.error,
                    "paused": bool(controller and (controller.paused.is_set() or controller.manual_paused.is_set())),
                    "camera_supported": controller.transport.supports_strong_camera is True if controller else None,
                    "target_epoch": self.target_epoch, "attack_at": self.attack_at,
                    "observed_at": self.observed_at, "observation": dict(self.observed),
                    "farmer": self.farmer.model_dump(), "support": self.support.model_dump()}

    def require_session(self, session_id: str) -> None:
        if session_id != self.session_id:
            raise ValueError("Другая API-сессия")

    def heartbeat(self, session_id: str) -> None:
        with self.lock:
            self.require_session(session_id)
            self.heartbeat_at = time.monotonic()

    def selection(self) -> None:
        with self.lock:
            self.target_epoch += 1
            self.attack_at = None
            self.camera_turns = 0
            self.alignment_steps = 0
            self.observed = {}
            self.last_frame = None

    def attack(self) -> None:
        with self.lock:
            if self.attack_at is None:
                self.attack_at = time.monotonic()

    def observation(self, value: dict) -> None:
        with self.lock:
            self.observed, self.observed_at = value, time.monotonic()

    def start(self, script_id: str) -> dict:
        if script_id not in self.catalogue():
            raise ValueError("Выберите зарегистрированный API-форк")
        config = parse_duo_settings((self.scripts / (script_id + ".pilot")).read_text("utf-8"))
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise ValueError("Сценарий уже выполняется")
            if not self.hard_stop_ready():
                raise ValueError("Hard-stop недоступен")
            self.cancel.clear()
            self.session_id = uuid4().hex
            self.phase, self.error = "preparing", ""
            self.attack_key = config.attack
            self.selection()
            self.heartbeat_at = time.monotonic()
            self.worker = threading.Thread(target=self._run, args=(config,), daemon=True,
                                           name="PilotApiFarm")
            self.worker.start()
        return self.status()

    def _run(self, config: DuoConfig) -> None:
        controller = transport = engine = None
        try:
            farmer, support = snapshot(self.farmer.hwnd), snapshot(self.support.hwnd)
            if (farmer.pid, farmer.rect, support.pid, support.rect) != (
                self.farmer.pid, self.farmer.rect, self.support.pid, self.support.rect
            ):
                raise ValueError("Окна изменились после запуска API")
            profile = prepare_duo(self.profiles, farmer, support)
            party = load_duo_party(self.profiles, farmer)
            if self.cancel.is_set():
                return
            farmer = self._activate_farmer(farmer)
            if farmer.rect != self.farmer.rect:
                raise ValueError("Геометрия МА изменилась при активации")
            transport = self.transport_factory(self.port)
            controller = self.controller_factory(Desktop(farmer), transport, self.hard_stop_ready)
            controller.on_key = lambda key: self.attack() if (
                key == self.attack_key and controller.desktop.target == self.farmer
            ) else None
            with self.lock:
                self.controller = controller
            if self.cancel.is_set():
                return
            controller.prepare_keyboard((farmer, support))
            controller.start()
            engine = ApiDuoFarmer(controller, farmer, support, profile, config,
                                  lambda message: logger.info("%s", message), party_profile=party,
                                  support_life=SupportLifeObserver(lambda: None, lambda: None))
            engine.api = self
            engine.setup_observation(self.profiles / "farmer")
            with self.lock:
                self.phase = "running"
            engine.run()
        except Stopped as exc:
            if not self.cancel.is_set():
                with self.lock:
                    self.error = str(exc)
            logger.info("API-фарм остановлен: %s", exc)
        except Exception as exc:
            logger.exception("API-фарм остановлен")
            with self.lock:
                self.error = str(exc)
        finally:
            try:
                if engine is not None:
                    engine.close_observation()
            except Exception as exc:
                logger.exception("Ошибка завершения наблюдения API")
                with self.lock:
                    self.error = f"Завершение наблюдения: {exc}"
            finally:
                try:
                    if controller is not None:
                        controller.close()
                    elif transport is not None:
                        transport.close()
                except Exception as exc:
                    logger.exception("Ошибка освобождения ввода API")
                    with self.lock:
                        self.error = f"Освобождение ввода: {exc}"
                finally:
                    with self.lock:
                        self.controller = None
                        self.phase = "failed" if self.error else "stopped"

    def _activate_farmer(self, farmer: Target) -> Target:
        source_hwnd = win32gui.GetForegroundWindow()
        try:
            current = activate(farmer)
            deadline = time.monotonic() + .15
            while True:
                if self.cancel.is_set() or not self.hard_stop_ready():
                    raise Stopped("Старт отменён во время активации МА")
                try:
                    Desktop(current).check()
                    break
                except FocusLost:
                    if time.monotonic() >= deadline:
                        raise
                    # Успешный SetForegroundWindow может кратко оставить HWND=0.
                    self.cancel.wait(.01)
        except (ActivationDenied, FocusLost):
            # START из терминала может не иметь права на прямой SetForegroundWindow.
            # Helper проверяет исходный foreground и завершается по STOP/timeout.
            logger.info("Прямая активация МА недоступна: запускаем изолированный helper")
            source = snapshot(source_hwnd)
            for strategy in ("foreground", "attached_focus", "task_switch"):
                if self.cancel.is_set() or not self.hard_stop_ready():
                    raise Stopped("Старт отменён во время активации МА")
                if win32gui.GetForegroundWindow() not in (source.hwnd, farmer.hwnd):
                    raise RuntimeError("Активное окно изменилось; старт API отменён")
                try:
                    if strategy == "foreground":
                        request_activation(source, farmer, self.cancel, self.hard_stop_ready, .75)
                    else:
                        request_activation(source, farmer, self.cancel, self.hard_stop_ready,
                                           .75, strategy=strategy)
                    break
                except RuntimeError:
                    if strategy == "task_switch":
                        raise
                    logger.warning("Активация МА методом %s не удалась; пробуем следующий способ",
                                   strategy, exc_info=True)
            current = snapshot(farmer.hwnd)
            Desktop(current).check()
        if current.pid != farmer.pid or current.rect != farmer.rect:
            raise ValueError("Окно МА изменилось при активации")
        if self.cancel.is_set():
            raise Stopped("Старт отменён во время активации МА")
        return current

    def stop(self, session_id: str | None = None) -> None:
        with self.lock:
            if session_id is not None:
                self.require_session(session_id)
            self.cancel.set()
            if self.phase in {"running", "preparing"}:
                self.phase = "stopping"
            controller = self.controller
        if controller is not None:
            controller.stopped.set()
            controller.stop()

    def check_lease(self) -> None:
        with self.lock:
            expired = self.phase in {"preparing", "running"} and time.monotonic() - self.heartbeat_at > 5
        if expired:
            logger.warning("API-клиент не прислал heartbeat: STOP")
            self.stop()

    def pause(self, session_id: str, paused: bool) -> None:
        with self.lock:
            self.require_session(session_id)
            controller = self.controller
        if controller is None:
            raise ValueError("Контроллер ещё не запущен")
        controller.pause() if paused else controller.resume()

    def frame(self) -> dict:
        controller = self.controller
        if controller is None or not controller.operations.acquire(timeout=.03):
            raise ValueError("Фарм занят вводом")
        try:
            controller.check()
            if (controller.stopped.is_set() or controller.paused.is_set()
                    or controller.manual_paused.is_set()
                    or controller.desktop.target != self.farmer):
                raise ValueError("Нет доступного кадра МА: пауза или окно саппорта")
            at = time.monotonic()
            with self.lock:
                epoch, session_id = self.target_epoch, self.session_id
            image = controller.desktop.capture()
            controller.check()
            ok, encoded = cv2.imencode(".png", image)
            if not ok:
                raise OSError("Не удалось закодировать кадр")
            payload = encoded.tobytes()
            with self.lock:
                if (epoch != self.target_epoch or session_id != self.session_id
                        or self.controller is not controller or controller.stopped.is_set()
                        or controller.paused.is_set() or controller.manual_paused.is_set()):
                    raise ValueError("Состояние изменилось во время capture")
                self.frame_id += 1
                meta = {"session_id": self.session_id, "frame_id": self.frame_id,
                        "timestamp": at, "target_epoch": self.target_epoch,
                        "width": image.shape[1], "height": image.shape[0],
                        "window": self.farmer.model_dump(), "status": self.status()}
                self.last_frame = meta
                return meta | {"sha256": hashlib.sha256(payload).hexdigest(),
                               "png": base64.b64encode(payload).decode("ascii")}
        finally:
            controller.operations.release()

    def camera(self, request: CameraRequest) -> None:
        controller = self.controller
        if controller is None or not controller.operations.acquire(timeout=.2):
            raise ValueError("Фарм занят вводом; камера отложена")
        try:
            def verify() -> None:
                now = time.monotonic()
                with self.lock:
                    self.require_session(request.session_id)
                    frame = self.last_frame
                    if (frame is None or frame["frame_id"] != request.frame_id
                            or frame["target_epoch"] != self.target_epoch
                            or request.target_epoch != self.target_epoch):
                        raise ValueError("Камера: смена цели или кадра")
                    if not 0 <= now - frame["timestamp"] <= .9:
                        raise ValueError("Камера: кадр устарел")
                    if self.attack_at is None or not 0 <= now - self.attack_at <= 20:
                        raise ValueError("Камера: нет недавней команды атаки МА")
                    if not 0 <= now - self.observed_at <= 1:
                        raise ValueError("Камера: наблюдение устарело")
                    if self.observed.get("target_kind", "unknown") not in {"monster", "unknown"}:
                        raise ValueError("Камера: известная цель не монстр")
                    if not (self.observed.get("target_hp") or 0) > 0:
                        raise ValueError("Камера: нет живого HP цели")
                    if self.observed.get("critical", True):
                        raise ValueError("Камера: критическое состояние или бафф")
                    if controller.desktop.target != self.farmer or controller.stopped.is_set():
                        raise ValueError("Камера: окно саппорта или STOP")
            verify()
            with self.lock:
                if self.camera_turns >= 32 or request.frame_id <= self.last_camera_frame:
                    raise ValueError("Лимит камеры или повтор кадра")
                self.camera_turns += 1
                self.last_camera_frame = request.frame_id
            controller.camera_turn(request.direction, verify, search=True)
        finally:
            controller.operations.release()

    def align_camera(self, request: AlignmentRequest) -> None:
        controller = self.controller
        if controller is None or not controller.operations.acquire(timeout=.2):
            raise ValueError("Фарм занят вводом; выравнивание отложено")
        try:
            def verify() -> None:
                now = time.monotonic()
                with self.lock:
                    self.require_session(request.session_id)
                    frame = self.last_frame
                    if (frame is None or frame["frame_id"] != request.frame_id
                            or frame["target_epoch"] != self.target_epoch
                            or request.target_epoch != self.target_epoch
                            or not 0 <= now - frame["timestamp"] <= .9
                            or not 0 <= now - self.observed_at <= 1
                            or self.observed.get("critical", True)
                            or controller.desktop.target != self.farmer
                            or controller.stopped.is_set() or controller.paused.is_set()
                            or controller.manual_paused.is_set()):
                        raise ValueError("Нет свежего безопасного кадра МА для выравнивания")
                    controller.check()
            verify()
            with self.lock:
                if self.alignment_steps >= 48 or request.frame_id <= self.last_camera_frame:
                    raise ValueError("Лимит выравнивания или повтор кадра")
                self.alignment_steps += 1
                self.last_camera_frame = request.frame_id
            if request.operation in {"zoom_in", "zoom_out"}:
                controller.camera_zoom(-1 if request.operation == "zoom_in" else 1, verify)
            else:
                controller.camera_turn(request.operation, verify, scale=.5 if request.fine else 1)
        finally:
            controller.operations.release()
