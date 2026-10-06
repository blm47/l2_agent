"""
Ресурсы сборки и доступные для записи пользовательские файлы.
"""

import os
import sys
from pathlib import Path


def resource_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Ресурс выходит за границы приложения")
    return path


def user_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "HIDPilot"


def example_scripts_dir() -> Path:
    destination = user_data_dir() / "scripts"
    destination.mkdir(parents=True, exist_ok=True)
    from hid_pilot.extensions import extensions_dir
    for source in (extensions_dir() / "scripts").glob("*.pilot"):
        target = destination / source.name
        if not target.exists():
            target.write_bytes(source.read_bytes())
    return destination
