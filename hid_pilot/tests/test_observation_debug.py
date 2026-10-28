import json
from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.observation_debug import ObservationDebugRecorder


def recorder(monkeypatch, tmp_path):
    monkeypatch.setattr("hid_pilot.observation_debug.threading.Thread.start", lambda self: None)
    box = BarBox(x1=0, y1=0, x2=.5, y2=.2)
    profile = BarProfile(width=200, height=100, own_level=box, target_level=box, target_panel=box)
    return ObservationDebugRecorder(tmp_path, profile)


def test_target_loss_keeps_previous_frame_and_does_not_require_separate_script(monkeypatch, tmp_path):
    debug = recorder(monkeypatch, tmp_path)
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    debug.observe(image, {"hp": 1., "mp": 1., "own_level": 50,
                          "target": {"hp": .01, "name": "Mob", "level": 50}})
    assert debug.pending is None
    debug.observe(image + 1, {"hp": 1., "mp": 1., "own_level": 50, "target": {"hp": None}})
    before, failure, reasons = debug.pending
    assert reasons == ["target_hp_lost"]
    assert before[1]["target"]["hp"] == .01
    assert failure[0][0, 0, 0] == 1
    debug.write_event(*debug.pending)
    event = json.loads((tmp_path / "event-00/event.json").read_text("utf-8"))
    assert event["failure"]["captured_at"]
    assert (tmp_path / "event-00/before_window.jpg").exists()
    assert (tmp_path / "event-00/failure_target_level.png").exists()


def test_missing_levels_and_name_are_recorded_and_throttled(monkeypatch, tmp_path):
    debug = recorder(monkeypatch, tmp_path)
    monkeypatch.setattr("hid_pilot.observation_debug.time.monotonic", lambda: 0.)
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    state = {"hp": 1., "mp": 1., "own_level": None, "target": {"hp": .5}}
    debug.observe(image, state)
    assert set(debug.pending[2]) == {"own_level_unknown", "target_level_unknown", "target_name_unknown"}
    debug.pending = None
    debug.observe(image, state)
    assert debug.pending is None


def test_archive_reuses_fixed_slots(monkeypatch, tmp_path):
    debug = recorder(monkeypatch, tmp_path)
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    for index in range(34):
        debug.write_event(None, (image, {"sequence": index}), ["own_level_unknown"])
    assert len(list(tmp_path.iterdir())) == 32
    event = json.loads((tmp_path / "event-00/event.json").read_text("utf-8"))
    assert event["failure"]["sequence"] == 32


def test_party_failure_records_resources_profile_and_party_crops(monkeypatch, tmp_path):
    from test_party import profile as party_profile, scene

    config = party_profile()
    image, _ = scene(1)
    monkeypatch.setattr("hid_pilot.observation_debug.threading.Thread.start", lambda self: None)
    debug = ObservationDebugRecorder(tmp_path, BarProfile(width=200, height=800))
    state = {"hp": 1., "mp": 1., "party_profile": config.model_dump(mode="json"),
             "party": [{"slot": 1, "name": None, "hp": None, "mp": .5}],
             "party_ocr_age_seconds": 4.5}
    debug.observe(image, state)
    assert set(debug.pending[2]) == {"party_resources_unknown", "party_name_unknown"}
    debug.write_event(*debug.pending)
    slot = tmp_path / "event-00"
    assert (slot / "failure_party_1_hp.png").exists()
    assert (slot / "failure_party_8_name.png").exists()
    event = json.loads((slot / "event.json").read_text("utf-8"))
    assert event["failure"]["party_ocr_age_seconds"] == 4.5


def test_missing_party_triggers_capture_even_without_target_error(monkeypatch, tmp_path):
    from test_party import profile as party_profile, scene

    debug = recorder(monkeypatch, tmp_path)
    image, _ = scene(0)
    debug.observe(image, {"hp": 1., "mp": 1., "own_level": 32,
                          "party_profile": party_profile().model_dump(mode="json"), "party": []})
    assert debug.pending[2] == ["party_missing"]


def test_farmer_automatically_submits_unknown_levels(monkeypatch):
    from hid_pilot.farm import Farmer, FarmConfig

    box = BarBox(x1=0, y1=0, x2=.5, y2=.2)
    profile = BarProfile(width=200, height=100, bars={key: box for key in ("hp", "mp", "target_hp")},
                         own_level=box, target_level=box)
    monkeypatch.setattr(BarProfile, "read_detail", lambda self, image, name: (.5, "полоса"))
    monkeypatch.setattr(BarProfile, "read_level", lambda *args: None)
    controller = Mock()
    controller.pause_generation = 0
    controller.capture.return_value = np.zeros((100, 200, 3), dtype=np.uint8)
    farmer = Farmer(controller, profile, FarmConfig(rest="="), Mock(), lambda: 0.)
    farmer.debug_recorder = Mock()
    farmer.observe()
    state = farmer.debug_recorder.observe.call_args.args[1]
    assert state["own_level"] is None
    assert state["target"]["level"] is None


@pytest.mark.parametrize("debug_enabled", [False, True])
def test_setup_creates_recorder_only_in_debug_mode(monkeypatch, tmp_path, debug_enabled):
    from hid_pilot.farm import Farmer, FarmConfig

    factory = Mock()
    monkeypatch.setattr("hid_pilot.observation_debug.ObservationDebugRecorder", factory)
    monkeypatch.setattr("hid_pilot.farm.MonsterCatalogue", Mock())
    monkeypatch.setattr("hid_pilot.farm_observer.FarmIdentityCache", Mock())
    controller = Mock(debug_capture=debug_enabled)
    farmer = Farmer(controller, BarProfile(width=200, height=100), FarmConfig(rest="="), Mock(), lambda: 0.)
    farmer.setup_observation(tmp_path)
    assert factory.call_count == int(debug_enabled)
    assert (farmer.debug_recorder is not None) is debug_enabled
    farmer.close_observation()


def test_normal_start_does_not_save_hp_mp_snapshots(monkeypatch, tmp_path):
    from hid_pilot.farm import Farmer, FarmConfig

    path_factory = Mock(return_value=tmp_path)
    monkeypatch.setattr("hid_pilot.farm.user_data_dir", path_factory)
    farmer = Farmer(Mock(debug_capture=False), BarProfile(width=200, height=100),
                    FarmConfig(rest="="), Mock(), lambda: 0.)
    farmer.observation_frame = np.zeros((100, 200, 3), dtype=np.uint8)
    farmer.save_observation_failure("HP неизвестно")
    path_factory.assert_not_called()
    assert list(tmp_path.iterdir()) == []
