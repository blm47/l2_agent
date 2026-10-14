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

from hid_pilot.bars import BarProfile
from hid_pilot.desktop import Desktop, Target
from hid_pilot.mob_statistics import MobStatistics
from hid_pilot.numbers import shared_numbers
from hid_pilot.party import PartyProfile, read_party
from hid_pilot.party_statistics import format_statistics
from hid_pilot.target import TargetReader

logger = logging.getLogger("hid_pilot.party")


class PartyPanel(QWidget):
    result = Signal(object)
    setup_requested = Signal()
    manual_requested = Signal()
    own_requested = Signal()
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
        self.target_reader: TargetReader | None = None
        self.mob_statistics = MobStatistics(time.monotonic())
        layout = QVBoxLayout(self)
        setup = QPushButton("Автопоиск HP / MP / CP / цели / пати…")
        setup.clicked.connect(self.setup_requested.emit)
        layout.addWidget(setup)
        manual = QPushButton("Ручная корректировка пати…")
        manual.clicked.connect(self.manual_requested.emit)
        layout.addWidget(manual)
        own = QPushButton("Вручную указать свои статы и уровни…")
        own.clicked.connect(self.own_requested.emit)
        layout.addWidget(own)
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
        self.target_reader = None
        self.mob_statistics = MobStatistics(time.monotonic())
        self.status.setText("Ожидание свежего кадра" if self.enabled.isChecked() else "Наблюдение выключено")
        self.updated.emit(self.status.text())

    def profile_path(self) -> Path:
        path = self.path()
        if path is None:
            raise ValueError("Сначала сохраните сценарий")
        return path.parent / "party.json"

    def poll(self) -> None:
        if not self.enabled.isChecked() or self.running():
            return
        if time.monotonic() - self.last_result > 3:
            self.status.setText("Пати: нет свежего наблюдения")
        if self.busy:
            return
        try:
            target, path = self.target(), self.profile_path()
            if not isinstance(target, Target):
                raise TypeError("Выберите окно игры")
            profile = PartyProfile.model_validate_json(path.read_text("utf-8")) if path.exists() else None
            if profile is None and not (path.parent / "farm-bars.json").exists():
                raise ValueError("Сначала настройте области статов")
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
                selected = {}
                own_values = {}
                own_path = path.parent / "farm-bars.json"
                if own_path.exists():
                    own = BarProfile.model_validate_json(own_path.read_text("utf-8"))
                    if self.target_reader is None or self.target_reader.profile != own:
                        self.target_reader = TargetReader(own)
                    observation = self.target_reader.read(image, payload["started"])
                    payload["observation"] = observation
                    selected = {"name": observation.name, "class": observation.player_class,
                                "clan": observation.clan, "type": observation.kind,
                                "hp": observation.hp, "dead": observation.dead, "level": observation.level}
                    own_values = {name: own.read(image, name) if name in own.bars.keys() | own.numbers.keys() else None
                                  for name in ("hp", "mp", "cp")}
                    own_values["level"] = own.read_level(image, "own_level")
                payload["text"] = format_statistics(own_values, selected,
                                                    read_party(image, profile, shared_numbers()) if profile else [],
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
        if "observation" in payload:
            self.mob_statistics.observe(payload["observation"], time.monotonic())
            self.mob_statistics.report(time.monotonic())
            payload["text"] += "\n" + self.mob_statistics.summary(time.monotonic())
        self.status.setText(payload["text"])
        self.updated.emit(payload["text"])

    def shutdown(self) -> bool:
        self.timer.stop()
        self.enabled.setChecked(False)
        if self.worker is not None:
            self.worker.join(timeout=2)
            return not self.worker.is_alive()
        return True
