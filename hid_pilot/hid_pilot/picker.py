"""
Снимок поверх клиентской области: клик выбирает цвет, drag — ROI полосы.
"""

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QDialog

from hid_pilot.bars import BarBox


def color_command(bgr: np.ndarray, x: int, y: int) -> str:
    height, width = bgr.shape[:2]
    if not 0 <= x < width or not 0 <= y < height:
        raise ValueError("Точка вне снимка")
    blue, green, red = (int(value) for value in bgr[y, x])
    return f"color {x / (width - 1):.6f} {y / (height - 1):.6f} {red:02X}{green:02X}{blue:02X} 15"


class ScreenPicker(QDialog):
    def __init__(self, bgr: np.ndarray, rect: tuple[int, int, int, int],
                 bar: str | None = None) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.bgr = bgr
        height, width = bgr.shape[:2]
        self.image = QImage(bgr.data, width, height, bgr.strides[0],
                            QImage.Format.Format_BGR888).copy()
        self.setGeometry(rect[0], rect[1], width, height)
        self.bar = bar
        self.start: QPoint | None = None
        self.selection = QRect()
        self.command: str | None = None
        self.box: BarBox | None = None

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.drawImage(self.rect(), self.image)
        painter.setPen(QPen(Qt.GlobalColor.yellow, 2))
        painter.drawRect(self.selection)
        painter.fillRect(10, 10, min(self.width() - 20, 850), 40, Qt.GlobalColor.black)
        text = (f"Выделите ПОЛНУЮ длину {self.bar}, включая пустую часть; Esc — отмена"
                if self.bar else "Клик: вставить цвет и координаты в скрипт. Esc — отмена")
        if self.bar == "target_zero":
            text = "На мёртвой цели выделите только надпись 0 / максимум HP; Esc — отмена"
        if self.bar and self.bar.startswith("numbers_"):
            text = "Выделите только всю надпись текущее / максимум, с небольшим отступом; Esc — отмена"
        painter.drawText(20, 35, text)

    def pixel(self, event: QMouseEvent) -> QPoint:
        return QPoint(max(0, min(self.image.width() - 1,
                                 round(event.position().x() * self.image.width() / self.width()))),
                      max(0, min(self.image.height() - 1,
                                 round(event.position().y() * self.image.height() / self.height()))))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self.pixel(event)
        if self.bar is None:
            self.command = color_command(self.bgr, point.x(), point.y())
            self.accept()
        else:
            self.start = point

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.start is not None:
            self.selection = QRect(self.start, self.pixel(event)).normalized()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.bar is None or self.start is None:
            return
        selection = QRect(self.start, self.pixel(event)).normalized()
        if selection.width() < 20 or selection.height() < 3:
            self.start = None
            return
        self.box = BarBox(x1=selection.left() / self.image.width(),
                          y1=selection.top() / self.image.height(),
                          x2=(selection.right() + 1) / self.image.width(),
                          y2=(selection.bottom() + 1) / self.image.height())
        self.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
