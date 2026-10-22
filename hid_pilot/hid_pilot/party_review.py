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
from hid_pilot.party_class import icon_pixels
from hid_pilot.bars import BarProfile

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
        self.class_icon: BarBox | None = None
        self.class_templates: dict[str, list[list[int]]] = {}
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
        self.field.addItem("CP первого участника (перенести всю разметку)", "anchor")
        for key, title in FIELDS.items():
            self.field.addItem(title, key)
        self.field.addItem("Иконка класса первого участника (весь значок)", "class_icon")
        self.field.addItem("Эталон класса: выделить значок любого участника", "class_reference")
        form.addRow("Выделяем мышью", self.field)
        self.reference_class = QComboBox()
        self.reference_class.addItems(["Elven Elder", "Shilien Elder"])
        form.addRow("Класс выделяемого эталона", self.reference_class)
        self.class_status = QLabel("Эталоны классов не заданы; распознавание выключено")
        self.class_status.setWordWrap(True)
        form.addRow(self.class_status)
        clear_class = QPushButton("Удалить эталон выбранного класса")
        clear_class.clicked.connect(self.clear_class_reference)
        form.addRow(clear_class)
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
        self.class_icon = profile.class_icon
        self.class_templates = {name: [row.copy() for row in pixels]
                                for name, pixels in profile.class_templates.items()}
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
                            row_step=self.step.value(), row_offsets=offsets,
                            class_icon=self.class_icon, class_templates=self.class_templates)

    def select_region(self, box: BarBox) -> None:
        key = self.field.currentData()
        if key == "class_icon":
            self.class_icon = box
            self.update_preview()
            return
        if key == "class_reference":
            height, width = self.image.shape[:2]
            try:
                crop = BarProfile(width=width, height=height).crop(self.image, box)
                self.class_templates[self.reference_class.currentText()] = icon_pixels(crop)
            except ValueError as exc:
                self.status.setText(str(exc))
                return
            self.update_preview()
            return
        if key == "panel":
            candidates = find_party(self.image, None, box)
            if len(candidates) != 1:
                self.status.setText("Не удалось однозначно найти пати в выделении. "
                                    "Уточните область либо задайте имя и CP/HP/MP вручную.")
                return
            self.load_profile(candidates[0].profile)
            self.zoom_party()
        else:
            if key == "anchor":
                previous = self.regions.get("cp")
                if previous is None:
                    self.status.setText("Сначала найдите панель или задайте полосы вручную.")
                    return
                dx, dy = box.x1 - previous.x1, box.y1 - previous.y1
                try:
                    shifted = {kind: BarBox(x1=value.x1 + dx, x2=value.x2 + dx,
                                           y1=value.y1 + dy, y2=value.y2 + dy)
                               for kind, value in self.regions.items()}
                except ValueError:
                    self.status.setText("Перенос выводит области за границы окна; уточните выделение.")
                    return
                shifted["cp"] = box
                if self.class_icon is not None:
                    icon = self.class_icon
                    try:
                        self.class_icon = BarBox(x1=icon.x1 + dx, x2=icon.x2 + dx,
                                                 y1=icon.y1 + dy, y2=icon.y2 + dy)
                    except ValueError:
                        self.status.setText("Перенос иконки выходит за границы окна")
                        return
                self.regions = shifted
                self.update_preview()
                return
            self.regions[key] = box
            self.update_preview()

    def update_preview(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self.canvas.boxes = {FIELDS[key]: box for key, box in self.regions.items()}
        self.class_status.setText("Эталоны: " + ", ".join(self.class_templates) if self.class_templates
                                  else "Эталоны классов не заданы; распознавание выключено")
        try:
            profile = self.profile()
            self.canvas.boxes = {f"{slot + 1}: {kind}": profile.box(kind, slot)
                                 for slot in range(8) for kind in FIELDS}
            if profile.class_icon is not None:
                self.canvas.boxes.update({f"{slot + 1}: класс": profile.box("class_icon", slot)
                                          for slot in range(8)})
            self.status.setText("Рамки 1–8 идут сверху вниз; питомцы должны оставаться между строками.")
        except ValueError as exc:
            self.status.setText(str(exc))
        self.canvas.update()

    def clear_class_reference(self) -> None:
        self.class_templates.pop(self.reference_class.currentText(), None)
        self.update_preview()

    def show_full(self) -> None:
        self.canvas.view_box = BarBox(x1=0, y1=0, x2=1, y2=1)
        self.canvas.update()

    def zoom_party(self) -> None:
        try:
            profile = self.profile()
        except ValueError:
            return
        boxes = [profile.box(kind, slot) for slot in range(8) for kind in FIELDS]
        if profile.class_icon is not None:
            boxes.extend(profile.box("class_icon", slot) for slot in range(8))
        self.canvas.view_box = BarBox(
            x1=max(0, min(b.x1 for b in boxes) - .02),
            x2=min(1, max(b.x2 for b in boxes) + .02),
            # Ошибочно пропущенная первая строка должна быть доступна для коррекции.
            y1=max(0, min(b.y1 for b in boxes) - profile.row_step / profile.height - .02),
            y2=min(1, max(b.y2 for b in boxes) + .02))
        self.canvas.update()

    def save(self) -> None:
        try:
            self.profile()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.accept()
