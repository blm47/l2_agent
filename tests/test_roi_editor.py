import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from l2_agent.roi import NormalizedBox, RoiProfile
from l2_agent.roi_editor import RoiCanvas, RoiEditor


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_canvas_maps_only_image_and_accepts_reverse_drag(app):
    canvas = RoiCanvas()
    canvas.editable = True
    canvas.resize(600, 600)
    image = QImage(1000, 500, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.black)
    canvas.set_image(image)
    spy = QSignalSpy(canvas.box_selected)
    canvas.show()
    app.processEvents()
    try:
        assert canvas.normalized_point(QPointF(50, 50)) is None
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(540, 420))
        QTest.mouseMove(canvas, QPoint(60, 180))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(60, 180))
        assert spy.count() == 1
        box = spy.at(0)[0]
        assert (box.x1, box.y1, box.x2, box.y2) == pytest.approx((0.1, 0.1, 0.9, 0.9))
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(60, 180))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(60, 50))
        assert spy.count() == 1
        canvas.editable = False
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(300, 300))
        assert spy.count() == 1
    finally:
        canvas.close()


def test_editor_add_remove_and_resize_profile(app):
    image = QImage(800, 600, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.black)
    old = RoiProfile(
        frame_width=1000, frame_height=500, bars={"hp": NormalizedBox(x1=0, y1=0, x2=0.1, y2=0.1)}
    )
    editor = RoiEditor(image, old)
    try:
        assert not editor.profile().bars
        box = NormalizedBox(x1=0.1, y1=0.1, x2=0.5, y2=0.15)
        editor.select_box(box)
        assert editor.profile().bars == {"hp": box}
        assert editor.profile().matches(800, 600)
        assert old.bars["hp"] != box
        editor.remove_box()
        assert not editor.profile().bars
    finally:
        editor.close()
