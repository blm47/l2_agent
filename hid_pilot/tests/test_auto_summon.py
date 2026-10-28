import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from hid_pilot.auto_review import AutoReview
from hid_pilot.auto_summon import find_summon_bars
from hid_pilot.bars import BarProfile
from hid_pilot.summon_review import SummonReview
from test_summon import summon_scene, summon_profile


def test_finds_pair_below_top_panels():
    image, boxes = summon_scene()
    assert find_summon_bars(image) == [{"hp": boxes["hp"], "mp": boxes["mp"]}]


@pytest.mark.parametrize("case", ["missing", "cp", "top", "misaligned"])
def test_rejects_incomplete_or_other_panels(case):
    image, _ = summon_scene()
    if case == "missing":
        image[130:138] = 20
    elif case == "cp":
        image[110:118, 60:260] = (30, 180, 180)
    elif case == "top":
        image[:40] = image[110:150]
        image[110:150] = 20
    else:
        image[130:138] = 20
        image[130:138, 80:280] = (195, 110, 55)
    assert find_summon_bars(image) == []


def test_auto_review_requires_icon_confirmation_and_keeps_saved_profile(monkeypatch):
    app = QApplication.instance() or QApplication([])
    image, boxes = summon_scene()
    original = summon_profile()
    review = AutoReview(image, [], None, initial_profile=original)
    assert review.summon == original.summon
    review.summon_choice.setCurrentIndex(1)

    def confirm(dialog):
        assert dialog.mode.currentData() == "icon"
        dialog.save()
        assert dialog.result() != QDialog.DialogCode.Accepted
        dialog.choose(boxes["icon"])
        dialog.save()
        return dialog.result()

    monkeypatch.setattr(SummonReview, "exec", confirm)
    review.edit_summon()
    assert review.summon == original.summon
    assert review.canvas.boxes["Самон / пет: hp"] == boxes["hp"]
    assert BarProfile.model_validate_json(BarProfile(width=400, height=200,
                                                   summon=review.summon).model_dump_json()).summon == review.summon
    monkeypatch.setattr(SummonReview, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    review.edit_summon()
    assert review.summon == original.summon
    monkeypatch.setattr(SummonReview, "exec", lambda dialog: (dialog.clear() or dialog.result()))
    review.edit_summon()
    assert review.summon is None
    assert original.summon is not None
    review.close()
