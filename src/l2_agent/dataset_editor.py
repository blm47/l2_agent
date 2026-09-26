import logging
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget,
)

from l2_agent.dataset import (
    DETECTOR_CLASSES, TAXONOMY_VERSION, ObjectAnnotation, load_detector_sample, save_annotations,
)
from l2_agent.roi import NormalizedBox
from l2_agent.roi_editor import RoiCanvas

logger = logging.getLogger("l2_agent.dataset_editor")


class ObjectCanvas(RoiCanvas):
    def __init__(self) -> None:
        super().__init__()
        self.objects: list[ObjectAnnotation] = []
        self.editable = True

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        rect = self.image_rect()
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#70ff90"), 2))
        for index, obj in enumerate(self.objects):
            box = obj.box
            bounds = QRectF(
                rect.left() + box.x1 * rect.width(), rect.top() + box.y1 * rect.height(),
                (box.x2 - box.x1) * rect.width(), (box.y2 - box.y1) * rect.height(),
            )
            painter.drawRect(bounds)
            painter.drawText(QPointF(bounds.left(), max(12, bounds.top() - 3)),
                             f"{index + 1}: {obj.kind}")
        painter.end()


class DatasetEditor(QDialog):
    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.sample, payload = load_detector_sample(path)
        self.setWindowTitle("M1 — разметка объектов")
        self.resize(1100, 800)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Новый объект: выберите класс и обведите. Смена класса: выберите рамку в списке "
            "и нажмите «Применить класс»."
        ))
        controls = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItems(DETECTOR_CLASSES)
        controls.addWidget(self.kind)
        self.selected = QComboBox()
        for label, value in (("Выбор цели неизвестен", None), ("Выбранная цель", True),
                             ("Не выбранная цель", False)):
            self.selected.addItem(label, value)
        controls.addWidget(self.selected)
        apply_class = QPushButton("Применить класс к выбранной рамке")
        apply_class.clicked.connect(self.reclassify_object)
        controls.addWidget(apply_class)
        remove = QPushButton("Удалить выбранную рамку")
        remove.clicked.connect(self.remove_object)
        controls.addWidget(remove)
        layout.addLayout(controls)
        self.canvas = ObjectCanvas()
        self.canvas.set_image(QImage.fromData(payload, "PNG"))
        self.canvas.objects = list(self.sample.objects)
        self.canvas.box_selected.connect(self.add_object)
        layout.addWidget(self.canvas, 1)
        self.objects = QListWidget()
        self.objects.setMaximumHeight(100)
        layout.addWidget(self.objects)
        self.reviewed = QCheckBox("Все объекты размечены; пустой список означает отсутствие объектов")
        layout.addWidget(self.reviewed)
        self.status = QLabel()
        self.status.setWordWrap(True)
        if self.sample.taxonomy_version != TAXONOMY_VERSION:
            self.status.setText(
                "Старая схема классов: уточните классы существующих рамок и добавьте "
                "пропущенные объекты перед подтверждением."
            )
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_objects()

    def refresh_objects(self) -> None:
        self.objects.clear()
        for index, obj in enumerate(self.canvas.objects):
            self.objects.addItem(f"{index + 1}: {obj.kind}; selected={obj.selected}")
        self.reviewed.setChecked(False)
        self.canvas.update()

    def add_object(self, box: NormalizedBox) -> None:
        self.canvas.objects.append(ObjectAnnotation(
            kind=self.kind.currentText(), box=box, selected=self.object_selected(),
        ))
        self.refresh_objects()

    def object_selected(self) -> bool | None:
        if self.kind.currentText() == "mob_selected":
            return True
        return self.selected.currentData()

    def remove_object(self) -> None:
        index = self.objects.currentRow()
        if index >= 0:
            del self.canvas.objects[index]
            self.refresh_objects()

    def reclassify_object(self) -> None:
        index = self.objects.currentRow()
        if index < 0:
            self.status.setText("Сначала выберите рамку в списке.")
            return
        self.canvas.objects[index] = ObjectAnnotation(
            kind=self.kind.currentText(), box=self.canvas.objects[index].box,
            selected=self.object_selected(),
        )
        self.refresh_objects()
        self.objects.setCurrentRow(index)

    def save(self) -> None:
        if not self.reviewed.isChecked():
            self.status.setText("Подтвердите, что разметка кадра завершена.")
            return
        try:
            save_annotations(self.path, self.sample, self.canvas.objects)
        except (OSError, ValueError) as exc:
            logger.warning("Не удалось сохранить разметку: %s", exc)
            self.status.setText(str(exc))
            return
        self.accept()
