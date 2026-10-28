import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication, QComboBox, QDialog

from hid_pilot.auto_review import AutoReview, ReviewCanvas
from hid_pilot.bars import BarBox, BarProfile


def test_manual_stat_and_level_draw_save_and_cancel(monkeypatch):
    app = QApplication.instance() or QApplication([])
    image = np.zeros((200, 400, 3), dtype=np.uint8)
    box = BarBox(x1=.1, y1=.1, x2=.5, y2=.15)
    profile = BarProfile(width=400, height=200, numbers={"hp": box})
    review = AutoReview(image, [], None, initial_profile=profile)

    def accept(dialog):
        mode = dialog.findChild(QComboBox)
        canvas = dialog.findChild(ReviewCanvas)
        mode.setCurrentIndex(mode.findData("own_level"))
        canvas.selected.emit(box)
        mode.setCurrentIndex(mode.findData("mp"))
        canvas.selected.emit(box)
        mode.setCurrentIndex(mode.findData("target_panel"))
        canvas.selected.emit(box)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    review.edit_own_regions()
    padded = box.expanded_horizontal(.2, .2)
    name = box.expanded_horizontal(0., 1.)
    assert review.boxes == {"hp": box, "mp": padded}
    assert review.level_boxes["own_level"] == padded
    assert review.target_config["target_panel"] == name.model_dump()
    assert review.target_player_panel == name
    monkeypatch.setattr(QDialog, "exec", lambda dialog: QDialog.DialogCode.Accepted)
    review.edit_own_regions()
    assert review.boxes == {"hp": box, "mp": padded}
    assert review.target_player_panel == name
    monkeypatch.setattr(QDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    review.edit_own_regions()
    assert review.boxes == {"hp": box, "mp": padded}
    review.close()
    app.processEvents()


def test_level_ocr_rejects_ambiguous_and_missing_values():
    box = BarBox(x1=.1, y1=.1, x2=.5, y2=.15)
    profile = BarProfile(width=400, height=200, own_level=box)
    image = np.zeros((200, 400, 3), dtype=np.uint8)
    reader = Mock()
    reader.detect.return_value = [(None, "52", .95)]
    assert profile.read_level(image, "own_level", reader) == 52
    reader.detect.return_value = [(None, "52", .95), (None, "53", .95)]
    assert profile.read_level(image, "own_level", reader) is None
    reader.detect.return_value = [(None, "52", .5)]
    assert profile.read_level(image, "own_level", reader) is None
    assert profile.read_level(image, "target_level", reader) is None


def test_level_setup_opens_target_level_and_preserves_own_level(monkeypatch):
    app = QApplication.instance() or QApplication([])
    box = BarBox(x1=.1, y1=.1, x2=.5, y2=.15)
    image = np.zeros((200, 400, 3), dtype=np.uint8)
    review = AutoReview(image, [], None, initial_profile=BarProfile(width=400, height=200, own_level=box))

    def accept(dialog):
        assert dialog.findChild(QComboBox).currentData() == "target_level"
        dialog.findChild(ReviewCanvas).selected.emit(box)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    review.edit_own_regions("target_level")
    assert review.level_boxes == {"own_level": box, "target_level": box.expanded_horizontal(.2, .2)}
    review.close()
    app.processEvents()
