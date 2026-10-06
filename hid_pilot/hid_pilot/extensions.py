"""
Загрузка команд из внешних расширений; ввод остаётся в ActionController.
"""

import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExtensionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: int = Field(ge=0, le=8)
    config: str
    prepare: str
    run: str
    exclusive_observer: bool = False


def extensions_dir() -> Path:
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    return root / "extensions"


def extension_commands() -> dict[str, ExtensionCommand]:
    manifest = extensions_dir() / "commands.json"
    if not manifest.exists():
        return {}
    data = json.loads(manifest.read_text("utf-8"))
    if data.get("api_version") != 1:
        raise ValueError("Неподдерживаемая версия API расширений")
    commands = data.get("commands", {})
    if not isinstance(commands, dict):
        # Это ошибка содержимого манифеста; parser показывает её как ValueError.
        raise ValueError("Неверный список команд расширений")  # noqa: TRY004
    reserved = {"set", "wait", "key", "move", "click", "find", "color", "repeat", "end", "if_found"}
    result = {}
    for name, config in commands.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in reserved:
            raise ValueError(f"Недопустимая команда расширения: {name}")
        result[name] = ExtensionCommand.model_validate(config)
    return result


def extension_symbol(reference: str) -> Any:
    if not re.fullmatch(r"hid_pilot\.[a-z][a-z0-9_]*:[A-Za-z][A-Za-z0-9_]*", reference):
        raise ValueError("Неверная ссылка на функцию расширения")
    module_name, symbol = reference.split(":")
    source = extensions_dir() / "hid_pilot" / f"{module_name.rsplit('.', 1)[-1]}.py"
    if not source.is_file():
        raise ValueError(f"Расширение не установлено: {source.name}")
    try:
        module = importlib.import_module(module_name)
        if Path(module.__file__).resolve() != source.resolve():
            raise ValueError("Модуль расширения загружен из другой папки")
        return getattr(module, symbol)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"Не удалось загрузить расширение: {reference}") from exc
