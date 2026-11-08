import math
import threading
import time
from collections.abc import Callable
from typing import Any

from hid_pilot.controller import ActionController, Stopped
from hid_pilot.desktop import Target


class ApiActionController(ActionController):
    """
    API-адаптер: фарм двигает курсор только для клика, камера имеет отдельный вход.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.operations = threading.RLock()
        self._navigation = threading.local()
        self.on_key: Callable[[str], None] = lambda _: None
        self._typing_target = False

    def key(self, name: str, duration: int, *, exact: bool = False) -> None:
        with self.operations:
            super().key(name, duration, exact=exact)
            if not self._typing_target:
                self.on_key(name)

    def move(self, x: float, y: float, **kwargs: Any) -> None:
        if not getattr(self._navigation, "allowed", False):
            raise ValueError("API-форк запрещает самостоятельное движение курсора из фарма")
        super().move(x, y, **kwargs)

    def click(self, button: str, x: float, y: float, **kwargs: Any) -> None:
        with self.operations:
            self._navigation.allowed = True
            try:
                super().click(button, x, y, **kwargs)
            finally:
                self._navigation.allowed = False

    def click_current(self, button: str) -> None:
        with self.operations:
            super().click_current(button)

    def _hold(self, kind: str, name: str, duration: int) -> None:
        # Операция включает key-up: камера не обрывает удержание WASD или скилла.
        with self.operations:
            super()._hold(kind, name, duration)

    def switch_window(self, target: Target, timeout: float = .75) -> None:
        with self.operations:
            super().switch_window(target, timeout)

    def target_by_name(self, name: str) -> None:
        with self.operations:
            self._typing_target = True
            try:
                super().target_by_name(name)
            finally:
                self._typing_target = False

    def recover_view(self, deadline: float) -> None:
        raise ValueError("В API-форке камерой управляет корневой агент")

    def camera_zoom(self, steps: int, verify: Callable[[], None] = lambda: None) -> None:
        if type(steps) is not int or not 1 <= abs(steps) <= 4:
            raise ValueError("Зум камеры ограничен 1–4 шагами колеса")
        if self.transport.supports_camera_zoom is not True:
            raise ValueError("Зум камеры требует HID_PILOT_V8")
        with self.operations:
            verify()
            with self.ready():
                self.transport.release()
            self._navigation.allowed = True
            try:
                self.move(.5, .5, fast=True, verify=verify)
            finally:
                self._navigation.allowed = False
            with self.ready():
                verify()
                self.desktop.check_point(self.desktop.cursor())
                self.transport.command(f"WHEEL {steps}", "SCROLLED")

    def camera_turn(self, direction: str, verify: Callable[[], None], *, scale: float = 1, search: bool = False) -> None:
        if not math.isfinite(scale) or not .05 <= scale <= 1:
            raise ValueError("Масштаб точного поворота должен быть от 0.05 до 1")
        if self.transport.supports_fast_camera is not True:
            raise ValueError("Ускоренная камера x20 требует прошивку HID_PILOT_V7")
        if direction not in {"left", "right", "up", "down"}:
            raise ValueError("Неизвестное направление камеры")
        if search and self.transport.supports_strong_camera is not True:
            raise ValueError("Сильный рывок камеры требует HID_PILOT_V10")
        generation = self.pause_generation
        deadline = time.monotonic() + 3

        def check() -> None:
            self.check()
            if (self.manual_paused.is_set() or self.paused.is_set()
                    or self.pause_generation != generation or time.monotonic() > deadline):
                raise Stopped("Камера отменена паузой или лимитом")
            verify()

        with self.operations:
            try:
                with self.lock:
                    check()
                    self.transport.release()
                # Наведение курсора и сам поворот имеют отдельные ограниченные бюджеты.
                deadline = time.monotonic() + 1
                dx, dy = {"left": (-30, 0), "right": (30, 0),
                          "up": (0, -20), "down": (0, 20)}[direction]
                dx, dy = round(dx * scale), round(dy * scale)
                # V9 выполняет сильный рывок одной командой без пауз между COM-запросами.
                if search:
                    dx, dy = dx * 10, dy * 10
                with self.lock:
                    check()
                    x, y = self.desktop.cursor()
                    self.desktop.check_point((x, y))
                    # Медленные проверки выполняются до зажатия ПКМ: они не расходуют её deadline.
                    self.transport.command("HB", "ALIVE")
                    self._begin_hold("BUTTON", "right", 1000 if search else 300)
                    held_at = time.monotonic()
                if self.stopped.wait(.025):
                    raise Stopped("Камера остановлена")
                with self.lock:
                    self.check()
                    if (self.manual_paused.is_set() or self.paused.is_set()
                            or self.pause_generation != generation):
                        raise Stopped("Камера отменена паузой")
                    # После задержки планировщика нельзя посылать движение с истёкшей ПКМ.
                    if time.monotonic() - held_at > .1:
                        raise ValueError("Рывок отложен: истёк бюджет старта ПКМ")
                    if search:
                        self.transport.command(f"CAMERA {dx} {dy}", "MOVING")
                    else:
                        self.transport.command(f"MOVE {dx} {dy}", "MOVED")
                if search:
                    while True:
                        if self.stopped.wait(.01):
                            raise Stopped("Камера остановлена")
                        with self.lock:
                            self.check()
                            if (self.manual_paused.is_set() or self.paused.is_set()
                                    or self.pause_generation != generation
                                    or time.monotonic() - held_at > .8):
                                raise Stopped("Камера отменена паузой или лимитом")
                            self.transport.heartbeat()
                            result = self.transport.command("CAMERA_STATUS", ("MOVING", "DONE"))
                            if result == "DONE":
                                break
                if self.stopped.wait(.02):
                    raise Stopped("Камера остановлена")
                with self.lock:
                    self.check()
                    self.desktop.check_point(self.desktop.cursor())
            finally:
                with self.lock:
                    self.transport.release()
