import ctypes
import logging
import os
import sys
import threading
import time
from concurrent.futures import Future
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QFontDatabase, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from hid_pilot.controller import ActionController, Stopped
from hid_pilot.duo import DuoFarmer, SupportDied, prepare_duo, load_duo_party
from hid_pilot.duo_panel import DuoPanel
from hid_pilot.farm_settings import FarmSettings
from hid_pilot.desktop import Desktop, Target, activate, snapshot, windows
from hid_pilot.hard_stop import HardStopHotkey
from hid_pilot.logging_setup import setup_logging
from hid_pilot.chat_preview import latest_chat_preview
from hid_pilot.telegram_notifications import PlayerDied, TelegramNotifier, ScriptStopped
from hid_pilot.loops import KeyLoop, run_key_loops
from hid_pilot.party_panel import PartyPanel
from hid_pilot.party_statistics import format_statistics
from hid_pilot.paths import example_scripts_dir, user_data_dir
from hid_pilot.quick_panel import QuickPanel
from hid_pilot.replay import FarmReplay
from hid_pilot.runner import Runner
from hid_pilot.script import parse_script, script_variables
from hid_pilot.session import match_session_window
from hid_pilot.support_life import SupportLifeObserver
from hid_pilot.duo import parse_duo_settings
from hid_pilot.transport import PicoTransport

logger = logging.getLogger("hid_pilot.gui")


class Events(QObject):
    status = Signal(str)
    replay_status = Signal(str)
    finished = Signal()
    auto_found = Signal(object)
    observation = Signal(object)
    duo_observation = Signal(object)
    duo_statistics = Signal(object)


class ScriptNotifications:
    def __init__(self, events: Events, telegram: TelegramNotifier | None = None,
                 script: str = "", window: str = "", duo_mode: bool = False,
                 replay: FarmReplay | None = None, support_window: str = "") -> None:
        self.events = events
        self.telegram = telegram
        self.script = script
        self.window = window
        self.dead = False
        self.duo_mode = duo_mode
        self.replay = replay
        self.support_window = support_window
        self.support_dead = False

    def __call__(self, text: str) -> None:
        self.events.status.emit(text)

    def state(self, payload: object) -> None:
        if self.duo_mode:
            self.events.duo_statistics.emit(payload)
        else:
            self.events.observation.emit(payload)

    def death(self) -> None:
        if self.dead:
            return
        self.dead = True
        reason = "Смерть МА" if self.duo_mode else "Смерть персонажа"
        self._notify_death(PlayerDied(self.script, self.window, "МА" if self.duo_mode else ""), reason)

    def _notify_death(self, event: PlayerDied, reason: str) -> None:
        video = Future() if self.replay is not None and self.telegram is not None else None
        if self.telegram is not None:
            if video is None:
                self.telegram.notify_death(event)
            else:
                self.telegram.notify_death(event, video=video)
        if self.replay is not None:
            if video is None:
                self.replay.trigger(reason, death=True)
            else:
                self.replay.trigger(reason, death=True, completion=video)

    def revived(self) -> None:
        self.dead = False

    def support_death(self) -> None:
        if self.support_dead:
            return
        self.support_dead = True
        self.events.status.emit("Саппорт умер: подтверждены HP=0 или кнопка To Village")
        self._notify_death(PlayerDied(self.script, self.support_window, "Саппорт"), "Смерть саппорта")

    def support_revived(self) -> None:
        self.support_dead = False


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HID Pilot — текстовые сценарии / Pico")
        available = self.screen().availableGeometry()
        self.resize(min(1180, available.width()), min(1300, available.height() - 40))
        self.layout_settings = QSettings(str(user_data_dir() / "window-layout.ini"), QSettings.Format.IniFormat)
        self.layout_restored = False
        self.path: Path | None = None
        self.controller: ActionController | None = None
        self.worker: threading.Thread | None = None
        self.cancel = threading.Event()
        self.silent_button_stop = threading.Event()
        self.telegram = TelegramNotifier(user_data_dir() / "telegram.toml")
        self.auto_busy = False
        self.auto_generation = 0
        self.firmware_open = False
        self.events = Events()
        self.events.status.connect(self.show_status)
        self.events.replay_status.connect(self.show_replay_status)
        self.replay = FarmReplay(user_data_dir() / "obs-replay.toml", self.events.replay_status.emit)
        self.events.finished.connect(self.finished)
        self.events.auto_found.connect(self.review_auto_numbers)
        self.events.observation.connect(self.show_observation)
        self.hotkey = HardStopHotkey(self.stop)
        body = QWidget()
        layout = QVBoxLayout(body)
        self.targets = QComboBox()
        layout.addWidget(self.targets)
        refresh = QPushButton("Обновить окна")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        self.port = QLineEdit("COM4")
        self.port.setPlaceholderText("CDC data port Pico")
        layout.addWidget(self.port)
        layout.addWidget(QLabel(
            "Старт активирует выбранное окно; ввод начнётся через 1 секунду.\n"
            "Hard-stop: \\ | Пауза — кнопкой мыши | Автопауза при потере фокуса."
        ))
        self.tabs = QTabWidget()
        self.quick = QuickPanel()
        self.quick.requested.connect(self.start_loops)
        self.tabs.addTab(self.quick, "Общее")
        script_page = QWidget()
        script_layout = QVBoxLayout(script_page)
        self.tabs.addTab(script_page, "Скрипт")
        self.party = PartyPanel(self.targets.currentData, lambda: self.path,
                                lambda: self.auto_busy or (self.worker is not None and self.worker.is_alive()))
        self.tabs.addTab(self.party, "Пати")
        self.duo = DuoPanel(self.targets.currentData,
                            lambda: self.auto_busy or (self.worker is not None and self.worker.is_alive()),
                            self.show_status)
        self.duo.setup_requested.connect(self.setup_duo)
        for panel in self.duo.panels.values():
            panel.summon_requested.connect(lambda panel=panel: self.auto_numbers(panel=panel, manual_summon=True))
        self.events.duo_observation.connect(self.duo.show_observation)
        self.events.duo_statistics.connect(self.duo.show_statistics)
        self.tabs.addTab(self.duo, "Дуо: МА + саппорт")
        layout.addWidget(self.tabs, 1)
        self.editor = QPlainTextEdit("# Безопасный первый сценарий без ввода\nwait 1\n")
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setMinimumHeight(300)
        self.party_state = QPlainTextEdit(format_statistics({}, {}, [], {}, "Ожидание распознавания пати"))
        self.party_state.setReadOnly(True)
        self.party_state.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.party_state.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.party_state.setMinimumWidth(230)
        self.script_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.farm_settings = FarmSettings(self.editor)
        self.script_settings_tabs = QTabWidget()
        self.script_settings_tabs.addTab(self.editor, "Текст сценария")
        self.script_settings_tabs.addTab(self.farm_settings, "Настройки мили-фарма")
        self.script_splitter.addWidget(self.script_settings_tabs)
        self.script_splitter.addWidget(self.party_state)
        self.script_splitter.setStretchFactor(0, 2)
        self.script_splitter.setStretchFactor(1, 3)
        script_layout.addWidget(self.script_splitter, 1)
        self.party.updated.connect(self.party_state.setPlainText)
        row = QHBoxLayout()
        for label, callback in [("Открыть", self.open_script), ("Сохранить", self.save_script),
                                ("Проверить", self.validate),
                                ("Алгоритм farm…", self.show_farm_code)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        script_layout.addLayout(row)
        tools_row = QHBoxLayout()
        color = QPushButton("Цвет в точке")
        color.clicked.connect(lambda: self.pick_screen())
        tools_row.addWidget(color)
        script_layout.addLayout(tools_row)
        auto_row = QHBoxLayout()
        self.auto_search = QPushButton("Автопоиск HP / MP / CP / цели / пати…")
        self.auto_search.clicked.connect(self.auto_numbers)
        self.party.setup_requested.connect(self.auto_numbers)
        self.party.manual_requested.connect(lambda: self.auto_numbers(manual_party=True))
        self.party.own_requested.connect(lambda: self.auto_numbers(manual_stats=True))
        self.party.summon_requested.connect(lambda: self.auto_numbers(manual_summon=True))
        self.auto_scope = QComboBox()
        self.auto_scope.addItem("Верхняя часть окна", True)
        self.auto_scope.addItem("Всё окно", False)
        self.log_level = QComboBox()
        self.log_level.addItems(["INFO", "DEBUG"])
        self.log_level.currentTextChanged.connect(self.change_log_level)
        auto_row.addWidget(self.auto_search)
        auto_row.addWidget(self.auto_scope)
        auto_row.addWidget(self.log_level)
        script_layout.addLayout(auto_row)
        row = QHBoxLayout()
        self.start_button = QPushButton("START")
        self.start_button.clicked.connect(self.start)
        row.addWidget(self.start_button)
        self.record_video = QCheckBox("Вести запись")
        self.record_video.setChecked(self.layout_settings.value("record_video", False, type=bool))
        self.record_video.toggled.connect(lambda checked: self.layout_settings.setValue("record_video", checked))
        row.addWidget(self.record_video)
        self.debug_start_button = QPushButton("ДебагСтарт")
        self.debug_start_button.setToolTip("Запуск с сохранением кадров ошибок цели, уровней и HP/MP")
        self.debug_start_button.clicked.connect(lambda: self.start(debug=True))
        row.addWidget(self.debug_start_button)
        pause = QPushButton("PAUSE / RESUME")
        pause.clicked.connect(self.toggle_pause)
        row.addWidget(pause)
        stop = QPushButton("STOP")
        stop.clicked.connect(self.stop_from_button)
        row.addWidget(stop)
        layout.addLayout(row)
        self.replay_status = QLabel("Запись выключена")
        self.replay_status.setWordWrap(True)
        layout.addWidget(self.replay_status)
        self.status = QLabel("Готово" if self.hotkey.available else self.hotkey.error)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.journal = QPlainTextEdit()
        self.journal.setReadOnly(True)
        self.journal.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.journal.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.journal.setMaximumBlockCount(500)
        self.journal.setMinimumHeight(60)
        self.system_chat_view = QPlainTextEdit("Распознавание системного чата временно отключено")
        self.system_chat_view.setReadOnly(True)
        self.system_chat_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.system_chat_view.setMaximumBlockCount(100)
        self.system_chat_view.setMinimumWidth(120)
        self.system_chat_view.setMinimumHeight(60)
        self.logs_row = QSplitter(Qt.Orientation.Horizontal)
        self.logs_row.setChildrenCollapsible(False)
        self.logs_row.addWidget(self.journal)
        self.logs_row.addWidget(self.system_chat_view)
        self.logs_row.setStretchFactor(0, 1)
        self.logs_row.setStretchFactor(1, 1)
        self.logs_row.handle(1).setToolTip("Потяните, чтобы изменить ширину журналов")
        self.chat_timer = QTimer(self)
        self.chat_timer.timeout.connect(self.refresh_system_chat)
        self.chat_timer.start(500)
        self.log_splitter = QSplitter(Qt.Orientation.Vertical)
        self.log_splitter.setChildrenCollapsible(False)
        self.log_splitter.addWidget(body)
        self.log_splitter.addWidget(self.logs_row)
        self.log_splitter.setStretchFactor(0, 2)
        self.log_splitter.setStretchFactor(1, 1)
        self.log_splitter.handle(1).setToolTip("Потяните, чтобы изменить высоту журнала")
        self.setCentralWidget(self.log_splitter)
        file_menu = self.menuBar().addMenu("Файл")
        file_menu.addAction("Открыть папку примеров скриптов", self.open_examples)
        file_menu.addAction("Открыть папку расширений", self.open_extensions)
        file_menu.addAction("Открыть логи и файлы приложения", self.open_data)
        file_menu.addAction("Открыть конфиг Telegram", self.open_telegram_config)
        pico_menu = self.menuBar().addMenu("Pico контроллер")
        pico_menu.addAction("Прошивка / сброс / проверка…", self.open_firmware)
        self.refresh()
        tab = self.layout_settings.value("tab", 1, type=int)
        self.tabs.setCurrentIndex(tab if 0 <= tab < self.tabs.count() else 1)
        self.port.setText(self.layout_settings.value("port", "COM4", type=str))
        self.auto_scope.setCurrentIndex(0 if self.layout_settings.value("upper_scope", True, type=bool) else 1)
        level = self.layout_settings.value("log_level", "INFO", type=str)
        self.log_level.setCurrentText(level if level in {"INFO", "DEBUG"} else "INFO")
        geometry = self.layout_settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self.restore_session()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self.layout_restored:
            return
        self.layout_restored = True
        # Пропорции скриншота применяем после расчёта размеров видимых панелей.
        for name, splitter, weights in (
                ("script", self.script_splitter, (2, 3)),
                ("duo_details", self.duo.details, (2, 3)),
                ("logs_row", self.logs_row, (1, 1)),
                ("journal", self.log_splitter, (2, 1))):
            saved = self.layout_settings.value(name)
            if saved is None or not splitter.restoreState(saved):
                extent = splitter.width() if splitter.orientation() == Qt.Orientation.Horizontal else splitter.height()
                splitter.setSizes([extent * weight // sum(weights) for weight in weights])

    def save_layout(self) -> None:
        for key, path in (("solo_script", self.path), ("duo_script", self.duo.path)):
            if path is not None:
                self.layout_settings.setValue(f"session/{key}", str(path.resolve()))
        for role, target in (("farmer", self.targets.currentData()), ("support", self.duo.captured)):
            if isinstance(target, Target):
                self.layout_settings.setValue(f"session/{role}", target.model_dump_json())
        self.layout_settings.setValue("duo_details", self.duo.details.saveState())
        self.layout_settings.setValue("geometry", self.saveGeometry())
        self.layout_settings.setValue("tab", self.tabs.currentIndex())
        self.layout_settings.setValue("port", self.port.text())
        self.layout_settings.setValue("upper_scope", self.auto_scope.currentData())
        self.layout_settings.setValue("log_level", self.log_level.currentText())
        self.layout_settings.setValue("script", self.script_splitter.saveState())
        self.layout_settings.setValue("journal", self.log_splitter.saveState())
        self.layout_settings.setValue("logs_row", self.logs_row.saveState())
        self.layout_settings.sync()
        if self.layout_settings.status() != QSettings.Status.NoError:
            logger.warning("Не удалось сохранить размеры окна и панелей")

    def restore_session(self) -> None:
        for kind in ("solo", "duo"):
            value = self.layout_settings.value(f"session/{kind}_script", "", type=str)
            if not value:
                continue
            try:
                path = Path(value)
                if path.stat().st_size > 100_000:
                    raise ValueError("Сценарий слишком большой")
                text = path.read_text("utf-8-sig")
                if kind == "duo":
                    config = parse_duo_settings(text)
                    self.duo.select_script(path, config, text if not text.lstrip().startswith("{") else None)
                else:
                    self.editor.setPlainText(text)
                    self.path = path
            except (OSError, ValueError) as exc:
                self.show_status(f"Не удалось восстановить сценарий {value}: {exc}")
        available = [self.targets.itemData(index) for index in range(self.targets.count())]
        saved_roles = {}
        for role in ("farmer", "support"):
            value = self.layout_settings.value(f"session/{role}", "", type=str)
            if value:
                try:
                    saved_roles[role] = match_session_window(available, Target.model_validate_json(value))
                except ValueError:
                    saved_roles[role] = None
        lu4 = [target for target in available if target.title.lower().startswith("lu4")]
        if not saved_roles and len(lu4) == 2:
            saved_roles = {"farmer": lu4[0], "support": lu4[1]}
        farmer, support = saved_roles.get("farmer"), saved_roles.get("support")
        if farmer is not None:
            for index, target in enumerate(available):
                if target == farmer:
                    self.targets.setCurrentIndex(index)
                    break
        elif "farmer" in saved_roles:
            self.targets.setCurrentIndex(-1)
        if (farmer is not None and support is not None and farmer.hwnd != support.hwnd
                and farmer in lu4 and support in lu4):
            for index in range(self.duo.targets.count()):
                if self.duo.targets.itemData(index) == support:
                    self.duo.targets.setCurrentIndex(index)
                    self.duo.capture_second()
                    break
        elif saved_roles:
            self.show_status("Порядок окон не восстановлен однозначно: выберите МА и саппорта")

    def open_examples(self) -> None:
        os.startfile(example_scripts_dir())

    def open_extensions(self) -> None:
        from hid_pilot.extensions import extensions_dir

        directory = extensions_dir()
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)

    def open_data(self) -> None:
        directory = user_data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)

    def open_telegram_config(self) -> None:
        try:
            os.startfile(self.telegram.config_path)
        except OSError:
            logger.warning("Не удалось открыть конфиг Telegram")
            self.show_status("Откройте telegram.toml в папке файлов приложения")

    def open_firmware(self) -> None:
        if self.firmware_open or self.auto_busy or (self.worker is not None and self.worker.is_alive()):
            self.show_status("Перед прошивкой Pico остановите сценарий и дождитесь завершения операций")
            return
        from hid_pilot.firmware_panel import FirmwareDialog

        self.firmware_open = True
        self.record_video.setEnabled(False)
        self.start_button.setEnabled(False)
        self.debug_start_button.setEnabled(False)
        self.quick.setEnabled(False)
        self.party.enabled.setChecked(False)
        try:
            dialog = FirmwareDialog(lambda: self.worker is None or not self.worker.is_alive(),
                                    self.port.text().strip(), self)
            dialog.exec()
            if dialog.verified_port:
                self.port.setText(dialog.verified_port)
                self.show_status(f"Pico проверен; выбран CDC data port {dialog.verified_port}")
        finally:
            self.firmware_open = False
            self.record_video.setEnabled(True)
            self.start_button.setEnabled(True)
            self.debug_start_button.setEnabled(True)
            self.quick.setEnabled(True)

    def show_status(self, text: str) -> None:
        self.status.setText(text.splitlines()[0].strip() if text else "")
        stamp = time.strftime("%H:%M:%S")
        self.journal.appendPlainText("\n".join(f"[{stamp}] {line}" for line in text.split("\n")))

    def refresh_system_chat(self) -> None:
        preview = latest_chat_preview()
        if preview is None:
            return
        age = max(0, time.monotonic() - preview.at)
        status = preview.status if age <= 3 else f"Нет свежего OCR {age:.0f} с; {preview.status}"
        text = "СИСТЕМНЫЙ ЧАТ\n" + status + "\n\n" + "\n".join(preview.lines)
        if text != self.system_chat_view.toPlainText():
            self.system_chat_view.setPlainText(text)

    def change_log_level(self, level: str) -> None:
        logging.getLogger("hid_pilot").setLevel(getattr(logging, level))

    def show_observation(self, payload: dict) -> None:
        if "text" in payload:
            self.party_state.setPlainText(payload["text"])
            self.party.status.setText(payload["text"])
            return
        state = payload["state"]
        text = format_statistics({"cp": state.cp, "hp": state.hp, "mp": state.mp},
                                 {"name": state.selected_name, "class": state.selected_class, "clan": state.selected_clan,
                                  "type": state.target.kind if state.target else None,
                                  "hp": state.target.hp if state.target else None,
                                  "level": state.target.level if state.target else None,
                                  "dead": state.target.dead if state.target else None},
                                 list(state.members), payload.get("rates", {}), payload["reason"],
                                 buffs=payload.get("buffs"))
        self.party_state.setPlainText(text)
        self.party.status.setText(text)

    def show_farm_code(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Полный алгоритм команды farm — farm.py")
        dialog.resize(950, 750)
        layout = QVBoxLayout(dialog)
        label = QLabel(
            "В .pilot хранятся настройки и вызов farm. Ниже — исполняемый Python-код этой команды.\n"
            "Это внешнее расширение; код можно обновлять без пересборки EXE."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        code = QPlainTextEdit()
        code.setReadOnly(True)
        code.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        code.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        from hid_pilot.extensions import extensions_dir

        source = extensions_dir() / "hid_pilot" / "farm.py"
        code.setPlainText(source.read_text("utf-8") if source.exists() else "Расширение farm не установлено")
        layout.addWidget(code, 1)
        dialog.exec()

    def refresh(self) -> None:
        previous = self.targets.currentData()
        self.targets.clear()
        for target in windows():
            if target.hwnd != int(self.winId()):
                self.targets.addItem(f"{target.title} [PID {target.pid}]", target)
        selected = -1
        for index in range(self.targets.count()):
            target = self.targets.itemData(index)
            if isinstance(previous, Target) and (target.hwnd, target.pid) == (
                previous.hwnd, previous.pid
            ):
                selected = index
                break
            if previous is None and target.title.lower().startswith("lu4"):
                selected = index
        if selected >= 0:
            self.targets.setCurrentIndex(selected)
        self.duo.set_windows([self.targets.itemData(index) for index in range(self.targets.count())])

    def open_script(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Открыть сценарий", "", "Сценарии (*.pilot)")
        if path:
            try:
                text = Path(path).read_text(encoding="utf-8-sig")
                if len(text) > 100_000:
                    raise ValueError("Сценарий слишком большой")
                self.editor.setPlainText(text)
                self.path = Path(path)
            except (OSError, ValueError) as exc:
                self.show_status(str(exc))

    def save_script(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить сценарий",
                                             str(self.path or "scenario.pilot"),
                                             "Сценарии (*.pilot)")
        if path:
            try:
                Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")
                self.path = Path(path)
            except OSError as exc:
                self.show_status(str(exc))

    def validate(self) -> None:
        try:
            parse_script(self.editor.toPlainText())
            self.show_status("Синтаксис проверен; ввод не выполнялся")
        except ValueError as exc:
            self.show_status(str(exc))

    def pick_screen(self) -> None:
        if self.auto_busy:
            self.show_status("Дождитесь завершения автопоиска")
            return
        if self.worker is not None and self.worker.is_alive():
            self.show_status("Остановите сценарий перед выбором цвета или полосы")
            return
        try:
            target = self.targets.currentData()
            if not isinstance(target, Target):
                raise TypeError("Выберите окно игры")
            target = activate(target)
        except Exception as exc:
            logger.exception("Не удалось открыть выбор области")
            self.show_status(str(exc))
            return

        def capture() -> None:
            from hid_pilot.picker import ScreenPicker
            try:
                image = Desktop(target).capture()
                picker = ScreenPicker(image, target.rect)
                if picker.exec() != QDialog.DialogCode.Accepted:
                    return
                if picker.command:
                    self.editor.textCursor().insertText(picker.command + "\n")
                    self.show_status("Цвет и координаты вставлены; клик в игру не отправлялся")
                self.activateWindow()
            except Exception as exc:
                logger.exception("Ошибка выбора цвета/полосы")
                self.show_status(str(exc))

        QTimer.singleShot(350, capture)

    def setup_duo(self, role: str, manual_party: bool, manual_stats: bool) -> None:
        panel = self.duo.panels[role]
        self.auto_numbers(manual_party, manual_stats, panel)

    def auto_numbers(self, manual_party: bool = False, manual_stats: bool = False,
                     panel: PartyPanel | None = None, *, manual_summon: bool = False) -> None:
        if self.auto_busy or (self.worker is not None and self.worker.is_alive()):
            self.show_status("Перед автопоиском остановите исполнение сценария")
            return
        selected_path = panel.path() if panel is not None else self.path
        if selected_path is None:
            self.show_status("Сначала сохраните сценарий — профиль хранится рядом с ним")
            return
        try:
            target = panel.target() if panel is not None else self.targets.currentData()
            if not isinstance(target, Target):
                raise TypeError("Выберите окно игры")
            target = activate(target)
            selected_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.exception("Не удалось начать автопоиск")
            self.show_status(str(exc))
            return
        self.auto_busy = True
        self.auto_search.setEnabled(False)
        self.auto_generation += 1
        generation, path, top_only = self.auto_generation, selected_path, self.auto_scope.currentData()
        context = {"panel": panel} if panel is not None else {}
        self.show_status("Снимок для ручной корректировки пати" if manual_party else
                         "Автопоиск HP/MP/CP, цели и пати: ожидайте окно проверки")

        def capture() -> None:
            try:
                image = Desktop(target).capture()
            except Exception as exc:
                logger.exception("Не удалось снять кадр для автопоиска")
                self.review_auto_numbers({"generation": generation, "error": str(exc)})
                return

            if manual_summon:
                self.review_summon({"generation": generation, "image": image,
                                    "target": target, "path": path, **context})
                return
            if manual_party:
                self.review_party({"generation": generation, "image": image,
                                   "target": target, "path": path, **context})
                return
            if manual_stats:
                self.review_auto_numbers({"generation": generation, "image": image,
                                          "target": target, "path": path, "candidates": [],
                                          "manual_stats": True, **context})
                return

            def search() -> None:
                from hid_pilot.auto_numbers import find_numbers
                from hid_pilot.auto_party import find_party
                from hid_pilot.auto_player import find_player_panels
                from hid_pilot.numbers import shared_numbers
                payload = {"generation": generation, "image": image, "target": target, "path": path, **context}
                try:
                    payload["candidates"] = find_numbers(image, shared_numbers(), top_only)
                    payload["party_candidates"] = find_party(image, shared_numbers())
                    target_boxes = [item.box for item in payload["candidates"] if item.kind == "target_hp"]
                    payload["player_candidates"] = find_player_panels(
                        image, shared_numbers(), top_only,
                        target_boxes[0] if len(target_boxes) == 1 else None)
                except Exception as exc:
                    logger.exception("Автопоиск OCR завершился с ошибкой")
                    payload["error"] = str(exc)
                self.events.auto_found.emit(payload)

            threading.Thread(target=search, name="AutoNumbers", daemon=True).start()

        QTimer.singleShot(350, capture)

    def review_summon(self, payload: dict) -> None:
        from hid_pilot.bars import BarProfile
        from hid_pilot.summon_review import SummonReview

        if payload["generation"] != self.auto_generation:
            return
        try:
            target = payload["target"]
            height, width = payload["image"].shape[:2]
            path = payload["path"].parent / "farm-bars.json"
            current = snapshot(target.hwnd)
            if (current.pid, current.rect) != (target.pid, target.rect) or not self.setup_context_matches(payload):
                raise ValueError("Окно/сценарий изменились; повторите настройку самона")
            profile = (BarProfile.model_validate_json(path.read_text("utf-8")) if path.exists()
                       else BarProfile(width=width, height=height))
            if (profile.width, profile.height) != (width, height):
                raise ValueError("Размер окна изменился; сначала настройте основные статы заново")
            self.activateWindow()
            dialog = SummonReview(payload["image"], profile.summon, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self.show_status("Настройка самона отменена; профиль не изменён")
                return
            current = snapshot(target.hwnd)
            if (current.pid, current.rect) != (target.pid, target.rect) or not self.setup_context_matches(payload):
                raise ValueError("Окно/сценарий изменились; повторите настройку самона")
            # Меняется только панель самона; остальные настройки остаются в профиле.
            profile.summon = dialog.profile
            path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
            panel = payload.get("panel", self.party)
            panel.enabled.setChecked(False)
            panel.reset()
            self.show_status("Панель самона сохранена" if profile.summon else "Наблюдение самона отключено")
        except Exception as exc:
            logger.exception("Не удалось настроить самона")
            self.show_status(str(exc))
        finally:
            self.auto_busy = False
            self.auto_search.setEnabled(True)

    def setup_context_matches(self, payload: dict) -> bool:
        panel = payload.get("panel")
        if panel is None:
            return self.path == payload["path"]
        selected = panel.target()
        target = payload["target"]
        return (panel.path() == payload["path"] and isinstance(selected, Target)
                and (selected.hwnd, selected.pid) == (target.hwnd, target.pid))

    def review_party(self, payload: dict) -> None:
        from hid_pilot.party import PartyProfile
        from hid_pilot.party_review import PartyReview

        if payload["generation"] != self.auto_generation:
            return
        try:
            target = payload["target"]
            current = snapshot(target.hwnd)
            if (current.pid, current.rect) != (target.pid, target.rect) or not self.setup_context_matches(payload):
                raise RuntimeError("Окно/сценарий изменились — повторите настройку пати")
            path = payload["path"].parent / "party.json"
            image = payload["image"]
            previous = None
            if path.exists():
                try:
                    previous = PartyProfile.model_validate_json(path.read_text("utf-8"))
                    if (previous.height, previous.width) != image.shape[:2]:
                        previous = None
                except ValueError:
                    logger.warning("Старый профиль пати некорректен; нужна новая разметка")
            self.activateWindow()
            dialog = PartyReview(image, previous, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self.show_status("Настройка пати отменена; профиль не изменён")
                return
            current = snapshot(target.hwnd)
            if (current.pid, current.rect) != (target.pid, target.rect) or not self.setup_context_matches(payload):
                raise RuntimeError("Окно/сценарий изменились — повторите настройку пати")
            path.write_text(dialog.profile().model_dump_json(indent=2), encoding="utf-8")
            panel = payload.get("panel", self.party)
            panel.enabled.setChecked(False)
            panel.reset()
            self.show_status("Разметка восьми строк пати сохранена в party.json")
        except Exception as exc:
            logger.exception("Не удалось настроить пати")
            self.show_status(str(exc))
        finally:
            self.auto_busy = False
            self.auto_search.setEnabled(True)

    def review_auto_numbers(self, payload: dict) -> None:
        from hid_pilot.auto_review import AutoReview
        from hid_pilot.bars import BarProfile
        if payload["generation"] != self.auto_generation:
            return
        self.auto_busy = False
        self.auto_search.setEnabled(True)
        try:
            if "error" in payload:
                raise RuntimeError(payload["error"])
            target = payload["target"]
            current = snapshot(target.hwnd)
            if (current.pid, current.rect) != (target.pid, target.rect) or not self.setup_context_matches(payload):
                raise RuntimeError("Окно/сценарий изменились — повторите автопоиск")
            image, candidates = payload["image"], payload["candidates"]
            height, width = image.shape[:2]
            profile_path = payload["path"].parent / "farm-bars.json"
            profile = BarProfile(width=width, height=height)
            if profile_path.exists():
                previous = BarProfile.model_validate_json(profile_path.read_text("utf-8"))
                if (previous.width, previous.height) == (width, height):
                    profile = previous
            self.activateWindow()
            dialog = AutoReview(image, candidates, self, payload.get("party_candidates", []),
                                payload.get("player_candidates", []), initial_profile=profile)
            if payload.get("manual_stats"):
                dialog.setWindowTitle("Ручная настройка статов и уровней")
                dialog.edit_own_regions()
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self.show_status("Автопоиск отменён; профиль не изменён")
                return
            current = snapshot(target.hwnd)
            if (current.pid, current.rect) != (target.pid, target.rect) or not self.setup_context_matches(payload):
                raise RuntimeError("Геометрия окна изменилась — повторите автопоиск")
            profile.numbers = dialog.boxes.copy()
            profile.summon = getattr(dialog, "summon", profile.summon)
            if isinstance(getattr(dialog, "chat_enabled", None), QCheckBox):
                profile.system_chat = dialog.system_chat
                profile.system_chat_enabled = dialog.chat_enabled.isChecked()
            level_boxes = getattr(dialog, "level_boxes", {})
            if isinstance(level_boxes, dict):
                for name, box in level_boxes.items():
                    setattr(profile, name, box)
            profile.target_player_panel = dialog.target_player_panel
            target_config = getattr(dialog, "target_config", {})
            if isinstance(target_config, dict) and target_config:
                profile = BarProfile.model_validate(profile.model_dump() | target_config)
            if profile.target_hp_presence_box is None:
                profile.target_hp_presence_box = profile.numbers.get("target_hp")
            # Новый автопоиск заменяет старые ручные ROI, включая fallback.
            if payload.get("manual_stats"):
                for name in dialog.boxes:
                    profile.bars.pop(name, None)
            else:
                profile.bars.clear()
                profile.target_zero_box = None
                profile.target_zero_mask = []
            party = dialog.party_profile()
            party_path = payload["path"].parent / "party.json"
            if party is not None:
                party_path.write_text(party.model_dump_json(indent=2), encoding="utf-8")
            elif party_path.exists() and not payload.get("manual_stats"):
                party_path.unlink()
            panel = payload.get("panel", self.party)
            panel.enabled.setChecked(False)
            panel.reset()
            profile_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
            self.show_status(f"Подтверждены области: {', '.join(dialog.boxes) or 'нет'}; "
                             f"пати: {'да' if party else 'нет'}")
        except Exception as exc:
            logger.exception("Не удалось сохранить предложения OCR")
            self.show_status(str(exc))

    def start(self, debug: bool = False) -> None:
        if self.tabs.currentWidget() is self.party:
            self.show_status("Наблюдение включается флажком на вкладке Пати")
            return
        if self.tabs.currentIndex() == 0:
            self.quick.start_selected()
            return
        self.launch(debug=debug)

    def start_loops(self, plans: tuple[KeyLoop, ...]) -> None:
        if not plans:
            self.show_status("Отметьте хотя бы одну строку")
            return
        self.launch(plans)

    def show_replay_status(self, text: str) -> None:
        self.replay_status.setText(text)
        self.journal.appendPlainText(text)

    def launch(self, plans: tuple[KeyLoop, ...] | None = None, *, debug: bool = False) -> None:
        if self.firmware_open:
            self.show_status("Дождитесь завершения обслуживания Pico и закройте окно прошивки")
            return
        if self.auto_busy:
            self.show_status("Дождитесь завершения автопоиска")
            return
        if self.worker is not None and self.worker.is_alive():
            return
        if self.replay.busy:
            self.show_status("Дождитесь сохранения видео предыдущего запуска")
            return
        record_video = self.record_video.isChecked()
        duo_mode = plans is None and self.tabs.currentWidget() is self.duo
        try:
            script = parse_script(self.editor.toPlainText()) if plans is None and not duo_mode else ()
            variables = script_variables(self.editor.toPlainText()) if plans is None and not duo_mode else {}
            base = self.path.parent if self.path else Path.cwd()
            from hid_pilot.extensions import extension_commands, extension_symbol
            extensions = extension_commands()

            def prepare(items: tuple) -> None:
                for instruction in items:
                    if instruction.operation in extensions:
                        command = extensions[instruction.operation]
                        extension_symbol(command.prepare)(base, instruction.args)
                    prepare(instruction.body)

            prepare(script)
            target = self.targets.currentData()
            if not isinstance(target, Target) or not self.hotkey.available:
                raise ValueError("Нужны выбранное окно и рабочий hard-stop")
            port = self.port.text().strip()
            if not port:
                raise ValueError("Укажите COM-порт")
            if duo_mode:
                config = self.duo.config()
                support = self.duo.captured
                if not isinstance(support, Target):
                    raise ValueError("Захватите второе окно")
                current = snapshot(support.hwnd)
                if (current.pid, current.rect) != (support.pid, support.rect):
                    raise ValueError("Второе окно изменилось; захватите его заново")
                # Геометрия первого окна фиксируется на весь совместный запуск.
                current = snapshot(target.hwnd)
                if current.pid != target.pid:
                    raise ValueError("Окно МА сменило процесс")
                target = current
                profile = prepare_duo(self.duo.base(), target, support)
                party_profile = load_duo_party(self.duo.base(), target)
            activated = activate(target)
            if duo_mode and activated.rect != target.rect:
                raise ValueError("Геометрия окна МА изменилась; повторите запуск")
            target = activated
        except Exception as exc:
            logger.exception("Не удалось подготовить запуск")
            self.show_status(str(exc))
            return
        self.cancel.clear()
        self.silent_button_stop.clear()
        if duo_mode:
            self.party.enabled.setChecked(False)
        self.duo.set_running(True)
        # У хилера собственный observer; второй OCR монитор не должен мешать его tick.
        if plans is None and any(extensions[item.operation].exclusive_observer
                                 for item in script if item.operation in extensions):
            self.party.enabled.setChecked(False)
        self.record_video.setEnabled(False)
        self.start_button.setEnabled(False)
        self.debug_start_button.setEnabled(False)
        self.quick.set_running(plans if plans is not None else ())
        cancelled = self.quick.cancelled.copy()
        self.targets.setEnabled(False)
        self.show_status(f"Старт через 1 секунду: {target.title}")

        script_name = self.duo.path.name if duo_mode else "Быстрые клавиши" if plans is not None else (
            self.path.name if self.path is not None else "Несохранённый сценарий")

        def work() -> None:
            transport = None
            started_at = time.monotonic()
            stop_reason = "Сценарий завершён"
            recording = False
            finished_after_support_death = False
            try:
                if record_video:
                    self.replay.prepare()
                    recording = True
                if self.cancel.wait(1):
                    raise Stopped("Старт отменён")
                desktop = Desktop(target)
                transport = PicoTransport(port)
                self.controller = ActionController(desktop, transport,
                                                   lambda: self.hotkey.available, self.events.status.emit)
                self.controller.debug_capture = debug
                if self.cancel.is_set():
                    raise Stopped("Старт отменён")
                self.controller.prepare_keyboard((target, support) if duo_mode else (target,))
                self.controller.start()
                if duo_mode:
                    notifications = ScriptNotifications(self.events, self.telegram, script_name, target.title,
                                                        duo_mode=True, replay=self.replay if recording else None,
                                                        support_window=support.title)
                    duo_farmer = DuoFarmer(self.controller, target, support, profile, config, notifications,
                                           self.events.duo_observation.emit, party_profile=party_profile,
                                           support_life=SupportLifeObserver(notifications.support_death,
                                                                            notifications.support_revived))
                    try:
                        duo_farmer.setup_observation(self.duo.base() / "farmer")
                        duo_farmer.run()
                    finally:
                        duo_farmer.close_observation()
                elif plans is None:
                    notifications = ScriptNotifications(self.events, self.telegram, script_name, target.title,
                                                        replay=self.replay if recording else None)
                    Runner(self.controller, base, notifications, variables).run(script)
                else:
                    run_key_loops(self.controller, plans, cancelled, self.events.status.emit)
                self.events.status.emit("Сценарий завершён")
            except SupportDied as exc:
                finished_after_support_death = True
                stop_reason = str(exc)
                logger.info("%s", stop_reason)
                self.events.status.emit(stop_reason)
            except Exception as exc:
                stop_reason = f"Остановлено: {exc}"
                logger.exception("Сценарий остановлен")
                self.events.status.emit(f"Остановлено: {exc}")
            finally:
                try:
                    if self.controller is not None:
                        self.controller.close()
                    elif transport is not None:
                        transport.close()
                except Exception as exc:
                    stop_reason += f"; ошибка освобождения HID: {exc}"
                    logger.exception("Ошибка освобождения HID")
                    self.events.status.emit(f"Ошибка отключения: {exc}")
                self.controller = None
                if recording:
                    if finished_after_support_death:
                        self.replay.trigger(stop_reason, final=True, preserve_death=True)
                    else:
                        self.replay.trigger(stop_reason, final=True)
                if not self.silent_button_stop.is_set():
                    self.telegram.notify_stopped(ScriptStopped(script_name, target.title, stop_reason,
                                                               time.monotonic() - started_at))
                self.events.finished.emit()

        self.worker = threading.Thread(target=work, name="ScriptRunner", daemon=True)
        self.worker.start()

    def stop_from_button(self) -> None:
        self.stop()

    def stop(self) -> None:
        # Ручной STOP, hard-stop и закрытие GUI не отправляют уведомление Telegram.
        self.silent_button_stop.set()
        self.cancel.set()
        self.replay.trigger("STOP / hard-stop", final=True)
        controller = self.controller
        if controller is not None:
            if controller.stopped.is_set():
                return
            # Отменяем выполнение сразу, а lock и ответ Pico ждём вне GUI и hard-stop.
            controller.stopped.set()

            def stop_input() -> None:
                try:
                    controller.stop()
                except Exception:
                    logger.exception("Ошибка STOP; действует watchdog платы")

            threading.Thread(target=stop_input, name="StopInput", daemon=True).start()

    def toggle_pause(self) -> None:
        controller = self.controller
        if controller is None or controller.stopped.is_set():
            return
        # Watchdog применяет запрос под lock; GUI не ждёт текущую HID/CV-операцию.
        if controller.manual_paused.is_set():
            controller.manual_paused.clear()
            self.events.status.emit("Ручная пауза снята; продолжение при активации окна игры")
        else:
            controller.manual_paused.set()
            self.events.status.emit("Запрошена PAUSE — вручную; для продолжения нажмите кнопку ещё раз")

    def finished(self) -> None:
        self.record_video.setEnabled(True)
        self.duo.set_running(False)
        self.start_button.setEnabled(True)
        self.debug_start_button.setEnabled(True)
        self.targets.setEnabled(True)
        self.quick.set_running(None)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.firmware_open:
            self.show_status("Завершите обслуживание Pico и закройте окно прошивки перед выходом")
            event.ignore()
            return
        self.auto_generation += 1
        self.stop()
        if not self.party.shutdown() or not self.duo.shutdown():
            self.show_status("Ожидание завершения OCR пати; повторите закрытие")
            event.ignore()
            return
        if self.worker is not None:
            self.worker.join(timeout=2)
            if self.worker.is_alive():
                self.show_status("Ожидание завершения worker; повторите закрытие")
                event.ignore()
                return
        if not self.replay.close():
            self.show_status("Остановка видеобуфера; повторите закрытие через несколько секунд")
            event.ignore()
            return
        self.hotkey.close()
        self.save_layout()
        event.accept()


def main() -> int:
    setup_logging()
    if "--self-test" in sys.argv:
        from hid_pilot.self_test import run_self_test
        return run_self_test(sys.argv[1:])
    if sys.platform != "win32":
        logger.error("HID Pilot поддерживает Windows")
        return 1
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
