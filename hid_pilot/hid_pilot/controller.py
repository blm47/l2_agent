import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import numpy as np

from hid_pilot.desktop import Desktop, FocusLost
from hid_pilot.script import KEYS
from hid_pilot.transport import PicoTransport

logger = logging.getLogger("hid_pilot.controller")


class Stopped(Exception):
    pass


class ActionController:
    """
    Единственный путь к HID; watchdog проверяет окно даже во время CV.
    """

    def __init__(self, desktop: Desktop, transport: PicoTransport,
                 hard_stop_ready: Callable[[], bool], notify: Callable[[str], None] | None = None) -> None:
        self.desktop = desktop
        self.transport = transport
        self.hard_stop_ready = hard_stop_ready
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.paused = threading.Event()
        self.manual_paused = threading.Event()
        self.pause_generation = 0
        self.pause_started: float | None = None
        self.paused_seconds = 0.
        self.notify = notify or (lambda _: None)
        self.monitor: threading.Thread | None = None
        self.error = ""

    def check(self) -> None:
        if self.stopped.is_set():
            raise Stopped(self.error or "Сценарий остановлен")
        if not self.hard_stop_ready():
            raise RuntimeError("Hard-stop недоступен")
        self.desktop.check()

    def start(self) -> None:
        with self.lock:
            self.sync_pause()
            if not self.paused.is_set():
                self.transport.arm()
        self.monitor = threading.Thread(target=self._watch, daemon=True)
        self.monitor.start()

    def _watch(self) -> None:
        while not self.stopped.wait(0.025):
            try:
                with self.lock:
                    self.sync_pause()
                    if not self.paused.is_set():
                        self.transport.heartbeat()
            except Exception as exc:
                logger.exception("Watchdog остановил ввод")
                self.error = str(exc)
                try:
                    self.stop()
                except Exception:
                    logger.exception("Ошибка STOP; ожидается watchdog платы")

    def sync_pause(self) -> None:
        # Вызывается под lock; ручная пауза не снимается возвращением фокуса.
        focused = True
        try:
            self.check()
        except FocusLost:
            focused = False
        should_pause = self.manual_paused.is_set() or not focused
        if should_pause and not self.paused.is_set():
            self.pause_generation += 1
            self.pause_started = time.monotonic()
            self.paused.set()
            self.transport.stop()
            message = "PAUSE — вручную" if self.manual_paused.is_set() else "Автопауза — окно не активно"
            logger.info(message)
            self.notify(message)
        elif not should_pause and self.paused.is_set():
            self.transport.arm()
            self.paused_seconds += time.monotonic() - self.pause_started
            self.pause_started = None
            self.paused.clear()
            logger.info("Сценарий продолжен; выбранное окно активно")
            self.notify("Продолжение — выбранное окно активно")

    def active_time(self) -> float:
        with self.lock:
            now = time.monotonic()
            pending = 0. if self.pause_started is None else now - self.pause_started
            return now - self.paused_seconds - pending

    def pause(self) -> None:
        with self.lock:
            if self.stopped.is_set():
                return
            self.manual_paused.set()
            self.sync_pause()

    def resume(self) -> None:
        with self.lock:
            self.manual_paused.clear()
            self.sync_pause()

    def checkpoint(self) -> None:
        while True:
            with self.lock:
                self.sync_pause()
                if not self.paused.is_set():
                    return
            self.stopped.wait(0.02)

    @contextmanager
    def ready(self) -> Iterator[None]:
        while True:
            self.checkpoint()
            self.lock.acquire()
            try:
                self.sync_pause()
                if not self.paused.is_set():
                    break
            except Exception:
                self.lock.release()
                raise
            self.lock.release()
        try:
            yield
        finally:
            self.lock.release()

    def capture(self) -> np.ndarray:
        # Потеря фокуса между двумя проверками capture не завершает сценарий.
        while True:
            self.checkpoint()
            try:
                frame = self.desktop.capture()
                self.check()
                return frame
            except FocusLost:
                continue

    def stop(self) -> None:
        self.stopped.set()
        with self.lock:
            self.transport.stop()

    def close(self) -> None:
        self.stopped.set()
        if self.monitor is not None:
            self.monitor.join(timeout=1)
        with self.lock:
            self.transport.close()

    def wait(self, seconds: float) -> None:
        deadline = self.active_time() + seconds
        while True:
            self.checkpoint()
            remaining = deadline - self.active_time()
            if remaining <= 0:
                return
            self.stopped.wait(min(0.02, remaining))

    def move(self, x: float, y: float, verify: Callable[[], None] | None = None) -> None:
        if self.transport.protocol == "L2_PICO_V1":
            raise RuntimeError("Для движения мыши нужна прошивка HID_PILOT_V1; клавиши доступны")
        target = self.desktop.point(x, y)
        with self.ready():
            self.desktop.check_point(target)
            # После START курсор может быть над GUI. Входим в игру без зажатых кнопок.
            self.transport.release()
        deadline = self.active_time() + 8
        while self.active_time() < deadline:
            # Три шага между паузами ускоряют дальний ход без превышения лимита Pico.
            for _ in range(3):
                with self.ready():
                    if verify is not None:
                        verify()
                    current = self.desktop.cursor()
                    self.desktop.check_point(target)
                    dx, dy = target[0] - current[0], target[1] - current[1]
                    if abs(dx) <= 2 and abs(dy) <= 2:
                        return
                    sx = max(-16, min(16, dx // 2 if abs(dx) > 2 else 0))
                    sy = max(-16, min(16, dy // 2 if abs(dy) > 2 else 0))
                    self.transport.command(f"MOVE {sx} {sy}", "MOVED")
            self.wait(0.006)
        raise RuntimeError("HID-наведение не сошлось; клик отменён")

    def recover_view(self, deadline: float) -> None:
        """
        Ограниченное восстановление вида: WASD и три коротких поворота с ПКМ.
        """
        generation = self.pause_generation
        deadline = min(deadline, time.monotonic() + 7)

        def verify() -> None:
            self.check()
            if (self.paused.is_set() or self.pause_generation != generation
                    or time.monotonic() >= deadline):
                raise Stopped("Восстановление вида прервано паузой или лимитом времени")

        try:
            for key in ("W", "A", "S", "D"):
                with self.lock:
                    verify()
                    self.transport.hold("KEY", key, 350)
                self.wait(.37)
                with self.lock:
                    self.transport.release()
            self.move(.5, .5, verify=verify)
            for direction in (1, -1, 1):
                with self.lock:
                    verify()
                    self.desktop.check_point(self.desktop.cursor())
                    self.transport.hold("BUTTON", "right", 800)
                until = min(deadline, time.monotonic() + .6)
                while time.monotonic() < until:
                    with self.lock:
                        verify()
                        x, y = self.desktop.cursor()
                        self.desktop.check_point((x, y))
                        self.desktop.check_point((x + direction * 6, y))
                        self.transport.command(f"MOVE {direction * 6} 0", "MOVED")
                    self.wait(.02)
                with self.lock:
                    self.transport.release()
        finally:
            with self.lock:
                self.transport.release()

    def key(self, name: str, duration: int) -> None:
        if name not in KEYS or not 1 <= duration <= 1000:
            raise ValueError("Недопустимая клавиша или длительность")
        self._hold("KEY", name, duration)

    def click(self, button: str, x: float, y: float,
              verify: Callable[[], None] | None = None) -> None:
        if button not in {"left", "right"}:
            raise ValueError("Недопустимая кнопка мыши")
        self.move(x, y, verify=verify)
        if verify is not None:
            verify()
        with self.ready():
            if verify is not None:
                verify()
            current = self.desktop.cursor()
            target = self.desktop.point(x, y)
            self.desktop.check_point(current)
            if max(abs(a - b) for a, b in zip(current, target)) > 2:
                raise RuntimeError("Курсор сместился; клик отменён")
            self.transport.hold("BUTTON", button, 80)
        self.wait(0.10)
        with self.ready():
            self.transport.release()

    def _hold(self, kind: str, name: str, duration: int) -> None:
        with self.ready():
            self.transport.hold(kind, name, duration)
        self.wait(duration / 1000 + 0.02)
        with self.ready():
            self.transport.release()

    def click_current(self, button: str) -> None:
        if button not in {"left", "right"}:
            raise ValueError("Недопустимая кнопка мыши")
        with self.ready():
            self.desktop.check_point(self.desktop.cursor())
            self.transport.hold("BUTTON", button, 80)
        self.wait(.10)
        with self.ready():
            self.transport.release()
