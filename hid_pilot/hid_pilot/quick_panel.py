import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hid_pilot.loops import KeyLoop
from hid_pilot.script import KEYS


class QuickPanel(QWidget):
    requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[QCheckBox, QComboBox, QSpinBox, QSpinBox, QPushButton]] = []
        self.cancelled: dict[int, threading.Event] = {}
        layout = QVBoxLayout(self)
        group = QGroupBox("Нажатия клавиш в выбранном окне")
        grid = QGridLayout(group)
        for column, name in enumerate(("Вкл.", "Клавиша", "Пауза, мс", "Повторы", "")):
            grid.addWidget(QLabel(name), 0, column)
        for index, (key, interval) in enumerate(zip(("R", "1", "2", "3"),
                                                  (5000, 5000, 1200, 900))):
            enabled = QCheckBox()
            enabled.setChecked(index == 0)
            keys = QComboBox()
            for name in sorted(KEYS):
                keys.addItem(name, name)
            keys.addItem("ЛКМ", "LMB")
            keys.addItem("ПКМ", "RMB")
            keys.setCurrentText(key)
            delay = QSpinBox()
            delay.setRange(150, 3_600_000)
            delay.setValue(interval)
            repeats = QSpinBox()
            repeats.setRange(-1, 100_000)
            repeats.setValue(-1)
            repeats.valueChanged.connect(
                lambda value, box=repeats: box.setValue(1) if value == 0 else None
            )
            repeats.setToolTip("-1 — до STOP (лимит запуска 1 час); 1 и выше — число нажатий")
            button = QPushButton("Старт")
            button.clicked.connect(lambda _=False, row=index: self.start_row(row))
            controls = (enabled, keys, delay, repeats, button)
            self.rows.append(controls)
            for column, control in enumerate(controls):
                grid.addWidget(control, index + 1, column)
        layout.addWidget(group)
        self.start_all = QPushButton("Старт отмеченных строк")
        self.start_all.clicked.connect(self.start_selected)
        layout.addWidget(self.start_all)
        hint = QLabel(
            "-1 — повторять до остановки. Пауза отсчитывается после отпускания клавиши.\n"
            "Строки работают со своими интервалами; нажатия не перекрываются.\n"
            "ЛКМ/ПКМ нажимаются в текущей точке курсора внутри выбранного окна.\n"
            "\\ — остановить всё. При переключении окна — автопауза; при возврате — продолжение."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()

    def plan(self, row: int) -> KeyLoop:
        _, key, interval, repeats, _ = self.rows[row]
        return KeyLoop(row=row, key=key.currentData(), interval_ms=interval.value(),
                       repeats=repeats.value())

    def start_row(self, row: int) -> None:
        if row in self.cancelled:
            self.cancelled[row].set()
            self.rows[row][-1].setEnabled(False)
        else:
            self.requested.emit((self.plan(row),))

    def start_selected(self) -> None:
        self.requested.emit(tuple(self.plan(i) for i, row in enumerate(self.rows)
                                  if row[0].isChecked()))

    def set_running(self, plans: tuple[KeyLoop, ...] | None) -> None:
        self.cancelled = {plan.row: threading.Event() for plan in plans or ()}
        self.start_all.setEnabled(plans is None)
        for index, row in enumerate(self.rows):
            for control in row[:-1]:
                control.setEnabled(plans is None)
            row[-1].setText("Стоп" if index in self.cancelled else "Старт")
            row[-1].setEnabled(plans is None or index in self.cancelled)
