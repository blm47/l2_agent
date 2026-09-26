from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from l2_agent.roi import BAR_LABELS, BarName, NormalizedBox, RoiProfile

COLORS = {"hp": "#ff6060", "mp": "#609fff", "cp": "#ffdc50", "target_hp": "#ff90ee"}


class RoiCanvas(QLabel):
    box_selected = Signal(object)

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.boxes: dict[BarName, NormalizedBox] = {}
        self.bar_text: dict[BarName, str] = {}
        self.editable = False
        self._start: QPointF | None = None
        self._end: QPointF | None = None
        self._source: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

    def set_image(self, image: QImage) -> None:
        self._source = QPixmap.fromImage(image)
        self._scale_image()

    def _scale_image(self) -> None:
        if self._source is not None:
            self.setPixmap(
                self._source.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._start = self._end = None
        self._scale_image()
        super().resizeEvent(event)

    def image_rect(self) -> QRectF:
        pixmap = self.pixmap()
        if pixmap.isNull():
            return QRectF()
        size = pixmap.deviceIndependentSize()
        return QRectF(
            (self.width() - size.width()) / 2,
            (self.height() - size.height()) / 2,
            size.width(),
            size.height(),
        )

    def normalized_point(self, point: QPointF) -> QPointF | None:
        rect = self.image_rect()
        if rect.isEmpty() or not rect.contains(point):
            return None
        return QPointF(
            (point.x() - rect.left()) / rect.width(), (point.y() - rect.top()) / rect.height()
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.editable and event.button() == Qt.MouseButton.LeftButton:
            self._start = self.normalized_point(event.position())
            self._end = self._start
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._start is not None:
            self._end = self.normalized_point(event.position())
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.editable and event.button() == Qt.MouseButton.LeftButton:
            end = self.normalized_point(event.position())
            start = self._start
            self._start = self._end = None
            if start is not None and end is not None:
                rect = self.image_rect()
                if (
                    abs(start.x() - end.x()) * rect.width() >= 2
                    and abs(start.y() - end.y()) * rect.height() >= 2
                ):
                    self.box_selected.emit(
                        NormalizedBox(
                            x1=min(start.x(), end.x()),
                            y1=min(start.y(), end.y()),
                            x2=max(start.x(), end.x()),
                            y2=max(start.y(), end.y()),
                        )
                    )
            self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        rect = self.image_rect()
        if rect.isEmpty():
            return
        painter = QPainter(self)
        for name, box in self.boxes.items():
            painter.setPen(QPen(QColor(COLORS[name]), 2))
            bounds = QRectF(
                rect.left() + box.x1 * rect.width(),
                rect.top() + box.y1 * rect.height(),
                (box.x2 - box.x1) * rect.width(),
                (box.y2 - box.y1) * rect.height(),
            )
            painter.drawRect(bounds)
            painter.drawText(
                QPointF(bounds.left(), max(12, bounds.top() - 3)),
                self.bar_text.get(name, BAR_LABELS[name]),
            )
        if self._start is not None and self._end is not None:
            painter.setPen(QPen(QColor("white"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(
                QRectF(
                    QPointF(
                        rect.left() + self._start.x() * rect.width(),
                        rect.top() + self._start.y() * rect.height(),
                    ),
                    QPointF(
                        rect.left() + self._end.x() * rect.width(),
                        rect.top() + self._end.y() * rect.height(),
                    ),
                ).normalized()
            )
        painter.end()


class RoiEditor(QDialog):
    def __init__(
        self, image: QImage, profile: RoiProfile | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("M1 — области полос на стоп-кадре")
        self.resize(1100, 750)
        self.frame_width, self.frame_height = image.width(), image.height()
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Выберите полосу и обведите её ПОЛНУЮ внутреннюю длину, включая пустую часть.\n"
            "Не включайте рамку, подписи и соседние полосы. Мышь здесь только размечает изображение."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        controls = QHBoxLayout()
        self.selector = QComboBox()
        for name, title in BAR_LABELS.items():
            self.selector.addItem(title, name)
        controls.addWidget(self.selector)
        remove = QPushButton("Удалить выбранную область")
        remove.clicked.connect(self.remove_box)
        controls.addWidget(remove)
        layout.addLayout(controls)
        self.canvas = RoiCanvas()
        self.canvas.editable = True
        self.canvas.set_image(image)
        if profile is not None and profile.matches(image.width(), image.height()):
            self.canvas.boxes = dict(profile.bars)
        elif profile is not None:
            layout.addWidget(QLabel("Размер кадра изменился: задайте области заново."))
        self.canvas.box_selected.connect(self.select_box)
        layout.addWidget(self.canvas, 1)
        self.status = QLabel()
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_status()

    def select_box(self, box: NormalizedBox) -> None:
        self.canvas.boxes[self.selector.currentData()] = box
        self.canvas.update()
        self.update_status()

    def remove_box(self) -> None:
        self.canvas.boxes.pop(self.selector.currentData(), None)
        self.canvas.update()
        self.update_status()

    def update_status(self) -> None:
        names = ", ".join(BAR_LABELS[name] for name in self.canvas.boxes) or "нет"
        self.status.setText(f"Кадр: {self.frame_width} × {self.frame_height}. Размечены: {names}")

    def profile(self) -> RoiProfile:
        return RoiProfile(
            frame_width=self.frame_width,
            frame_height=self.frame_height,
            bars=dict(self.canvas.boxes),
        )
