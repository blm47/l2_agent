"""
Ручной запуск smoke: три коротких R через GUI в уже открытом LU4.
"""

import ctypes
import json
import logging
import time
from pathlib import Path

import cv2
import win32api
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hid_pilot.__main__ import MainWindow
from hid_pilot.desktop import Desktop
from hid_pilot.logging_setup import setup_logging


def main() -> int:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    setup_logging()
    output = Path("../artifacts/hid-pilot/quick-smoke")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    window = MainWindow()
    target = window.targets.currentData()
    if target is None or not target.title.lower().startswith("lu4"):
        window.close()
        raise RuntimeError("LU4 не выбран; smoke отменён")
    events = []
    edges = []
    last_down = [False]
    started = time.monotonic()
    window.events.status.connect(events.append)
    window.quick.rows[0][2].setValue(800)
    window.quick.rows[0][3].setValue(3)
    window.show()

    def begin() -> None:
        window.grab().save(str(output / "gui.png"))
        window.quick.start_selected()

    def sample() -> None:
        down = bool(win32api.GetAsyncKeyState(ord("R")) & 0x8000)
        if down != last_down[0]:
            edges.append({"time": time.monotonic() - started, "down": down})
            last_down[0] = down

    def finish() -> None:
        try:
            cv2.imwrite(str(output / "game-after.png"), Desktop(target).capture())
        except Exception as exc:
            logging.getLogger("hid_pilot.smoke").exception("Не удалось сохранить игровой кадр")
            events.append(str(exc))
        window.close()
        report = {
            "target": target.model_dump(), "events": events, "r_edges": edges,
            "r_released": not bool(win32api.GetAsyncKeyState(ord("R")) & 0x8000),
            "completed": any("завершён" in event for event in events),
        }
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
        app.quit()

    timer = QTimer()
    timer.timeout.connect(sample)
    timer.start(10)
    QTimer.singleShot(500, begin)
    QTimer.singleShot(8000, finish)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
