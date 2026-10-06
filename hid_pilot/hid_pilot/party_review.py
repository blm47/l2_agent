"""
Ручная разметка первой строки пати и интервалов между участниками.
"""

from itertools import pairwise

import numpy as np
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hid_pilot.auto_party import find_party
from hid_pilot.auto_review import ReviewCanvas
from hid_pilot.bars import BarBox
from hid_pilot.party import PartyProfile

FIELDS = {"name": "Имя первого участника", "cp": "CP первого участника",
          "hp": "HP первого участника", "mp": "MP первого участника"}


class PartyReview(QDialog):
    def __init__(self, image: np.ndarray, profile: PartyProfile | None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(QFont("Segoe UI", 10))
        self.setWindowTitle("Пати — ручная корректировка сверху вниз")
        self.resize(1200, 900)
        self.image = image
        self.regions: dict[str, BarBox] = {}
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Выделите мышью ВСЮ панель пати: от первого имени до MP последнего участника. "
            "Полосы питомцев без CP не считаются участниками.\n"
            "Затем при необходимости выделите отдельно имя и полную длину CP/HP/MP первого "
            "участника, включая пустую часть. Проверьте рамки всех восьми строк.\n"
            "Шаг — расстояние между обычными строками. Дополнительный отступ после участника "
            "учитывает его питомца; например, после 4 и 7. Эти настройки относятся к текущему "
            "расположению: при смене состава или появлении питомца проверьте разметку снова."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.field = QComboBox()
        self.field.addItem("Вся панель пати (найти строки в выделении)", "panel")
        for key, title in FIELDS.items():
            self.field.addItem(title, key)
        form.addRow("Выделяем мышью", self.field)
        self.step = QSpinBox()
        self.step.setRange(20, 500)
        self.step.setValue(70)
        form.addRow("Обычный шаг, px", self.step)
        layout.addLayout(form)
        gap_layout = QHBoxLayout()
        self.gaps: list[QSpinBox] = []
        for slot in range(7):
            column = QVBoxLayout()
            column.addWidget(QLabel(f"После {slot + 1}, +px"))
            spin = QSpinBox()
            spin.setRange(0, image.shape[0])
            spin.setToolTip("Дополнительная высота питомца или промежутка после участника")
            spin.valueChanged.connect(self.update_preview)
            self.gaps.append(spin)
            column.addWidget(spin)
            gap_layout.addLayout(column)
        layout.addLayout(gap_layout)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.canvas = ReviewCanvas(image)
        self.canvas.selected.connect(self.select_region)
        layout.addWidget(self.canvas, 1)
        zoom_row = QHBoxLayout()
        for title, callback in (("Показать всё окно", self.show_full),
                                ("Приблизить разметку пати", self.zoom_party)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            zoom_row.addWidget(button)
        layout.addLayout(zoom_row)
        self.step.valueChanged.connect(self.update_preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                  QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if profile is not None:
            self.load_profile(profile)
            self.zoom_party()
        else:
            self.update_preview()

    def load_profile(self, profile: PartyProfile) -> None:
        self.regions = {kind: getattr(profile, kind) for kind in FIELDS}
        offsets = profile.row_offsets or [i * profile.row_step for i in range(8)]
        step = min(500, min(b - a for a, b in pairwise(offsets)))
        self.step.setValue(step)
        for index, spin in enumerate(self.gaps):
            spin.setValue(offsets[index + 1] - offsets[index] - step)
        self.update_preview()

    def profile(self) -> PartyProfile:
        if set(self.regions) != set(FIELDS):
            raise ValueError("Выделите всю панель либо задайте имя, CP, HP и MP первого участника")
        offsets = [0]
        for spin in self.gaps:
            offsets.append(offsets[-1] + self.step.value() + spin.value())
        height, width = self.image.shape[:2]
        return PartyProfile(width=width, height=height, **self.regions,
                            row_step=self.step.value(), row_offsets=offsets)

    def select_region(self, box: BarBox) -> None:
        key = self.field.currentData()
        if key == "panel":
            candidates = find_party(self.image, None, box)
            if len(candidates) != 1:
                self.status.setText("Не удалось однозначно найти пати в выделении. "
                                    "Уточните область либо задайте имя и CP/HP/MP вручную.")
                return
            self.load_profile(candidates[0].profile)
            self.zoom_party()
        else:
            self.regions[key] = box
            self.update_preview()

    def update_preview(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self.canvas.boxes = {FIELDS[key]: box for key, box in self.regions.items()}
        try:
            profile = self.profile()
            self.canvas.boxes = {f"{slot + 1}: {kind}": profile.box(kind, slot)
                                 for slot in range(8) for kind in FIELDS}
            self.status.setText("Рамки 1–8 идут сверху вниз; питомцы должны оставаться между строками.")
        except ValueError as exc:
            self.status.setText(str(exc))
        self.canvas.update()

    def show_full(self) -> None:
        self.canvas.view_box = BarBox(x1=0, y1=0, x2=1, y2=1)
        self.canvas.update()

    def zoom_party(self) -> None:
        try:
            profile = self.profile()
        except ValueError:
            return
        boxes = [profile.box(kind, slot) for slot in range(8) for kind in FIELDS]
        self.canvas.view_box = BarBox(
            x1=max(0, min(b.x1 for b in boxes) - .02),
            x2=min(1, max(b.x2 for b in boxes) + .02),
            y1=max(0, min(b.y1 for b in boxes) - .02),
            y2=min(1, max(b.y2 for b in boxes) + .02))
        self.canvas.update()

    def save(self) -> None:
        try:
            self.profile()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.accept()
