"""
Настройка и фоновый просмотр пати без отправки игрового ввода.
"""

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hid_pilot.desktop import Desktop, Target
from hid_pilot.numbers import shared_numbers
from hid_pilot.party import PartyProfile, read_party
from hid_pilot.party_statistics import format_statistics

logger = logging.getLogger("hid_pilot.party")


class PartyPanel(QWidget):
    result = Signal(object)
    setup_requested = Signal()
    manual_requested = Signal()
    updated = Signal(str)

    def __init__(self, target: Callable[[], Target | None], path: Callable[[], Path | None],
                 running: Callable[[], bool]) -> None:
        super().__init__()
        self.target = target
        self.path = path
        self.running = running
        self.busy = False
        self.worker: threading.Thread | None = None
        self.generation = 0
        self.last_result = 0.
        layout = QVBoxLayout(self)
        setup = QPushButton("Автопоиск HP / MP / CP / цели / пати…")
        setup.clicked.connect(self.setup_requested.emit)
        layout.addWidget(setup)
        manual = QPushButton("Ручная корректировка пати…")
        manual.clicked.connect(self.manual_requested.emit)
        layout.addWidget(manual)
        self.enabled = QCheckBox("Наблюдать пати (OCR имён, CP/HP/MP по полосам)")
        self.enabled.toggled.connect(self.reset)
        layout.addWidget(self.enabled)
        self.status = QLabel("Сохраните сценарий, выполните общий автопоиск и включите наблюдение")
        self.status.setWordWrap(True)
        self.status.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(self.status)
        layout.addStretch()
        self.result.connect(self.receive)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(1000)

    def reset(self) -> None:
        self.generation += 1
        self.last_result = 0
        self.status.setText("Ожидание свежего кадра" if self.enabled.isChecked() else "Наблюдение выключено")
        self.updated.emit(self.status.text())

    def profile_path(self) -> Path:
        path = self.path()
        if path is None:
            raise ValueError("Сначала сохраните сценарий")
        return path.parent / "party.json"

    def poll(self) -> None:
        if not self.enabled.isChecked():
            return
        if time.monotonic() - self.last_result > 3:
            self.status.setText("Пати: нет свежего наблюдения")
        if self.busy:
            return
        try:
            target, path = self.target(), self.profile_path()
            if not isinstance(target, Target):
                raise TypeError("Выберите окно игры")
            profile = PartyProfile.model_validate_json(path.read_text("utf-8"))
            Desktop(target).check()
        except Exception as exc:
            logger.debug("Пати недоступна", exc_info=True)
            self.status.setText(f"Пати: неизвестно — {exc}")
            return
        self.busy = True
        generation = self.generation

        def work() -> None:
            payload = {"generation": generation, "target": target, "path": path,
                       "started": time.monotonic()}
            try:
                desktop = Desktop(target)
                image = desktop.capture()
                payload["text"] = format_statistics({}, {}, read_party(image, profile, shared_numbers()),
                                                    {}, "Наблюдение пати")
                desktop.check()
            except Exception as exc:
                logger.debug("Наблюдение пати недоступно", exc_info=True)
                payload["text"] = f"Пати: неизвестно — {exc}"
            self.result.emit(payload)

        self.worker = threading.Thread(target=work, name="PartyOCR", daemon=True)
        self.worker.start()

    def receive(self, payload: dict) -> None:
        self.busy = False
        if payload["generation"] != self.generation or not self.enabled.isChecked():
            return
        if self.path() is None or payload["target"] != self.target() or payload["path"] != self.profile_path():
            self.reset()
            return
        if time.monotonic() - payload["started"] > 3:
            self.status.setText("Пати: OCR устарел, ожидается следующий кадр")
            return
        self.last_result = payload["started"]
        self.status.setText(payload["text"])
        self.updated.emit(payload["text"])

    def shutdown(self) -> bool:
        self.timer.stop()
        self.enabled.setChecked(False)
        if self.worker is not None:
            self.worker.join(timeout=2)
            return not self.worker.is_alive()
        return True
