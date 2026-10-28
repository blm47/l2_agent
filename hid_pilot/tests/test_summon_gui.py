import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import pytest
from PySide6.QtWidgets import QDialog, QPushButton

from hid_pilot.bars import BarProfile
from hid_pilot.summon import calibrate_summon
from hid_pilot.summon_review import SummonReview
from test_duo_gui import window
from test_summon import summon_profile, summon_scene


def test_dialog_saves_three_confirmed_regions_and_rejects_incomplete_selection(window):
    gui, _, _, _ = window
    image, boxes = summon_scene()
    dialog = SummonReview(image, None, gui)
    dialog.save()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.profile is None
    for name in ("hp", "mp", "icon"):
        assert dialog.mode.currentData() == name
        dialog.canvas.selected.emit(boxes[name])
    dialog.save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.profile == summon_profile().summon
    initial = dialog.profile.model_copy(deep=True)
    rejected = SummonReview(image, dialog.profile, gui)
    rejected.canvas.selected.emit(boxes["mp"])
    rejected.reject()
    assert dialog.profile == initial
    dialog.close()
    rejected.close()


def test_summon_buttons_use_the_matching_role(window):
    gui, _, _, _ = window
    gui.auto_numbers = Mock()
    for panel in gui.duo.panels.values():
        button = next(b for b in panel.findChildren(QPushButton) if "самона" in b.text())
        button.click()
        gui.auto_numbers.assert_called_with(panel=panel, manual_summon=True)
    gui.party.summon_requested.emit()
    gui.auto_numbers.assert_called_with(manual_summon=True)


@pytest.mark.parametrize("action", ["save", "cancel", "remove", "geometry", "script"])
def test_summon_setup_preserves_other_rois_and_validates_context(window, monkeypatch, action):
    gui, _, farmer, _ = window
    panel = gui.duo.panels["farmer"]
    path = panel.path().parent / "farm-bars.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    image, boxes = summon_scene()
    image = cv2.resize(image, (100, 100))
    regions = calibrate_summon(image, **boxes)
    profile = BarProfile(width=100, height=100, bars={"hp": boxes["hp"]},
                         numbers={"mp": boxes["mp"]}, summon=regions if action == "remove" else None)
    path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    before = path.read_bytes()
    gui.auto_busy = True
    monkeypatch.setattr("hid_pilot.__main__.snapshot", lambda hwnd: farmer)

    def edit(dialog):
        dialog.profile = None if action == "remove" else regions
        if action == "geometry":
            changed = farmer.model_copy(update={"rect": (0, 0, 90, 100)})
            monkeypatch.setattr("hid_pilot.__main__.snapshot", lambda hwnd: changed)
        elif action == "script":
            gui.duo.path = gui.duo.path.with_name("other.duo.pilot")
        return QDialog.DialogCode.Rejected if action == "cancel" else QDialog.DialogCode.Accepted

    monkeypatch.setattr(SummonReview, "exec", edit)
    gui.review_summon({"generation": gui.auto_generation, "image": image, "target": farmer,
                       "path": panel.path(), "panel": panel})
    saved = BarProfile.model_validate_json(path.read_text("utf-8"))
    assert saved.bars == profile.bars
    assert saved.numbers == profile.numbers
    if action in {"cancel", "geometry", "script"}:
        assert path.read_bytes() == before
    else:
        assert saved.summon == (None if action == "remove" else regions)
    assert not gui.auto_busy
    assert gui.auto_search.isEnabled()


def test_setup_captures_after_window_activation_without_ocr(window, monkeypatch):
    gui, _, farmer, _ = window
    panel = gui.duo.panels["farmer"]
    image, _ = summon_scene()
    desktop = Mock()
    desktop.capture.return_value = image
    monkeypatch.setattr("hid_pilot.__main__.activate", lambda target: target)
    monkeypatch.setattr("hid_pilot.__main__.Desktop", lambda target: desktop)
    callbacks = []
    monkeypatch.setattr("hid_pilot.__main__.QTimer.singleShot", lambda delay, callback: callbacks.append(callback))
    gui.review_summon = Mock()
    gui.auto_numbers(panel=panel, manual_summon=True)
    desktop.capture.assert_not_called()
    callbacks[0]()
    desktop.capture.assert_called_once_with()
    payload = gui.review_summon.call_args.args[0]
    assert payload["target"] == farmer
    assert payload["panel"] is panel
    assert payload["image"] is image
    gui.auto_busy = False


def test_live_duo_status_shows_summon_resources(window):
    gui, _, _, _ = window
    gui.duo.show_observation({"hp": 1., "target_hp": .8, "summon_hp": .4, "summon_mp": .75})
    assert "Самон: HP 40%, MP 75%" in gui.duo.live_status.text()
