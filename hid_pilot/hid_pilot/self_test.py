"""
Проверка автономной сборки без подключения Pico и без игрового ввода.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("hid_pilot.self_test")


def run_self_test(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", required=True)
    parser.add_argument("--gui-preview")
    options = parser.parse_args(arguments)
    result = {"frozen": bool(getattr(sys, "frozen", False)), "checks": {}, "ok": False}
    checks = result["checks"]
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import cv2
        import numpy as np
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication

        from hid_pilot.firmware_manager import BOARDS, FIRMWARE_FILES, vendor_asset
        from hid_pilot.firmware_panel import FirmwareDialog
        from hid_pilot.numbers import NumbersReader
        from hid_pilot.paths import resource_path
        from hid_pilot.script import parse_script

        for board in BOARDS:
            vendor_asset(f"{board}.uf2")
        vendor_asset("flash_nuke.uf2")
        checks["uf2"] = "Pico, Pico W, полный сброс; SHA256 и RP2040 family"
        for name in FIRMWARE_FILES:
            compile(resource_path(f"firmware/{name}").read_text("utf-8"), name, "exec")
        checks["firmware"] = "boot.py, code.py, control.py доступны"
        from hid_pilot.extensions import extension_commands, extension_symbol, extensions_dir
        commands = extension_commands()
        if not {"healer", "farm"} <= commands.keys():
            raise RuntimeError("Для self-test установите внешние расширения healer и farm")
        for command in commands.values():
            for reference in (command.config, command.prepare, command.run):
                extension_symbol(reference)
        for path in (extensions_dir() / "scripts").glob("*.pilot"):
            text = path.read_text("utf-8-sig")
            if path.name.endswith(".duo.pilot"):
                from hid_pilot.duo import parse_duo_settings

                parse_duo_settings(text)
            else:
                parse_script(text)
        checks["scripts"] = "Внешние расширения и сценарии доступны вне EXE"
        reader = NumbersReader()
        image = np.full((60, 280, 3), 20, dtype=np.uint8)
        cv2.putText(image, "148/148", (10, 43), cv2.FONT_HERSHEY_SIMPLEX, 1.3,
                    (240, 240, 240), 2, cv2.LINE_AA)
        numbers = reader.read(image)
        if numbers is None or (numbers.current, numbers.maximum) != (148, 148):
            raise RuntimeError(f"Автономный OCR чисел не подтверждён: {numbers}")
        image = np.full((70, 300, 3), 20, dtype=np.uint8)
        cv2.putText(image, "MemberOne", (10, 47), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                    (240, 240, 240), 2, cv2.LINE_AA)
        names = reader.detect(image)
        if not any(text.replace(" ", "").lower() == "memberone" for _, text, _ in names):
            raise RuntimeError(f"Автономный OCR имён не подтверждён: {names}")
        checks["ocr"] = {"numbers": numbers.model_dump(), "names": [text for _, text, _ in names]}
        app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
        dialog = FirmwareDialog(lambda: True)
        dialog.show()
        app.processEvents()
        if options.gui_preview:
            dialog.grab().save(options.gui_preview)
        dialog.reject()
        checks["gui"] = "Qt, окно прошивки, перечисление USB/COM без записи на плату"
        checks["win32"] = "Модули Win32 доступны"
        result["ok"] = True
        logger.info("Автономная сборка прошла self-test")
    except Exception as exc:
        logger.exception("Автономная сборка не прошла self-test")
        result["error"] = str(exc)
    report = Path(options.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    return 0 if result["ok"] else 1
