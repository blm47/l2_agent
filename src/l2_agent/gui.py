import logging
import time
from pathlib import Path

import pywintypes
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from l2_agent.action_controller import ActionController
from l2_agent.bar_detection import BarProposal
from l2_agent.bar_parser import perceive_bars
from l2_agent.bar_worker import BarDetectionWorker
from l2_agent.capture import CapturedFrame, CaptureWorker
from l2_agent.dataset import DEFAULT_DATASET_PATH, save_detector_sample
from l2_agent.dataset_editor import DatasetEditor
from l2_agent.hard_stop import HARD_STOP_KEY, HardStopHotkey
from l2_agent.pico import PicoTransport
from l2_agent.roi import BAR_LABELS, DEFAULT_ROI_PATH, RoiProfile, load_profile, save_profile
from l2_agent.roi_editor import RoiCanvas, RoiEditor
from l2_agent.target_ocr import TargetNameReader
from l2_agent.windows import GameWindowManager, WindowSnapshot
from l2_agent.world_state import WorldState

logger = logging.getLogger("l2_agent.gui")

KEY_TESTS = {
    "Клавиша R — 100 мс": ("R", 100),
    "Клавиша R — 300 мс": ("R", 300),
    **{f"Цифра {key} (верхний ряд) — 100 мс": (key, 100) for key in "1234"},
    **{
        f"Удержать {key} — {duration} мс": (key, duration)
        for key in "1234"
        for duration in (300, 1000)
    },
    "Удержать W — 100 мс": ("W", 100),
    "Удержать W — 1000 мс": ("W", 1000),
}

MOUSE_TESTS = {
    f"{label} в текущей точке — {duration} мс": (button, duration)
    for label, button in (("ЛКМ", "left"), ("ПКМ", "right"))
    for duration in (100, 300, 1000)
}


class MainWindow(QMainWindow):
    def __init__(self, roi_path: Path = DEFAULT_ROI_PATH, pico_port: str | None = None) -> None:
        super().__init__()
        self.roi_path = roi_path
        self.dataset_path = DEFAULT_DATASET_PATH
        self._dataset_target: tuple[int, int] | None = None
        self.world_state: WorldState | None = None
        self._world_target: tuple[int, int] | None = None
        self.roi_profile = None
        self.roi_error = ""
        self.bar_worker = BarDetectionWorker()
        self.target_reader = TargetNameReader()
        self._auto_key: tuple[int, int, int, int] | None = None
        self._auto_generation = 0
        self._auto_busy = False
        self._auto_last_search = float("-inf")
        self._auto_force = False
        self._auto_blocked = False
        self._proposals: list[BarProposal] = []
        self._proposal_index = 0
        try:
            self.roi_profile = load_profile(roi_path)
        except (OSError, ValueError) as exc:
            self.roi_error = f"Не удалось загрузить ROI: {exc}"
            logger.exception("Ошибка загрузки профиля ROI")
        self.manager = GameWindowManager()
        self.capture = CaptureWorker()
        self.controller = ActionController(pico=PicoTransport(pico_port) if pico_port else None)
        self.hard_stop = HardStopHotkey(
            lambda: self.controller.stop(f"EMERGENCY — {HARD_STOP_KEY}")
        )
        self.controller.hard_stop_ready = lambda: self.hard_stop.available
        self._input_target: tuple[int, int] | None = None
        self._pending_epoch = -1
        self._pending_test = ""
        self._shown_sequence = -1
        self.setWindowTitle("L2 Agent — M1 / настройка восприятия")
        self.resize(1000, 900)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(QLabel(f"Ввод: {self.controller.backend_name}"))
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
        self.preview = RoiCanvas("Ожидание кадра")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 180)
        self._preview_size = self.preview.size()
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.preview, 1)
        self.auto_bars = QCheckBox("Искать полосы автоматически")
        self.auto_bars.setChecked(True)
        self.auto_bars.toggled.connect(self.toggle_auto_bars)
        layout.addWidget(self.auto_bars)
        self.search_bars_button = QPushButton("Повторить автоматический поиск")
        self.search_bars_button.clicked.connect(self.retry_auto_bars)
        layout.addWidget(self.search_bars_button)
        self.auto_question = QLabel("Ожидание кадра для поиска полос")
        self.auto_question.setWordWrap(True)
        layout.addWidget(self.auto_question)
        self.target_bar_selector = QComboBox()
        self.target_bar_selector.addItem("HP цели: не определён", None)
        self.target_bar_selector.currentIndexChanged.connect(self.preview_target_option)
        self.target_bar_selector.setEnabled(False)
        layout.addWidget(self.target_bar_selector)
        answers = QHBoxLayout()
        self.confirm_bars_button = QPushButton("Да, это мои полосы и их полная длина")
        self.confirm_bars_button.clicked.connect(self.confirm_auto_bars)
        self.reject_bars_button = QPushButton("Нет, другой вариант")
        self.reject_bars_button.clicked.connect(self.reject_auto_bars)
        for button in (self.confirm_bars_button, self.reject_bars_button):
            button.setEnabled(False)
            answers.addWidget(button)
        layout.addLayout(answers)
        self.roi_button = QPushButton("Ручная корректировка областей (необязательно)")
        self.roi_button.setEnabled(False)
        self.roi_button.clicked.connect(self.configure_rois)
        layout.addWidget(self.roi_button)
        self.roi_status = QLabel(self.roi_error or "ROI: области ещё не настроены")
        self.roi_status.setWordWrap(True)
        layout.addWidget(self.roi_status)
        self.world_status = QLabel("WorldState: нет свежего кадра")
        self.world_status.setWordWrap(True)
        layout.addWidget(self.world_status)
        self.dataset_button = QPushButton("Сохранить кадр для разметки через 3 секунды")
        self.dataset_button.clicked.connect(self.schedule_dataset_sample)
        layout.addWidget(self.dataset_button)
        self.annotate_button = QPushButton("Разметить сохранённый кадр")
        self.annotate_button.clicked.connect(self.annotate_dataset_sample)
        layout.addWidget(self.annotate_button)
        self.dataset_status = QLabel("Датасет: кадры сохраняются локально, без автоматической разметки.")
        self.dataset_status.setWordWrap(True)
        layout.addWidget(self.dataset_status)
        self.dataset_timer = QTimer(self)
        self.dataset_timer.setSingleShot(True)
        self.dataset_timer.setInterval(3000)
        self.dataset_timer.timeout.connect(self.save_dataset_sample)
        layout.addWidget(QLabel("Цель: — | Текущий skill: —"))
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
            QLabel(f"M0: START разрешает только выбранный ручной тест. Hard-stop: {HARD_STOP_KEY}.")
        )
        self.test_selector = QComboBox()
        self.test_selector.addItems(
            [
                "Курсор в центр Lineage 2 / LU4 / Parsec",
                *KEY_TESTS,
                *MOUSE_TESTS,
            ]
        )
        layout.addWidget(self.test_selector)
        self.test_button = QPushButton("Выполнить через 3 секунды")
        self.test_button.clicked.connect(self.schedule_test)
        layout.addWidget(self.test_button)
        self.test_status = QLabel(
            "Выберите тест, затем переключитесь в Lineage 2 / LU4 / Parsec во время отсчёта."
        )
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

    def annotate_dataset_sample(self) -> None:
        self.dataset_timer.stop()
        self._dataset_target = None
        self.stop_input("PAUSE — разметка объектов")
        filename, _ = QFileDialog.getOpenFileName(
            self, "Выберите sample.json", str(self.dataset_path), "Метаданные (sample.json)"
        )
        if not filename:
            return
        try:
            editor = DatasetEditor(Path(filename), self)
            try:
                if editor.exec() == QDialog.DialogCode.Accepted:
                    self.dataset_status.setText(f"Разметка сохранена: {filename}")
            finally:
                editor.deleteLater()
        except (OSError, ValueError) as exc:
            logger.warning("Не удалось открыть кадр для разметки: %s", exc)
            self.dataset_status.setText(f"Не удалось открыть кадр: {exc}")

    def schedule_dataset_sample(self) -> None:
        selected = self.selector.currentData()
        if selected is None or not self.preview_enabled.isChecked():
            self.dataset_status.setText("Выберите окно игры и включите preview.")
            return
        self.stop_input("PAUSE — сбор датасета")
        self._dataset_target = (selected.hwnd, selected.pid)
        self.dataset_status.setText("Через 3 секунды сохраню кадр. Переключитесь в игру.")
        self.dataset_timer.start()

    def save_dataset_sample(self) -> None:
        target = self._dataset_target
        self._dataset_target = None
        self.dataset_timer.stop()
        try:
            selected = self.selector.currentData()
            if (target is None or selected is None
                    or target != (selected.hwnd, selected.pid)
                    or not self.preview_enabled.isChecked()):
                raise ValueError("Сохранение отменено: окно или preview изменились")
            frame = self.capture.latest().frame
            if frame is None:
                raise ValueError("Нет свежего кадра игры")
            current = self.manager.snapshot(*target)
            self.update_world_state(frame)
            profile = None if self._proposals or self._auto_force else self.roi_profile
            if profile is not None and not profile.matches(frame.rect.width, frame.rect.height):
                profile = None
            path = save_detector_sample(
                frame, current, root=self.dataset_path,
                world_state=self.world_state, roi_profile=profile,
            )
            self.dataset_status.setText(f"Кадр сохранён: {path}")
        except (OSError, ValueError, pywintypes.error) as exc:
            logger.warning("Кадр датасета не сохранён: %s", exc)
            self.dataset_status.setText(f"Кадр не сохранён: {exc}")

    def refresh_windows(self) -> None:
        previous = self.selector.currentData()
        self.selector.blockSignals(True)
        self.selector.clear()
        try:
            for window in self.manager.discover():
                self.selector.addItem(
                    f"{window.title or 'Без заголовка'} | {window.executable_name} | "
                    f"PID {window.pid} | HWND {window.hwnd}",
                    window,
                )
            if previous is not None:
                for index in range(self.selector.count()):
                    candidate = self.selector.itemData(index)
                    if (candidate.hwnd, candidate.pid) == (previous.hwnd, previous.pid):
                        self.selector.setCurrentIndex(index)
                        break
        except pywintypes.error:
            logger.exception("Не удалось получить список окон Lineage 2 / LU4 / Parsec")
        finally:
            self.selector.blockSignals(False)
        self.update_status()

    def update_status(self) -> None:
        selected: WindowSnapshot | None = self.selector.currentData()
        target = (selected.hwnd, selected.pid) if selected else None
        if target != self._world_target:
            self.clear_world_state()
            self._world_target = target
        if self.controller.armed and self._input_target != target:
            self.stop_input("STOP — окно изменилось")
        self.update_input_status()
        if selected is None:
            self.clear_world_state()
            self.capture.select(None)
            self.status.setText(
                "IDLE: окна Lineage 2 / LU4 / Parsec не найдены. Откройте окно и обновите список."
            )
            return
        try:
            current = self.manager.snapshot(selected.hwnd, selected.pid)
        except (pywintypes.error, OSError, ValueError):
            self.clear_world_state()
            if self.controller.armed:
                self.stop_input("STOP — окно недоступно")
            self.capture.select(None)
            self.status.setText("IDLE: окно недоступно. Обновите список окон.")
            return
        if current.rect is None:
            self.clear_world_state()
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
            f"Lineage 2 / LU4 / Parsec | HWND: {current.hwnd} | PID: {current.pid}\n"
            f"Клиентская область: {rect.width} × {rect.height}\n"
            f"Экранные координаты: ({rect.left}, {rect.top}) — ({rect.right}, {rect.bottom})\n"
            f"Фокус: {'Lineage 2 / LU4 / Parsec' if current.focused else 'другое окно'}"
        )

    def update_preview(self) -> None:
        status = self.capture.latest()
        frame = status.frame
        if frame is None:
            self.clear_world_state()
            if self._auto_key is not None:
                self._auto_key = None
                self._auto_generation += 1
                self._proposals = []
            self.confirm_bars_button.setEnabled(False)
            self.reject_bars_button.setEnabled(False)
            self.roi_button.setEnabled(False)
            self.preview.boxes = {}
            self.preview.clear()
            self.preview.setText(status.message)
            self._shown_sequence = -1
            self.capture_status.setText(f"{status.message} | FPS: {status.fps:.0f}")
            return
        age = max(0, time.monotonic() - frame.timestamp)
        self.update_auto_bars(frame)
        self.roi_button.setEnabled(age <= 2)
        height, width, _ = frame.rgb.shape
        if self.roi_profile is not None and self.roi_profile.matches(width, height):
            self.preview.boxes = self.roi_profile.bars
            self.roi_status.setText(
                f"Настроено областей: {len(self.roi_profile.bars)}/4. Значения показаны в WorldState."
            )
        elif self.roi_profile is not None:
            self.preview.boxes = {}
            self.roi_status.setText("Размер кадра изменился — нужен новый поиск полос.")
        else:
            self.preview.boxes = {}
        if self._proposals:
            self.preview.boxes = self.proposed_profile().bars
            self.roi_status.setText("Гипотеза: подтвердите назначение и полную длину полос.")
        self.update_world_state(frame)
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

    def clear_world_state(self) -> None:
        self.target_reader.invalidate()
        self.world_state = None
        self.world_status.setText("WorldState: нет свежего кадра")
        self.world_status.setToolTip("")
        self.preview.bar_text = {}
        self.preview.update()

    def update_world_state(self, frame: CapturedFrame) -> None:
        profile = None if self._proposals or self._auto_force else self.roi_profile
        state = perceive_bars(frame, profile)
        state = state.model_copy(
            update={
                "target_name": self.target_reader.observe(
                    frame, profile, profile is not None, self._world_target
                )
            }
        )
        self.world_state = state
        observations = {
            "hp": state.player.hp,
            "mp": state.player.mp,
            "cp": state.player.cp,
            "target_hp": state.target_hp,
        }
        labels = {}
        details = []
        for name, observation in observations.items():
            value = "?" if observation.value is None else f"≈{observation.value:.0%}"
            labels[name] = f"{BAR_LABELS[name]}: {value}"
            details.append(
                f"{BAR_LABELS[name]}: confidence={observation.confidence:.2f}; {observation.reason}"
            )
        target_name = state.target_name
        self.world_status.setText(
            f"WorldState #{state.frame_id}: "
            + " | ".join(labels.values())
            + f"\nВыбранная цель: {target_name.value or '?'}"
        )
        details.append(f"Имя цели: confidence={target_name.confidence:.2f}; {target_name.reason}")
        self.world_status.setToolTip("\n".join(details))
        self.preview.bar_text = {} if self._proposals else labels
        self.preview.update()

    def configure_rois(self) -> None:
        frame = self.capture.latest().frame
        if frame is None or time.monotonic() - frame.timestamp > 2:
            self.roi_status.setText("Для настройки нужен свежий кадр Lineage 2 / LU4 / Parsec.")
            return
        self.stop_input("PAUSE — настройка ROI")
        self._auto_generation += 1
        self._proposals = []
        self._auto_blocked = True
        height, width, _ = frame.rgb.shape
        image = QImage(
            frame.rgb.data, width, height, frame.rgb.strides[0], QImage.Format.Format_RGB888
        ).copy()
        editor = RoiEditor(image, self.roi_profile, self)
        try:
            if editor.exec() == QDialog.DialogCode.Accepted:
                profile = editor.profile()
                save_profile(profile, self.roi_path)
                self.roi_profile = profile
                self.roi_error = ""
                self._shown_sequence = -1
                self.update_preview()
                self.preview.update()
                logger.info(
                    "Профиль ROI сохранён: %s; областей=%s", self.roi_path, len(profile.bars)
                )
        except (OSError, ValueError) as exc:
            self.roi_status.setText(f"Не удалось сохранить ROI: {exc}")
            logger.exception("Ошибка сохранения профиля ROI")
        finally:
            editor.deleteLater()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.dataset_timer.stop()
        self.test_timer.stop()
        self.controller.close()
        self.hard_stop.close()
        self.bar_worker.close()
        self.target_reader.close()
        self.timer.stop()
        self.preview_timer.stop()
        self.capture.select(None)
        self.capture.close()
        super().closeEvent(event)

    def retry_auto_bars(self) -> None:
        self.auto_bars.setChecked(True)
        self._auto_generation += 1
        self._proposals = []
        self._auto_force = True
        self._auto_blocked = False
        self._auto_last_search = float("-inf")
        self.auto_question.setText("Повторный поиск на следующем свежем кадре…")

    def toggle_auto_bars(self, enabled: bool) -> None:
        if enabled:
            self.retry_auto_bars()
        else:
            self._auto_generation += 1
            self._proposals = []
            self._shown_sequence = -1
            self.auto_question.setText("Автоматический поиск выключен.")

    def update_auto_bars(self, frame: CapturedFrame) -> None:
        # Фоновое уточнение не должно отменять запущенный пользователем тест ввода.
        if self.controller.armed:
            return
        selected = self.selector.currentData()
        if selected is None:
            return
        key = (selected.hwnd, selected.pid, frame.rect.width, frame.rect.height)
        if key != self._auto_key:
            self._auto_key = key
            self._auto_generation += 1
            self._proposals = []
            self._auto_blocked = False
            self._auto_last_search = float("-inf")
        result = self.bar_worker.take_result()
        if result is not None:
            self._auto_busy = False
            generation, observed, detection = result
            if (
                generation == self._auto_generation
                and observed.rect == frame.rect
                and self.auto_bars.isChecked()
            ):
                self._proposals = detection.proposals
                self._proposal_index = 0
                self.auto_question.setText(detection.message)
                if self._proposals:
                    self.stop_input("PAUSE — уточнение найденных полос")
                    self.show_proposal()
        valid_profile = self.roi_profile is not None and self.roi_profile.matches(key[2], key[3])
        if valid_profile and not self._auto_force and not self._proposals:
            self.auto_question.setText(
                "Используются ранее сохранённые области. При изменении UI повторите поиск."
            )
        if (
            self.auto_bars.isChecked()
            and (not valid_profile or self._auto_force)
            and not self._proposals
            and not self._auto_busy
            and not self._auto_blocked
            and time.monotonic() - self._auto_last_search >= 15
            and time.monotonic() - frame.timestamp <= 2
        ):
            self._auto_busy = self.bar_worker.submit(self._auto_generation, frame)
            if self._auto_busy:
                self._auto_last_search = time.monotonic()
                self.auto_question.setText("Ищу полосы и читаю подписи на кадре…")
        self.confirm_bars_button.setEnabled(bool(self._proposals))
        self.reject_bars_button.setEnabled(bool(self._proposals))
        self.target_bar_selector.setEnabled(
            bool(self._proposals and self._proposals[self._proposal_index].target_options)
        )

    def show_proposal(self) -> None:
        proposal = self._proposals[self._proposal_index]
        self.target_bar_selector.blockSignals(True)
        self.target_bar_selector.clear()
        self.target_bar_selector.addItem("HP цели: не определён / цель не выбрана", None)
        for index, box in enumerate(proposal.target_options):
            self.target_bar_selector.addItem(f"HP цели: красная полоса №{index + 1}", box)
        self.target_bar_selector.setEnabled(bool(proposal.target_options))
        self.target_bar_selector.blockSignals(False)
        self.auto_question.setText(
            f"Вариант {self._proposal_index + 1}/{len(self._proposals)}. Это полосы вашего персонажа?\n"
            + "; ".join(proposal.evidence)
        )
        self._shown_sequence = -1

    def proposed_profile(self) -> RoiProfile:
        proposal = self._proposals[self._proposal_index]
        bars = dict(proposal.profile.bars)
        target = self.target_bar_selector.currentData()
        if target is not None and target in proposal.target_options:
            bars["target_hp"] = target
        return RoiProfile(
            frame_width=proposal.profile.frame_width,
            frame_height=proposal.profile.frame_height,
            bars=bars,
        )

    def preview_target_option(self) -> None:
        self._shown_sequence = -1
        if self._proposals:
            self.update_preview()

    def confirm_auto_bars(self) -> None:
        if not self._proposals:
            return
        profile = self.proposed_profile()
        frame = self.capture.latest().frame
        selected = self.selector.currentData()
        if (
            frame is None
            or selected is None
            or time.monotonic() - frame.timestamp > 2
            or not profile.matches(frame.rect.width, frame.rect.height)
            or self._auto_key != (selected.hwnd, selected.pid, frame.rect.width, frame.rect.height)
        ):
            self.retry_auto_bars()
            return
        try:
            save_profile(profile, self.roi_path)
        except (OSError, ValueError):
            logger.exception("Не удалось сохранить подтверждение полос")
            self.auto_question.setText("Не удалось сохранить подтверждение. Проверьте лог.")
            return
        self.roi_profile = profile
        self._proposals = []
        self._auto_force = False
        self._auto_blocked = False
        self._shown_sequence = -1
        self.update_preview()
        logger.info(
            "Пользователь подтвердил автоматически найденные полосы: %s", list(profile.bars)
        )

    def reject_auto_bars(self) -> None:
        if not self._proposals:
            return
        self._proposal_index += 1
        if self._proposal_index < len(self._proposals):
            self.show_proposal()
        else:
            self._proposals = []
            self._auto_blocked = True
            self.auto_question.setText(
                "Подходящего варианта нет. Покажите панель персонажа и нажмите повторный поиск."
            )
        self._shown_sequence = -1
        self.update_preview()

    def update_input_status(self) -> None:
        ready = self.hard_stop.available
        hotkey = (
            f"{HARD_STOP_KEY} зарегистрирована" if ready else f"НЕДОСТУПЕН: {self.hard_stop.error}"
        )
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
        logger.info("Запланирован ручной тест: %s; цель=%s", self._pending_test, self._input_target)
        self.test_status.setText(
            f"Через 3 секунды: {self._pending_test}. Переключитесь в Lineage 2 / LU4 / Parsec."
        )
        self.test_timer.start()
        self.update_input_status()

    def run_test(self) -> None:
        if not self.controller.armed or self._pending_epoch != self.controller.epoch:
            self.test_status.setText("Тест отменён остановкой ввода.")
            return
        try:
            if self._pending_test.startswith("Курсор"):
                self.controller.mouse_move_absolute_client(0.5, 0.5)
            elif self._pending_test in KEY_TESTS:
                key, duration_ms = KEY_TESTS[self._pending_test]
                self.controller.key_down(key, duration_ms)
            elif self._pending_test in MOUSE_TESTS:
                button, duration_ms = MOUSE_TESTS[self._pending_test]
                self.controller.mouse_button_down(button, duration_ms)
            else:
                raise ValueError("Неизвестный ручной тест")
            self.test_status.setText(
                "Windows приняла ввод; реакция игры не подтверждена. Проверьте игру."
            )
        except Exception as exc:
            logger.exception("Ручной тест не выполнен")
            self.test_status.setText(f"Тест отклонён: {exc}")
        self.update_input_status()
