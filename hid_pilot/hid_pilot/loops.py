"""
Независимые интервалы строк при последовательном доступе к одному HID-устройству.
"""

import logging
import threading
import time
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hid_pilot.controller import ActionController
from hid_pilot.script import KEYS

logger = logging.getLogger("hid_pilot.loops")


class KeyLoop(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    row: int = Field(ge=0, le=3)
    key: str
    interval_ms: int = Field(ge=150, le=3_600_000)
    repeats: int = Field(ge=-1, le=100_000)

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if value not in KEYS | {"LMB", "RMB"}:
            raise ValueError("Клавиша не разрешена")
        return value

    @field_validator("repeats")
    @classmethod
    def valid_repeats(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Повторы: -1 или положительное число")
        return value


def run_key_loops(controller: ActionController, plans: tuple[KeyLoop, ...],
                  cancelled: dict[int, threading.Event],
                  notify: Callable[[str], None],
                  clock: Callable[[], float] = time.monotonic) -> None:
    if len({plan.row for plan in plans}) != len(plans):
        raise ValueError("Строки не должны повторяться")
    due = {plan.row: clock() for plan in plans}
    counts = dict.fromkeys(due, 0)
    deadline = clock() + 3600
    while True:
        controller.checkpoint()
        if clock() >= deadline or sum(counts.values()) >= 100_000:
            raise RuntimeError("Лимит запуска: 1 час / 100000 нажатий")
        pending = [plan for plan in plans if not cancelled[plan.row].is_set()
                   and (plan.repeats == -1 or counts[plan.row] < plan.repeats)]
        if not pending:
            return
        plan = min(pending, key=lambda item: due[item.row])
        delay = due[plan.row] - clock()
        if delay > 0:
            controller.wait(min(delay, .05))
            continue
        controller.checkpoint()
        if cancelled[plan.row].is_set():
            continue
        if plan.key in {"LMB", "RMB"}:
            controller.click_current("left" if plan.key == "LMB" else "right")
        else:
            controller.key(plan.key, 100)
        counts[plan.row] += 1
        # Пропущенные интервалы не догоняем всплеском нажатий.
        due[plan.row] = clock() + plan.interval_ms / 1000
        message = f"Строка {plan.row + 1}: {plan.key}, нажатие {counts[plan.row]}"
        logger.info(message)
        notify(message)
