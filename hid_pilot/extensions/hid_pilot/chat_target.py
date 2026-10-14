"""
Отдельный навык выбора цели по имени через чат игры.
"""

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from hid_pilot.controller import ActionController


class ChatTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


def prepare_extension(base: Path, args: tuple[str, ...]) -> None:
    name = args[0]
    if not name or len(name) > 80 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 '-" for char in name):
        raise ValueError("Для /target укажите имя до 80 символов латиницей")


def run_extension(controller: ActionController, base: Path, args: tuple[str, ...],
                  config: ChatTargetConfig, notify: Callable[[str], None], deadline: float) -> None:
    controller.target_by_name(args[0])
    notify(f"Выбор цели через /target: {args[0]}")
