import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QImage, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hid_pilot.auto_numbers import NumberCandidate
from hid_pilot.auto_party import PartyCandidate
from hid_pilot.auto_player import PlayerCandidate
from hid_pilot.bars import BarBox
from hid_pilot.party import PartyProfile

LABELS = {"hp": "Моё HP", "mp": "Моё MP", "cp": "Моё CP", "target_hp": "HP цели"}


class ReviewCanvas(QWidget):
    selected = Signal(object)

    def __init__(self, image: np.ndarray) -> None:
        super().__init__()
        height, width = image.shape[:2]
        self.image = QImage(image.data, width, height, image.strides[0],
                            QImage.Format.Format_BGR888).copy()
        self.boxes: dict[str, BarBox] = {}
        self.view_box = BarBox(x1=0, y1=0, x2=1, y2=1)
        self.start: QPointF | None = None
        self.drag_end: QPointF | None = None
        self.editable = True
        self.setMinimumSize(400, 240)

    def viewport(self) -> QRectF:
        width = self.image.width() * (self.view_box.x2 - self.view_box.x1)
        height = self.image.height() * (self.view_box.y2 - self.view_box.y1)
        scale = min(self.width() / width, self.height() / height)
        width, height = width * scale, height * scale
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def normalized(self, point: QPointF) -> QPointF:
        area = self.viewport()
        view = self.view_box
        return QPointF(view.x1 + max(0., min(1., (point.x() - area.left()) / area.width()))
                       * (view.x2 - view.x1),
                       view.y1 + max(0., min(1., (point.y() - area.top()) / area.height()))
                       * (view.y2 - view.y1))

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        area = self.viewport()
        view = self.view_box
        painter.drawImage(area, self.image, QRectF(
            view.x1 * self.image.width(), view.y1 * self.image.height(),
            (view.x2 - view.x1) * self.image.width(),
            (view.y2 - view.y1) * self.image.height()))
        painter.setClipRect(area)

        def mapped(x: float, y: float) -> QPointF:
            return QPointF(area.left() + (x - view.x1) / (view.x2 - view.x1) * area.width(),
                           area.top() + (y - view.y1) / (view.y2 - view.y1) * area.height())

        for name, box in self.boxes.items():
            kind = name.rsplit(": ", 1)[-1]
            color = {"cp": Qt.GlobalColor.yellow, "hp": Qt.GlobalColor.red,
                     "mp": Qt.GlobalColor.cyan, "name": Qt.GlobalColor.green}.get(
                         kind, Qt.GlobalColor.yellow)
            painter.setPen(QPen(color, 1))
            rect = QRectF(mapped(box.x1, box.y1), mapped(box.x2, box.y2))
            painter.drawRect(rect)
            if not name.endswith((": cp", ": hp", ": mp")):
                painter.drawText(rect.topLeft() - QPointF(0, 4),
                                 LABELS.get(name, name.removesuffix(": name")))
        if self.start is not None and self.drag_end is not None:
            painter.setPen(QPen(Qt.GlobalColor.yellow, 2))
            painter.drawRect(QRectF(
                mapped(self.start.x(), self.start.y()),
                mapped(self.drag_end.x(), self.drag_end.y())).normalized())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.editable and event.button() == Qt.MouseButton.LeftButton and self.viewport().contains(event.position()):
            self.start = self.normalized(event.position())
            self.drag_end = self.start

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.start is not None:
            self.drag_end = self.normalized(event.position())
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        end = self.normalized(event.position())
        x1, x2 = sorted((self.start.x(), end.x()))
        y1, y2 = sorted((self.start.y(), end.y()))
        self.start = None
        self.drag_end = None
        self.update()
        if (x2 - x1) * self.image.width() >= 8 and (y2 - y1) * self.image.height() >= 3:
            self.selected.emit(BarBox(x1=x1, y1=y1, x2=x2, y2=y2))


class AutoReview(QDialog):
    def __init__(self, image: np.ndarray, candidates: list[NumberCandidate], parent: QWidget,
                 party_candidates: list[PartyCandidate] | None = None,
                 player_candidates: list[PlayerCandidate] | None = None) -> None:
        super().__init__(parent)
        self.setFont(QFont("Segoe UI", 10))
        self.setWindowTitle("Автопоиск HP/MP/CP, цели и пати — проверка")
        self.resize(1100, 750)
        self.source_image = image
        self.boxes: dict[str, BarBox] = {}
        self.choices: dict[str, QComboBox] = {}
        self.target_player_panel: BarBox | None = None
        layout = QVBoxLayout(self)
        hint = QLabel("Проверьте HP, MP, CP, цель и область пати на снимке.\n"
                      "Для пати проверьте первого участника сверху, полную длину полос и отступы для питомцев.\n"
                      "Кнопка ручной корректировки позволяет выделить панель и исправить разметку всех строк.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        for name, title in LABELS.items():
            choice = QComboBox()
            choice.addItem("Не найдено / не использовать", None)
            for candidate in candidates:
                n = candidate.numbers
                choice.addItem(f"{n.current}/{n.maximum} · OCR {n.confidence:.0%} · {candidate.evidence}",
                               candidate.box)
            suggestions = [i + 1 for i, candidate in enumerate(candidates) if candidate.kind == name]
            if len(suggestions) == 1:
                choice.setCurrentIndex(suggestions[0])
            self.choices[name] = choice
            choice.currentIndexChanged.connect(self.update_boxes)
            form.addRow(title, choice)
        self.party_choice = QComboBox()
        self.party_choice.addItem("Не найдено / не использовать", None)
        for candidate in party_candidates or []:
            self.party_choice.addItem(candidate.evidence, candidate.profile)
        self.party_step = QSpinBox()
        self.party_step.setRange(20, 500)
        self.party_step.setValue(70)
        self.party_choice.currentIndexChanged.connect(self.update_party)
        self.party_step.valueChanged.connect(self.update_boxes)
        form.addRow("Пати", self.party_choice)
        form.addRow("Шаг участников, px", self.party_step)
        self.party_edit = QPushButton("Ручная корректировка пати…")
        self.party_edit.clicked.connect(self.edit_party)
        form.addRow(self.party_edit)
        self.player_choice = QComboBox()
        self.player_choice.addItem("Не найдено / не использовать", None)
        for candidate in player_candidates or []:
            self.player_choice.addItem(candidate.evidence, candidate.box)
        self.player_choice.currentIndexChanged.connect(self.choose_player_panel)
        form.addRow("Выбранный игрок: ник / класс / клан", self.player_choice)
        self.player_edit = QPushButton("Панель выбранного игрока: ник / класс / клан…")
        self.player_edit.clicked.connect(self.edit_player_panel)
        form.addRow(self.player_edit)
        self.party_error = QLabel()
        form.addRow(self.party_error)
        layout.addLayout(form)
        self.canvas = ReviewCanvas(image)
        self.canvas.editable = False
        layout.addWidget(self.canvas, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                  QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if len(party_candidates or []) == 1:
            self.party_choice.setCurrentIndex(1)
        self.update_boxes()
        if len(player_candidates or []) == 1:
            self.player_choice.setCurrentIndex(1)

    def choose_player_panel(self) -> None:
        self.target_player_panel = self.player_choice.currentData()
        self.update_boxes()

    def update_boxes(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self.boxes = {name: choice.currentData() for name, choice in self.choices.items()
                      if choice.currentData() is not None}
        self.canvas.boxes = self.boxes.copy()
        if self.target_player_panel is not None:
            self.canvas.boxes["Игрок: ник / класс / клан"] = self.target_player_panel
        try:
            party = self.party_profile()
            self.party_error.clear()
            if party:
                regions = [party.box(kind, slot) for kind in ("name", "cp", "hp", "mp")
                           for slot in range(8)]
                self.canvas.boxes["Пати (до 8 участников)"] = BarBox(
                    x1=min(b.x1 for b in regions), x2=max(b.x2 for b in regions),
                    y1=min(b.y1 for b in regions), y2=max(b.y2 for b in regions))
                for slot in range(8):
                    for kind in ("name", "cp", "hp", "mp"):
                        self.canvas.boxes[f"Пати {slot + 1}: {kind}"] = party.box(kind, slot)
        except ValueError as exc:
            self.party_error.setText(str(exc))
        self.canvas.update()

    def edit_player_panel(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("OCR выделенного игрока")
        dialog.resize(1000, 650)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Выделите прямоугольник с ником, классом и кланом сверху вниз. "
                                "Не включайте эмблему, значки и кнопку закрытия."))
        canvas = ReviewCanvas(self.source_image)
        layout.addWidget(canvas, 1)
        selected = [self.target_player_panel]

        def choose(box: BarBox) -> None:
            selected[0] = box
            canvas.boxes = {"Игрок": box}
            canvas.update()

        canvas.selected.connect(choose)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.target_player_panel = selected[0]
            self.update_boxes()

    def update_party(self) -> None:
        party = self.party_choice.currentData()
        if party:
            self.party_step.setValue(party.row_step)
        self.party_step.setEnabled(party is not None)
        self.update_boxes()

    def party_profile(self) -> PartyProfile | None:
        party = self.party_choice.currentData()
        if party is None:
            return None
        offsets = party.row_offsets
        if offsets:
            offsets = [offset + slot * (self.party_step.value() - party.row_step)
                       for slot, offset in enumerate(offsets)]
        return PartyProfile.model_validate(party.model_dump() | {
            "row_step": self.party_step.value(), "row_offsets": offsets})

    def edit_party(self) -> None:
        from hid_pilot.party_review import PartyReview

        try:
            profile = self.party_profile()
        except ValueError:
            profile = self.party_choice.currentData()
        dialog = PartyReview(self.source_image, profile, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.party_choice.addItem("Ручная разметка пати", dialog.profile())
            self.party_choice.setCurrentIndex(self.party_choice.count() - 1)

    def save(self) -> None:
        try:
            self.party_profile()
        except ValueError as exc:
            self.party_error.setText(str(exc))
            return
        self.accept()
