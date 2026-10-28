"""
Два одинаковых интерфейса наблюдения и отдельный выбор общего сценария.
"""

from pathlib import Path
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from hid_pilot.desktop import Target, snapshot
from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from hid_pilot.party_panel import PartyPanel
from hid_pilot.farm_settings import FarmSettings


class DuoPanel(QScrollArea):
    setup_requested = Signal(object, bool, bool)

    def __init__(self, farmer: Callable[[], Target | None], running: Callable[[], bool],
                 notify: Callable[[str], None]) -> None:
        super().__init__()
        self.path: Path | None = None
        self.farmer = farmer
        self.notify = notify
        self.running = running
        container = QWidget()
        self.setWidgetResizable(True)
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.addWidget(QLabel(
            "Основное окно — МА, Мейн Ассистер (выбор сверху). Второе — саппорт.\n"
            "Один Pico, общие START / PAUSE / STOP. Профили каждого окна хранятся отдельно."
        ))
        self.targets = QComboBox()
        self.targets.setPlaceholderText("Выберите второе окно")
        layout.addWidget(self.targets)
        self.capture_button = QPushButton("Захватить второе окно")
        self.capture_button.clicked.connect(self.capture_second)
        layout.addWidget(self.capture_button)
        self.captured: Target | None = None
        self.target_status = QLabel("Второе окно не захвачено")
        layout.addWidget(self.target_status)
        self.live_status = QLabel("МА: HP — | цель —")
        layout.addWidget(self.live_status)
        self.roles = QTabWidget()
        self.panels: dict[str, PartyPanel] = {}
        for role, title, target in (("farmer", "Окно 1 — МА", farmer),
                                    ("support", "Окно 2 — саппорт", lambda: self.captured)):
            panel = PartyPanel(target, lambda role=role: self.role_path(role), running)
            panel.status.hide()
            panel.enabled.hide()
            panel.updated.connect(lambda text, panel=panel: self.show_preview_statistics(panel, text))
            panel.setup_requested.connect(
                lambda role=role: self.setup_requested.emit(role, False, False))
            panel.manual_requested.connect(
                lambda role=role: self.setup_requested.emit(role, True, False))
            panel.own_requested.connect(
                lambda role=role: self.setup_requested.emit(role, False, True))
            self.panels[role] = panel
            self.roles.addTab(panel, title)
        self.roles.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout.addWidget(self.roles)
        row = QHBoxLayout()
        self.script_buttons: list[QPushButton] = []
        for title, callback in (("Открыть сценарий для двух окон…", self.open_script),
                                 ("Сохранить сценарий для двух окон…", self.save_script)):
            button = QPushButton(title)
            self.script_buttons.append(button)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.script_status = QLabel("Сохраните отдельный сценарий *.duo.pilot перед настройкой областей")
        layout.addWidget(self.script_status)
        self.editor = QPlainTextEdit(format_duo_settings(DuoConfig()))
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setMinimumHeight(150)
        self.settings = FarmSettings(self.editor, duo=True)
        self.settings_tabs = QTabWidget()
        self.settings_tabs.addTab(self.settings, "Настройки по группам")
        self.settings_tabs.addTab(self.editor, "Текст сценария")
        self.statistics = QPlainTextEdit("Ожидание наблюдения дуо-фарма")
        self.statistics.setReadOnly(True)
        self.statistics.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.statistics.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.statistics.setMinimumWidth(230)
        self.details = QSplitter(Qt.Orientation.Horizontal)
        self.details.addWidget(self.settings_tabs)
        self.details.addWidget(self.statistics)
        self.details.setStretchFactor(0, 2)
        self.details.setStretchFactor(1, 3)
        layout.addWidget(self.details, 1)

    def show_statistics(self, payload: dict) -> None:
        self.statistics.setPlainText(payload["text"])

    def show_preview_statistics(self, panel: PartyPanel, text: str) -> None:
        if not self.running() and self.roles.currentWidget() is panel:
            self.statistics.setPlainText(text)

    def role_path(self, role: str) -> Path | None:
        if self.path is None:
            return None
        return self.path.parent / self.path.stem / role / "scenario.pilot"

    def base(self) -> Path:
        if self.path is None:
            raise ValueError("Сначала сохраните сценарий для двух окон")
        return self.path.parent / self.path.stem

    def capture_second(self) -> None:
        target = self.targets.currentData()
        if not isinstance(target, Target):
            self.notify("Выберите второе окно")
            return
        first = self.farmer()
        if isinstance(first, Target) and first.hwnd == target.hwnd:
            self.notify("Второе окно должно отличаться от окна МА")
            return
        try:
            current = snapshot(target.hwnd)
            if current.pid != target.pid:
                raise ValueError("Второе окно сменило процесс; обновите список")
        except Exception as exc:
            self.notify(str(exc))
            return
        self.captured = current
        self.panels["support"].enabled.setChecked(False)
        self.panels["support"].reset()
        self.target_status.setText(f"Захвачено: {current.title} [PID {current.pid}]")

    def set_windows(self, targets: list[Target]) -> None:
        previous = self.targets.currentData()
        self.targets.clear()
        for target in targets:
            self.targets.addItem(f"{target.title} [PID {target.pid}]", target)
            if isinstance(previous, Target) and (target.hwnd, target.pid) == (previous.hwnd, previous.pid):
                self.targets.setCurrentIndex(self.targets.count() - 1)

    def config(self) -> DuoConfig:
        return parse_duo_settings(self.editor.toPlainText())

    def open_script(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Сценарий для двух окон", "",
                                             "Сценарии (*.duo.pilot *.duo.json *.pilot)")
        if not path:
            return
        try:
            source = Path(path)
            if source.stat().st_size > 100_000:
                raise ValueError("Сценарий слишком большой")
            text = source.read_text("utf-8-sig")
            config = parse_duo_settings(text)
            self.select_script(source, config, text if not text.lstrip().startswith("{") else None)
        except (OSError, ValueError) as exc:
            self.notify(str(exc))

    def select_script(self, path: Path, config: DuoConfig, text: str | None = None) -> None:
        for panel in self.panels.values():
            panel.enabled.setChecked(False)
            panel.reset()
        self.path = path
        self.editor.setPlainText(text if text is not None else format_duo_settings(config))
        self.script_status.setText(str(path))

    def save_script(self) -> None:
        try:
            config = self.config()
            suggested = self.path.with_suffix(".pilot") if self.path else Path("ma-support.duo.pilot")
            # Native-диалог Windows должен добавлять только последнее расширение.
            path, selected_filter = QFileDialog.getSaveFileName(
                self, "Сценарий для двух окон", str(suggested),
                "Сценарии (*.pilot);;JSON (*.json)")
            if path:
                target = Path(path)
                if target.suffix.lower() not in {".pilot", ".json"}:
                    suffix = ".json" if selected_filter == "JSON (*.json)" else ".pilot"
                    target = target.with_name(target.name + suffix)
                text = self.editor.toPlainText()
                if target.suffix.lower() == ".json":
                    target.write_text(config.model_dump_json(indent=2), encoding="utf-8")
                    self.select_script(target, config)
                else:
                    if text.lstrip().startswith("{"):
                        text = format_duo_settings(config)
                    target.write_text(text, encoding="utf-8")
                    self.select_script(target, config, text)
        except (OSError, ValueError) as exc:
            self.notify(str(exc))

    def set_running(self, running: bool) -> None:
        # Во время фарма настройка областей скрыта, место занимает живой мониторинг.
        self.roles.setVisible(not running)
        self.targets.setEnabled(not running)
        self.capture_button.setEnabled(not running)
        self.editor.setReadOnly(running)
        for button in self.script_buttons:
            button.setEnabled(not running)
        for field in self.settings.fields.values():
            field.setEnabled(not running)
        self.settings.expand_button.setEnabled(not running)
        for panel in self.panels.values():
            panel.enabled.setEnabled(not running)
            for button in panel.findChildren(QPushButton):
                button.setEnabled(not running)

    def show_observation(self, payload: dict) -> None:
        hp = payload["hp"]
        target = payload["target_hp"]
        target_text = f"{target:.0%}" if target is not None else "неизвестно"
        support_hp = payload.get("support_hp")
        support_text = f"{support_hp:.0%}" if support_hp is not None else "—"
        pending = " | Баффы ожидают" if payload.get("buffs_pending") else ""
        support_mp = payload.get("support_mp")
        mp_text = f"{support_mp:.0%}" if support_mp is not None else "—"
        if payload.get("support_buff_active"):
            pending = " | Саппорт: макрос баффа выполняется"
        summon_hp, summon_mp = payload.get("summon_hp"), payload.get("summon_mp")
        summon_text = (f" | Самон: HP {f'{summon_hp:.0%}' if summon_hp is not None else '—'}, "
                       f"MP {f'{summon_mp:.0%}' if summon_mp is not None else '—'}")
        self.live_status.setText(
            f"МА: HP {hp:.0%} | Саппорт: HP {support_text}, MP {mp_text}{summon_text} | HP цели {target_text}{pending}")

    def shutdown(self) -> bool:
        return all([panel.shutdown() for panel in self.panels.values()])
