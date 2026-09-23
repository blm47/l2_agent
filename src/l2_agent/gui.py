import logging
import time

import pywintypes
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from l2_agent.action_controller import ActionController
from l2_agent.capture import CaptureWorker
from l2_agent.hard_stop import HardStopHotkey
from l2_agent.windows import ParsecWindowManager, WindowSnapshot

logger = logging.getLogger("l2_agent.gui")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.manager = ParsecWindowManager()
        self.capture = CaptureWorker()
        self.controller = ActionController()
        self.hard_stop = HardStopHotkey(lambda: self.controller.stop("EMERGENCY — F10"))
        self.controller.hard_stop_ready = lambda: self.hard_stop.available
        self._input_target: tuple[int, int] | None = None
        self._pending_epoch = -1
        self._pending_test = ""
        self._shown_sequence = -1
        self.setWindowTitle("L2 Agent — M0 / preview и ручной ввод")
        self.resize(900, 750)
        body = QWidget()
        layout = QVBoxLayout(body)
        self.selector = QComboBox()
        refresh = QPushButton("Обновить список окон")
        refresh.clicked.connect(self.refresh_windows)
        layout.addWidget(self.selector)
        layout.addWidget(refresh)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.preview_enabled = QCheckBox("Включить preview")
        self.preview_enabled.setChecked(True)
        self.preview_enabled.toggled.connect(self.update_status)
        layout.addWidget(self.preview_enabled)
        self.capture_status = QLabel("Ожидание кадра | FPS: 0")
        self.capture_status.setWordWrap(True)
        layout.addWidget(self.capture_status)
        self.preview = QLabel("Ожидание кадра")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 180)
        self._preview_size = self.preview.size()
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.preview, 1)
        layout.addWidget(QLabel("WorldState: —\nЦель: —\nТекущий skill: —"))
        self.input_status = QLabel()
        self.input_status.setWordWrap(True)
        layout.addWidget(self.input_status)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("START")
        self.start_button.clicked.connect(self.start_input)
        self.pause_button = QPushButton("PAUSE")
        self.pause_button.clicked.connect(lambda: self.stop_input("PAUSE"))
        self.stop_button = QPushButton("STOP")
        self.stop_button.clicked.connect(lambda: self.stop_input("STOP"))
        for button in (self.start_button, self.pause_button, self.stop_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addWidget(
            QLabel("M0: START разрешает только выбранный ручной тест. Hard-stop: F10.")
        )
        self.test_selector = QComboBox()
        self.test_selector.addItems(
            [
                "Курсор в центр Parsec",
                "Клавиша R — 100 мс",
                "Удержать W — 1000 мс",
                "ЛКМ в текущей точке — 100 мс",
                "ПКМ в текущей точке — 1000 мс",
            ]
        )
        layout.addWidget(self.test_selector)
        self.test_button = QPushButton("Выполнить через 3 секунды")
        self.test_button.clicked.connect(self.schedule_test)
        layout.addWidget(self.test_button)
        self.test_status = QLabel("Выберите тест, затем переключитесь в Parsec во время отсчёта.")
        self.test_status.setWordWrap(True)
        layout.addWidget(self.test_status)
        self.test_timer = QTimer(self)
        self.test_timer.setSingleShot(True)
        self.test_timer.setInterval(3000)
        self.test_timer.timeout.connect(self.run_test)
        self.setCentralWidget(body)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.update_status)
        self.selector.currentIndexChanged.connect(self.update_status)
        self.refresh_windows()
        self.timer.start()
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(33)
        self.preview_timer.timeout.connect(self.update_preview)
        self.preview_timer.start()

    def refresh_windows(self) -> None:
        previous = self.selector.currentData()
        self.selector.blockSignals(True)
        self.selector.clear()
        try:
            for window in self.manager.discover():
                self.selector.addItem(f"{window.title or 'Parsec'} — HWND {window.hwnd}", window)
            if previous is not None:
                for index in range(self.selector.count()):
                    candidate = self.selector.itemData(index)
                    if (candidate.hwnd, candidate.pid) == (previous.hwnd, previous.pid):
                        self.selector.setCurrentIndex(index)
                        break
        except pywintypes.error:
            logger.exception("Не удалось получить список окон Parsec")
        finally:
            self.selector.blockSignals(False)
        self.update_status()

    def update_status(self) -> None:
        selected: WindowSnapshot | None = self.selector.currentData()
        target = (selected.hwnd, selected.pid) if selected else None
        if self.controller.armed and self._input_target != target:
            self.stop_input("STOP — окно изменилось")
        self.update_input_status()
        if selected is None:
            self.capture.select(None)
            self.status.setText("IDLE: окна Parsec не найдены. Откройте окно и обновите список.")
            return
        try:
            current = self.manager.snapshot(selected.hwnd, selected.pid)
        except (pywintypes.error, OSError, ValueError):
            if self.controller.armed:
                self.stop_input("STOP — окно недоступно")
            self.capture.select(None)
            self.status.setText("IDLE: окно недоступно. Обновите список окон.")
            return
        if current.rect is None:
            if self.controller.armed:
                self.stop_input("STOP — окно свёрнуто")
            self.capture.select(None)
            self.status.setText("IDLE: окно свёрнуто или клиентская область пуста.")
            return
        rect = current.rect
        self.capture.select(
            (current.hwnd, current.pid) if self.preview_enabled.isChecked() else None
        )
        self.status.setText(
            f"Parsec | HWND: {current.hwnd} | PID: {current.pid}\n"
            f"Клиентская область: {rect.width} × {rect.height}\n"
            f"Экранные координаты: ({rect.left}, {rect.top}) — ({rect.right}, {rect.bottom})\n"
            f"Фокус: {'Parsec' if current.focused else 'другое окно'}"
        )

    def update_preview(self) -> None:
        status = self.capture.latest()
        frame = status.frame
        if frame is None:
            self.preview.clear()
            self.preview.setText(status.message)
            self._shown_sequence = -1
            self.capture_status.setText(f"{status.message} | FPS: {status.fps:.0f}")
            return
        age = max(0, time.monotonic() - frame.timestamp)
        self.capture_status.setText(
            f"{status.message} | FPS: {status.fps:.0f} | Возраст кадра: {age:.1f} с"
        )
        if frame.sequence == self._shown_sequence and self.preview.size() == self._preview_size:
            return
        height, width, _ = frame.rgb.shape
        image = QImage(
            frame.rgb.data, width, height, frame.rgb.strides[0], QImage.Format.Format_RGB888
        )
        self.preview.setPixmap(
            QPixmap.fromImage(image).scaled(
                self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self._shown_sequence = frame.sequence
        self._preview_size = self.preview.size()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.test_timer.stop()
        self.controller.close()
        self.hard_stop.close()
        self.timer.stop()
        self.preview_timer.stop()
        self.capture.select(None)
        self.capture.close()
        super().closeEvent(event)

    def update_input_status(self) -> None:
        ready = self.hard_stop.available
        hotkey = "F10 зарегистрирована" if ready else f"НЕДОСТУПЕН: {self.hard_stop.error}"
        self.input_status.setText(f"Ввод: {self.controller.state}\nHard-stop: {hotkey}")
        self.start_button.setEnabled(ready and self.selector.currentData() is not None)
        self.test_button.setEnabled(self.controller.armed and not self.test_timer.isActive())
        if self.test_timer.isActive() and self._pending_epoch != self.controller.epoch:
            self.test_timer.stop()
            self.test_status.setText("Тест отменён остановкой ввода.")

    def start_input(self) -> None:
        selected = self.selector.currentData()
        if selected is None:
            return
        try:
            self._input_target = (selected.hwnd, selected.pid)
            self.controller.arm(self._input_target)
            self.test_status.setText(
                "Выберите тест. Нажатие START само по себе не отправляет ввод."
            )
        except Exception as exc:
            logger.exception("Не удалось разрешить ручной ввод")
            self.test_status.setText(str(exc))
        self.update_input_status()

    def stop_input(self, reason: str) -> None:
        self.test_timer.stop()
        self.controller.stop(reason)
        self.test_status.setText("Ручной тест остановлен.")
        self.update_input_status()

    def schedule_test(self) -> None:
        if not self.controller.armed or self.test_timer.isActive():
            return
        self._pending_epoch = self.controller.epoch
        self._pending_test = self.test_selector.currentText()
        self.test_status.setText(f"Через 3 секунды: {self._pending_test}. Переключитесь в Parsec.")
        self.test_timer.start()
        self.update_input_status()

    def run_test(self) -> None:
        if not self.controller.armed or self._pending_epoch != self.controller.epoch:
            self.test_status.setText("Тест отменён остановкой ввода.")
            return
        try:
            if self._pending_test.startswith("Курсор"):
                self.controller.mouse_move_absolute_client(0.5, 0.5)
            elif self._pending_test.startswith("Клавиша R"):
                self.controller.key_down("R", 100)
            elif self._pending_test.startswith("Удержать W"):
                self.controller.key_down("W", 1000)
            elif self._pending_test.startswith("ЛКМ"):
                self.controller.mouse_button_down("left", 100)
            elif self._pending_test.startswith("ПКМ"):
                self.controller.mouse_button_down("right", 1000)
            else:
                raise ValueError("Неизвестный ручной тест")
            self.test_status.setText(
                "Ввод отправлен; удержание ограничено watchdog. Проверьте игру."
            )
        except Exception as exc:
            logger.exception("Ручной тест не выполнен")
            self.test_status.setText(f"Тест отклонён: {exc}")
        self.update_input_status()
