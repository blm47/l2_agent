import ctypes
import logging
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QFontDatabase, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
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
from hid_pilot.desktop import Desktop, Target, activate, snapshot, windows
from hid_pilot.hard_stop import HardStopHotkey
from hid_pilot.logging_setup import setup_logging
from hid_pilot.loops import KeyLoop, run_key_loops
from hid_pilot.party_panel import PartyPanel
from hid_pilot.party_statistics import format_statistics
from hid_pilot.paths import example_scripts_dir, user_data_dir
from hid_pilot.quick_panel import QuickPanel
from hid_pilot.runner import Runner
from hid_pilot.script import parse_script, script_variables
from hid_pilot.transport import PicoTransport

logger = logging.getLogger("hid_pilot.gui")


class Events(QObject):
    status = Signal(str)
    finished = Signal()
    auto_found = Signal(object)
    observation = Signal(object)


class ScriptNotifications:
    def __init__(self, events: Events) -> None:
        self.events = events

    def __call__(self, text: str) -> None:
        self.events.status.emit(text)

    def state(self, payload: object) -> None:
        self.events.observation.emit(payload)


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
        self.auto_busy = False
        self.auto_generation = 0
        self.firmware_open = False
        self.events = Events()
        self.events.status.connect(self.show_status)
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
        layout.addWidget(self.tabs, 1)
        self.editor = QPlainTextEdit("# Безопасный первый сценарий без ввода\nwait 1\n")
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setMinimumHeight(300)
        self.party_state = QPlainTextEdit("Ожидание распознавания пати")
        self.party_state.setReadOnly(True)
        self.party_state.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.party_state.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.party_state.setMinimumWidth(230)
        self.script_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.script_splitter.addWidget(self.editor)
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
        pause = QPushButton("PAUSE / RESUME")
        pause.clicked.connect(self.toggle_pause)
        row.addWidget(pause)
        stop = QPushButton("STOP")
        stop.clicked.connect(self.stop)
        row.addWidget(stop)
        layout.addLayout(row)
        self.status = QLabel("Готово" if self.hotkey.available else self.hotkey.error)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.journal = QPlainTextEdit()
        self.journal.setReadOnly(True)
        self.journal.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.journal.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.journal.setMaximumBlockCount(500)
        self.journal.setMinimumHeight(60)
        self.log_splitter = QSplitter(Qt.Orientation.Vertical)
        self.log_splitter.setChildrenCollapsible(False)
        self.log_splitter.addWidget(body)
        self.log_splitter.addWidget(self.journal)
        self.log_splitter.setStretchFactor(0, 2)
        self.log_splitter.setStretchFactor(1, 1)
        self.log_splitter.handle(1).setToolTip("Потяните, чтобы изменить высоту журнала")
        self.setCentralWidget(self.log_splitter)
        file_menu = self.menuBar().addMenu("Файл")
        file_menu.addAction("Открыть папку примеров скриптов", self.open_examples)
        file_menu.addAction("Открыть папку расширений", self.open_extensions)
        file_menu.addAction("Открыть логи и файлы приложения", self.open_data)
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

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self.layout_restored:
            return
        self.layout_restored = True
        # Пропорции скриншота применяем после расчёта размеров видимых панелей.
        for name, splitter, weights in (
                ("script", self.script_splitter, (2, 3)),
                ("journal", self.log_splitter, (2, 1))):
            saved = self.layout_settings.value(name)
            if saved is None or not splitter.restoreState(saved):
                extent = splitter.width() if name == "script" else splitter.height()
                splitter.setSizes([extent * weight // sum(weights) for weight in weights])

    def save_layout(self) -> None:
        self.layout_settings.setValue("geometry", self.saveGeometry())
        self.layout_settings.setValue("tab", self.tabs.currentIndex())
        self.layout_settings.setValue("port", self.port.text())
        self.layout_settings.setValue("upper_scope", self.auto_scope.currentData())
        self.layout_settings.setValue("log_level", self.log_level.currentText())
        self.layout_settings.setValue("script", self.script_splitter.saveState())
        self.layout_settings.setValue("journal", self.log_splitter.saveState())
        self.layout_settings.sync()
        if self.layout_settings.status() != QSettings.Status.NoError:
            logger.warning("Не удалось сохранить размеры окна и панелей")

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

    def open_firmware(self) -> None:
        if self.firmware_open or self.auto_busy or (self.worker is not None and self.worker.is_alive()):
            self.show_status("Перед прошивкой Pico остановите сценарий и дождитесь завершения операций")
            return
        from hid_pilot.firmware_panel import FirmwareDialog

        self.firmware_open = True
        self.start_button.setEnabled(False)
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
            self.start_button.setEnabled(True)
            self.quick.setEnabled(True)

    def show_status(self, text: str) -> None:
        self.status.setText(text.splitlines()[0].strip() if text else "")
        self.journal.appendPlainText(text)

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
                                  "dead": state.target.dead if state.target else None},
                                 list(state.members), payload.get("rates", {}), payload["reason"])
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

    def auto_numbers(self, manual_party: bool = False, manual_stats: bool = False) -> None:
        if self.auto_busy or (self.worker is not None and self.worker.is_alive()):
            self.show_status("Перед автопоиском остановите исполнение сценария")
            return
        if self.path is None:
            self.show_status("Сначала сохраните сценарий — профиль хранится рядом с ним")
            return
        try:
            target = self.targets.currentData()
            if not isinstance(target, Target):
                raise TypeError("Выберите окно игры")
            target = activate(target)
        except Exception as exc:
            logger.exception("Не удалось начать автопоиск")
            self.show_status(str(exc))
            return
        self.auto_busy = True
        self.auto_search.setEnabled(False)
        self.auto_generation += 1
        generation, path, top_only = self.auto_generation, self.path, self.auto_scope.currentData()
        self.show_status("Снимок для ручной корректировки пати" if manual_party else
                         "Автопоиск HP/MP/CP, цели и пати: ожидайте окно проверки")

        def capture() -> None:
            try:
                image = Desktop(target).capture()
            except Exception as exc:
                logger.exception("Не удалось снять кадр для автопоиска")
                self.review_auto_numbers({"generation": generation, "error": str(exc)})
                return

            if manual_party:
                self.review_party({"generation": generation, "image": image,
                                   "target": target, "path": path})
                return
            if manual_stats:
                self.review_auto_numbers({"generation": generation, "image": image,
                                          "target": target, "path": path, "candidates": [],
                                          "manual_stats": True})
                return

            def search() -> None:
                from hid_pilot.auto_numbers import find_numbers
                from hid_pilot.auto_party import find_party
                from hid_pilot.auto_player import find_player_panels
                from hid_pilot.numbers import shared_numbers
                payload = {"generation": generation, "image": image, "target": target, "path": path}
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

    def review_party(self, payload: dict) -> None:
        from hid_pilot.party import PartyProfile
        from hid_pilot.party_review import PartyReview

        if payload["generation"] != self.auto_generation:
            return
        try:
            target = payload["target"]
            current = snapshot(target.hwnd)
            if (current.pid, current.rect) != (target.pid, target.rect) or self.path != payload["path"]:
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
            if (current.pid, current.rect) != (target.pid, target.rect) or self.path != payload["path"]:
                raise RuntimeError("Окно/сценарий изменились — повторите настройку пати")
            path.write_text(dialog.profile().model_dump_json(indent=2), encoding="utf-8")
            self.party.enabled.setChecked(False)
            self.party.reset()
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
            if (current.pid, current.rect) != (target.pid, target.rect) or self.path != payload["path"]:
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
            if (current.pid, current.rect) != (target.pid, target.rect):
                raise RuntimeError("Геометрия окна изменилась — повторите автопоиск")
            profile.numbers = dialog.boxes.copy()
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
            self.party.enabled.setChecked(False)
            self.party.reset()
            profile_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
            self.show_status(f"Подтверждены области: {', '.join(dialog.boxes) or 'нет'}; "
                             f"пати: {'да' if party else 'нет'}")
        except Exception as exc:
            logger.exception("Не удалось сохранить предложения OCR")
            self.show_status(str(exc))

    def start(self) -> None:
        if self.tabs.currentWidget() is self.party:
            self.show_status("Наблюдение включается флажком на вкладке Пати")
            return
        if self.tabs.currentIndex() == 0:
            self.quick.start_selected()
            return
        self.launch()

    def start_loops(self, plans: tuple[KeyLoop, ...]) -> None:
        if not plans:
            self.show_status("Отметьте хотя бы одну строку")
            return
        self.launch(plans)

    def launch(self, plans: tuple[KeyLoop, ...] | None = None) -> None:
        if self.firmware_open:
            self.show_status("Дождитесь завершения обслуживания Pico и закройте окно прошивки")
            return
        if self.auto_busy:
            self.show_status("Дождитесь завершения автопоиска")
            return
        if self.worker is not None and self.worker.is_alive():
            return
        try:
            script = parse_script(self.editor.toPlainText()) if plans is None else ()
            variables = script_variables(self.editor.toPlainText()) if plans is None else {}
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
            target = activate(target)
        except Exception as exc:
            logger.exception("Не удалось подготовить запуск")
            self.show_status(str(exc))
            return
        self.cancel.clear()
        # У хилера собственный observer; второй OCR монитор не должен мешать его tick.
        if plans is None and any(extensions[item.operation].exclusive_observer
                                 for item in script if item.operation in extensions):
            self.party.enabled.setChecked(False)
        self.start_button.setEnabled(False)
        self.quick.set_running(plans if plans is not None else ())
        cancelled = self.quick.cancelled.copy()
        self.targets.setEnabled(False)
        self.show_status(f"Старт через 1 секунду: {target.title}")

        def work() -> None:
            transport = None
            try:
                if self.cancel.wait(1):
                    raise Stopped("Старт отменён")
                desktop = Desktop(target)
                transport = PicoTransport(port)
                self.controller = ActionController(desktop, transport,
                                                   lambda: self.hotkey.available, self.events.status.emit)
                if self.cancel.is_set():
                    raise Stopped("Старт отменён")
                self.controller.start()
                if plans is None:
                    Runner(self.controller, base, ScriptNotifications(self.events), variables).run(script)
                else:
                    run_key_loops(self.controller, plans, cancelled, self.events.status.emit)
                self.events.status.emit("Сценарий завершён")
            except Exception as exc:
                logger.exception("Сценарий остановлен")
                self.events.status.emit(f"Остановлено: {exc}")
            finally:
                try:
                    if self.controller is not None:
                        self.controller.close()
                    elif transport is not None:
                        transport.close()
                except Exception as exc:
                    logger.exception("Ошибка освобождения HID")
                    self.events.status.emit(f"Ошибка отключения: {exc}")
                self.controller = None
                self.events.finished.emit()

        self.worker = threading.Thread(target=work, name="ScriptRunner", daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.cancel.set()
        controller = self.controller
        if controller is not None:
            try:
                controller.stop()
            except Exception:
                logger.exception("Ошибка STOP; действует watchdog платы")

    def toggle_pause(self) -> None:
        controller = self.controller
        if controller is None:
            return
        try:
            if controller.manual_paused.is_set():
                controller.resume()
                self.events.status.emit("Ручная пауза снята; продолжение при активации окна игры")
            else:
                controller.pause()
                self.events.status.emit("PAUSE — вручную; для продолжения нажмите кнопку ещё раз")
        except Exception as exc:
            logger.exception("Не удалось переключить паузу")
            self.events.status.emit(str(exc))

    def finished(self) -> None:
        self.start_button.setEnabled(True)
        self.targets.setEnabled(True)
        self.quick.set_running(None)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.firmware_open:
            self.show_status("Завершите обслуживание Pico и закройте окно прошивки перед выходом")
            event.ignore()
            return
        self.auto_generation += 1
        self.stop()
        if not self.party.shutdown():
            self.show_status("Ожидание завершения OCR пати; повторите закрытие")
            event.ignore()
            return
        if self.worker is not None:
            self.worker.join(timeout=2)
            if self.worker.is_alive():
                self.show_status("Ожидание завершения worker; повторите закрытие")
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
