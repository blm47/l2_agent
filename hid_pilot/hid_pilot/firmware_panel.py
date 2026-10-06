"""
Отдельное окно прошивки Pico: действия только по явному нажатию пользователя.
"""

import logging
import os
import threading
from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hid_pilot.firmware_manager import (
    BOARDS,
    PicoVolume,
    backup_files,
    discover_volumes,
    flash_runtime,
    install_scripts,
    pico_ports,
    verify_firmware,
)
from hid_pilot.paths import user_data_dir

logger = logging.getLogger("hid_pilot.firmware.gui")
AUTO_PORT = "Автоопределение CDC data port"


class FirmwareDialog(QDialog):
    progress = Signal(str)
    completed = Signal(object)

    def __init__(self, available: Callable[[], bool], port: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Прошивка Pico контроллера")
        self.resize(800, 650)
        self.available = available
        self.busy = False
        self.worker: threading.Thread | None = None
        self.verified_port = ""
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Новая плата: подключите Pico с зажатой BOOTSEL → выберите RPI-RP2 и модель платы "
            "→ установите CircuitPython. Затем обновите список, выберите CIRCUITPY и установите HID Pilot.\n"
            "После установки файлов безопасно извлеките CIRCUITPY, переподключите USB без BOOTSEL "
            "и проверьте прошивку. Все файлы прошивки встроены; интернет не требуется.\n"
            "Для обновления уже работающего Pico обычно достаточно установки HID Pilot на CIRCUITPY. "
            "Примеры скриптов находятся в меню «Файл»."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.volume = QComboBox()
        self.board = QComboBox()
        for key, title in BOARDS.items():
            self.board.addItem(title, key)
        self.port = QComboBox()
        self.port.setEditable(True)
        form.addRow("USB-носитель Pico", self.volume)
        form.addRow("Модель платы для UF2", self.board)
        form.addRow("CDC data port для проверки", self.port)
        layout.addLayout(form)
        refresh = QPushButton("Обновить платы и COM-порты")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        row = QHBoxLayout()
        self.install_button = QPushButton("Прошить Pico…")
        self.install_button.clicked.connect(self.install)
        self.verify_button = QPushButton("Проверить прошивку")
        self.verify_button.clicked.connect(self.verify)
        self.backup_button = QPushButton("Сохранить копию файлов Pico")
        self.backup_button.clicked.connect(self.backup)
        for button in (self.install_button, self.verify_button, self.backup_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.erase_ack = QCheckBox(
            "Для полного сброса: разрешаю удалить все данные Pico; нужные файлы сохранены"
        )
        layout.addWidget(self.erase_ack)
        self.reset_button = QPushButton("Полный сброс памяти Pico (BOOTSEL)…")
        self.reset_button.clicked.connect(self.reset_board)
        layout.addWidget(self.reset_button)
        note = QLabel(
            "Копия сохраняет файлы CIRCUITPY, включая настройки; образ CircuitPython и NVM "
            "в неё не входят. Полный сброс удаляет прошивку, файлы и NVM, возвращая плату "
            "в BOOTSEL. После сброса установите CircuitPython и HID Pilot заново."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel("Готово")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.journal = QPlainTextEdit()
        self.journal.setReadOnly(True)
        self.journal.setMaximumBlockCount(300)
        layout.addWidget(self.journal, 1)
        folders = QHBoxLayout()
        self.backups_button = QPushButton("Открыть резервные копии")
        self.backups_button.clicked.connect(self.open_backups)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        folders.addWidget(self.backups_button)
        folders.addWidget(close)
        layout.addLayout(folders)
        self.controls = [self.volume, self.board, self.port, refresh, self.install_button,
                         self.verify_button, self.backup_button, self.erase_ack, self.reset_button]
        self.progress.connect(self.report)
        self.completed.connect(self.finish)
        self.volume.currentIndexChanged.connect(self.update_selection)
        self.refresh(port)

    def report(self, text: str) -> None:
        logger.info("%s", text)
        self.status.setText(text)
        self.journal.appendPlainText(text)

    def refresh(self, preferred_port: str = "") -> None:
        if self.busy:
            return
        previous = self.volume.currentData()
        self.volume.clear()
        self.volume.addItem("Плата не выбрана", None)
        for volume in discover_volumes():
            title = f"{volume.root} · {volume.kind} · {volume.uid or volume.board}"
            self.volume.addItem(title, volume)
            if volume == previous:
                self.volume.setCurrentIndex(self.volume.count() - 1)
        if self.volume.count() == 2:
            self.volume.setCurrentIndex(1)
        selected_port = preferred_port if isinstance(preferred_port, str) else ""
        selected_port = selected_port or self.selected_port()
        self.port.clear()
        self.port.addItem(AUTO_PORT)
        for port in pico_ports():
            self.port.addItem(port)
            if port == selected_port:
                self.port.setCurrentIndex(self.port.count() - 1)
        self.update_selection()
        self.report("Список обновлён. Выберите одну конкретную плату; прошивка сама не запускается.")

    def selected_port(self) -> str:
        text = self.port.currentText().strip()
        return "" if text == AUTO_PORT else text

    def update_selection(self) -> None:
        volume = self.volume.currentData()
        if isinstance(volume, PicoVolume) and volume.kind == "circuitpython":
            self.board.setCurrentIndex(self.board.findData(volume.board))
            self.install_button.setText("Установить / обновить HID Pilot…")
        else:
            self.install_button.setText("Установить CircuitPython (BOOTSEL)…")

    def require_volume(self, kind: str = "") -> PicoVolume:
        volume = self.volume.currentData()
        if not isinstance(volume, PicoVolume) or (kind and volume.kind != kind):
            raise ValueError("Выберите CIRCUITPY Pico" if kind == "circuitpython" else
                             "Выберите диск RPI-RP2: подключите Pico с зажатой BOOTSEL" if kind else
                             "Выберите USB-носитель Pico")
        return volume

    def confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text, QMessageBox.StandardButton.Yes |
                                    QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def run_job(self, title: str, job: Callable[[], dict]) -> None:
        if self.busy or not self.available():
            self.report("Перед обслуживанием Pico остановите сценарии и дождитесь завершения операций")
            return
        self.busy = True
        for control in self.controls:
            control.setEnabled(False)
        self.report(title)

        def work() -> None:
            try:
                result = job()
            except Exception as exc:
                logger.exception("Обслуживание Pico не завершено")
                result = {"message": f"Ошибка: {exc}"}
            self.completed.emit(result)

        self.worker = threading.Thread(target=work, name="PicoFirmware", daemon=True)
        self.worker.start()

    def finish(self, result: dict) -> None:
        self.busy = False
        for control in self.controls:
            control.setEnabled(True)
        self.erase_ack.setChecked(False)
        self.verified_port = result.get("port", "")
        self.report(result["message"])

    def install(self) -> None:
        try:
            volume = self.require_volume()
            board = self.board.currentData()
            if volume.kind == "bootloader":
                if not self.confirm("Установка CircuitPython", f"Плата: {BOARDS[board]}; диск {volume.root}.\n"
                                    "Записать встроенный CircuitPython? После записи нужно установить файлы HID Pilot."):
                    return

                def job() -> dict:
                    flash_runtime(volume, board)
                    return {"message": "UF2 отправлен. Дождитесь CIRCUITPY, обновите список и установите HID Pilot. "
                            "Если диск не появился, переподключите USB без BOOTSEL."}
            else:
                if not self.confirm("Установка HID Pilot", f"Плата: {volume.uid or volume.board}; диск {volume.root}.\n"
                                    "Сохранить резервную копию файлов и заменить boot.py, code.py, control.py?"):
                    return

                def job() -> dict:
                    backup = install_scripts(volume, self.progress.emit)
                    return {"message": "Файлы HID_PILOT_V3 проверены. Безопасно извлеките CIRCUITPY, "
                            f"переподключите USB и проверьте прошивку. Резервная копия: {backup}"}
            self.run_job("Запись прошивки Pico…", job)
        except (ValueError, KeyError, TypeError) as exc:
            self.report(f"Прошивка не начата: {exc}")

    def backup(self) -> None:
        try:
            volume = self.require_volume("circuitpython")
            self.run_job("Сохранение файлов Pico…", lambda: {
                "message": f"Копия файлов сохранена: {backup_files(volume)}"})
        except ValueError as exc:
            self.report(str(exc))

    def verify(self) -> None:
        volume = self.volume.currentData()
        if isinstance(volume, PicoVolume) and volume.kind == "bootloader":
            self.report("Плата находится в BOOTSEL; установите CircuitPython и HID Pilot")
            return
        port = self.selected_port()

        def job() -> dict:
            confirmed_port, message = verify_firmware(volume, port)
            return {"message": message, "port": confirmed_port}

        self.run_job("Проверка SHA256, HELLO и STATUS без игрового ввода…", job)

    def reset_board(self) -> None:
        try:
            volume = self.require_volume("bootloader")
            board = self.board.currentData()
            if not self.erase_ack.isChecked():
                self.report("Для полного сброса отметьте подтверждение удаления данных Pico")
                return
            if not self.confirm("Полное удаление памяти Pico", f"Диск: {volume.root}; модель: {BOARDS[board]}.\n"
                                "ВСЯ flash-память этой платы будет стёрта: прошивка, файлы, настройки и NVM.\n"
                                "Это не сброс настроек Windows. Продолжить?"):
                return

            def job() -> dict:
                flash_runtime(volume, board, erase=True)
                return {"message": "UF2 полного сброса отправлен. Дождитесь возврата в BOOTSEL, "
                        "обновите список, установите CircuitPython, затем HID Pilot."}

            self.run_job("Полный сброс flash-памяти Pico…", job)
        except ValueError as exc:
            self.report(str(exc))

    def open_backups(self) -> None:
        directory = user_data_dir() / "pico-backups"
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)

    def reject(self) -> None:
        if self.busy:
            self.report("Дождитесь завершения записи или проверки Pico")
            return
        super().reject()

    def closeEvent(self, event: object) -> None:
        if self.busy:
            self.report("Дождитесь завершения записи или проверки Pico")
            event.ignore()
        else:
            event.accept()
