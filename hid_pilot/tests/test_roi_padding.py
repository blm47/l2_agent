import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QDialogButtonBox

from hid_pilot.auto_review import AutoReview, ReviewCanvas
from hid_pilot.bars import BarBox


def test_padding_uses_original_width_and_preserves_height():
    box = BarBox(x1=.3, y1=.1, x2=.5, y2=.2)
    padded = box.expanded_horizontal(.2, .2)
    assert padded.x1 == pytest.approx(.26)
    assert padded.x2 == pytest.approx(.54)
    assert (padded.y1, padded.y2) == (box.y1, box.y2)
    name = box.expanded_horizontal(0., 1.)
    assert name.x1 == box.x1
    assert name.x2 == pytest.approx(.7)
    assert box.expanded_horizontal(10., 10.) == BarBox(x1=0, y1=.1, x2=1, y2=.2)


@pytest.mark.parametrize("margin", [-1., float("nan"), float("inf")])
def test_invalid_padding_is_rejected(margin):
    box = BarBox(x1=.1, y1=.1, x2=.5, y2=.2)
    with pytest.raises(ValueError):
        box.expanded_horizontal(margin, 0.)
    with pytest.raises(ValueError):
        box.expanded_horizontal(0., margin)


def test_manual_name_preview_and_saved_box_include_padding_once(monkeypatch):
    app = QApplication.instance() or QApplication([])
    review = AutoReview(np.zeros((200, 400, 3), dtype=np.uint8), [], None)
    box = BarBox(x1=.3, y1=.1, x2=.5, y2=.2)
    padded = box.expanded_horizontal(0., 1.)

    def draw(dialog):
        canvas = dialog.findChild(ReviewCanvas)
        canvas.selected.emit(box)
        assert list(canvas.boxes.values()) == [padded]
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", draw)
    review.edit_player_panel()
    assert review.target_player_panel == padded
    monkeypatch.setattr(QDialog, "exec", lambda dialog: QDialog.DialogCode.Accepted)
    review.edit_player_panel()
    assert review.target_player_panel == padded
    review.close()
    app.processEvents()


def test_target_panel_editor_pads_name_and_hp_but_not_death_marker(monkeypatch):
    app = QApplication.instance() or QApplication([])
    review = AutoReview(np.zeros((200, 400, 3), dtype=np.uint8), [], None)
    box = BarBox(x1=.3, y1=.1, x2=.5, y2=.2)

    def draw(dialog):
        canvas = dialog.findChild(ReviewCanvas)
        mode = dialog.findChild(QComboBox)
        for index, key, expected in (
            (0, "target_panel", box.expanded_horizontal(0., 1.)),
            (1, "target_dead_marker", box),
            (2, "target_hp_presence_box", box.expanded_horizontal(.2, .2)),
        ):
            mode.setCurrentIndex(index)
            canvas.selected.emit(box)
            assert canvas.boxes[key] == expected
        # Маркер проверяем на рамке, но не сохраняем без реального снимка черепа.
        review.configure_target = lambda panel, names, marker, hp: (
            setattr(review, "target_player_panel", panel),
            setattr(review, "boxes", {"target_hp": hp}),
        )
        dialog.findChild(QDialogButtonBox).accepted.emit()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", draw)
    review.edit_target_panel()
    assert review.target_player_panel == box.expanded_horizontal(0., 1.)
    assert review.boxes["target_hp"] == box.expanded_horizontal(.2, .2)
    review.close()
    app.processEvents()
