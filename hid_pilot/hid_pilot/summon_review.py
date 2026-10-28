"""
Подтверждение полос и иконки самона без изменения остальных областей профиля.
"""

import numpy as np
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout, QWidget

from hid_pilot.auto_review import ReviewCanvas
from hid_pilot.bars import BarBox, SummonProfile
from hid_pilot.summon import calibrate_summon


class SummonReview(QDialog):
    def __init__(self, image: np.ndarray, initial: SummonProfile | None, parent: QWidget,
                 suggested_boxes: dict[str, BarBox] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Самон / пет — HP, MP и иконка панели")
        self.resize(1100, 750)
        self.profile = initial
        self.image = image
        self.boxes = {name: getattr(initial, name) for name in ("hp", "mp", "icon")} if initial else {}
        if suggested_boxes is not None:
            self.boxes = suggested_boxes.copy()
        layout = QVBoxLayout(self)
        self.hint = QLabel("На панели самона над чатом выделите полные полосы HP и MP "
                           "(включая пустую часть, без рамки), затем иконку лапы слева. "
                           "Иконка нужна, чтобы отличать исчезновение панели от нулевого HP.")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.mode = QComboBox()
        for title, name in (("HP самона", "hp"), ("MP самона", "mp"), ("Иконка лапы", "icon")):
            self.mode.addItem(title, name)
        layout.addWidget(self.mode)
        if suggested_boxes is not None:
            self.mode.setCurrentIndex(2)
        self.canvas = ReviewCanvas(image)
        self.canvas.boxes = self.boxes.copy()
        self.canvas.selected.connect(self.choose)
        layout.addWidget(self.canvas, 1)
        clear = QPushButton("Отключить наблюдение самона")
        clear.clicked.connect(self.clear)
        layout.addWidget(clear)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def choose(self, box: BarBox) -> None:
        self.boxes[self.mode.currentData()] = box
        self.canvas.boxes = self.boxes.copy()
        self.canvas.update()
        self.mode.setCurrentIndex(min(2, self.mode.currentIndex() + 1))

    def clear(self) -> None:
        self.profile = None
        self.accept()

    def save(self) -> None:
        if set(self.boxes) != {"hp", "mp", "icon"}:
            self.hint.setText("Выделите все три области: HP, MP и иконку лапы самона")
            return
        try:
            self.profile = calibrate_summon(self.image, **self.boxes)
        except ValueError as exc:
            self.hint.setText(str(exc))
            return
        self.accept()
