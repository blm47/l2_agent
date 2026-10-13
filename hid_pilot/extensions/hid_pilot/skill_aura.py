"""
Управление аурой вокруг скиллов без прямого HID-ввода.
"""

import logging
from collections.abc import Callable

from hid_pilot.controller import ActionController

logger = logging.getLogger("hid_pilot.healer.aura")


class SkillAura:
    def __init__(self, controller: ActionController, key: str, notify: Callable[[str], None],
                 cast_seconds: float = .3) -> None:
        self.controller = controller
        self.key = key
        self.notify = notify
        self.cast_seconds = cast_seconds
        self.on = False
        self.off_at: float | None = None

    def before(self) -> None:
        if not self.on:
            self.controller.key(self.key, 100)
            self.on = True
            self.off_at = None
            # После переключения F8 ждём каст перед следующим игровым действием.
            self.controller.wait(self.cast_seconds)
            self.event("Аура включена перед скиллом")

    def after(self, until: float, replace: bool = False) -> None:
        self.off_at = until if replace or self.off_at is None else max(self.off_at, until)

    def tick(self, now: float) -> None:
        if self.on and self.off_at is not None and now >= self.off_at:
            self.controller.key(self.key, 100)
            self.on = False
            self.off_at = None
            self.controller.wait(self.cast_seconds)
            self.event("Аура выключена после скилла")

    def event(self, message: str) -> None:
        logger.info("%s: %s", self.key, message)
        self.notify(f"{self.key}: {message}")
