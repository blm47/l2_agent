from threading import RLock
from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.controller import ActionController, Stopped
from hid_pilot.farm import Farmer, FarmConfig
from hid_pilot.target import TargetObservation


def recovery_farm():
    now = [0.]
    controller = Mock()
    controller.pause_generation = 0
    controller.wait.side_effect = lambda delay: now.__setitem__(0, now[0] + delay)
    farmer = Farmer(controller, Mock(), FarmConfig(rest="I", observation_attempts=2),
                    Mock(), lambda: now[0])
    return farmer, controller, now


def test_recovery_requires_three_consecutive_successes_and_no_input():
    farmer, controller, _ = recovery_farm()
    failed, valid = (None, .7, None), (.8, .7, .5)
    farmer._observe = Mock(side_effect=[failed, failed, valid, failed, valid, valid, valid])
    assert farmer.observe() == valid
    assert farmer._observe.call_count == 7
    controller.release_inputs.assert_called_once()
    controller.key.assert_not_called()
    assert not farmer.observation_recovering


def test_stop_interrupts_recovery_wait():
    farmer, controller, _ = recovery_farm()
    farmer._observe = Mock(return_value=(None, None, None))
    controller.wait.side_effect = Stopped("STOP")
    with pytest.raises(Stopped):
        farmer.observe()
    controller.key.assert_not_called()


def test_slow_target_ocr_rereads_resources_on_new_frame(monkeypatch):
    farmer, controller, now = recovery_farm()
    box = BarBox(x1=0, y1=0, x2=1, y2=.2)
    farmer.profile = BarProfile(width=100, height=100,
                               bars={name: box for name in ("hp", "mp", "target_hp")})
    controller.capture.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(BarProfile, "read_detail", lambda self, image, name: (.8, "полоса"))
    farmer.target_reader = Mock()

    def slow_read(image, started):
        now[0] += 2
        return TargetObservation(at=started, hp=.5)

    farmer.target_reader.read.side_effect = slow_read
    farmer.on_observation = Mock()
    assert farmer.observe() == (.8, .8, None)
    assert controller.capture.call_count == 2
    assert farmer.last_target is None
    assert farmer.own_cp is None


def test_recovery_does_not_run_observation_actions(monkeypatch):
    farmer, controller, _ = recovery_farm()
    controller.capture.return_value = np.zeros((10, 10, 3), dtype=np.uint8)
    farmer.profile.read.return_value = .8
    farmer.observation_recovering = True
    farmer.on_observation = Mock()
    assert farmer._observe() == (.8, .8, .8)
    farmer.on_observation.assert_not_called()


def test_diagnostic_snapshot_overwrites_bounded_files(monkeypatch, tmp_path):
    farmer, _, _ = recovery_farm()
    farmer.controller.debug_capture = True
    box = BarBox(x1=0, y1=0, x2=1, y2=.2)
    farmer.profile = BarProfile(width=100, height=100, bars={"hp": box, "mp": box})
    farmer.observation_frame = np.zeros((100, 100, 3), dtype=np.uint8)
    monkeypatch.setattr("hid_pilot.farm.user_data_dir", lambda: tmp_path)
    farmer.save_observation_failure("HP неизвестно")
    farmer.save_observation_failure("MP неизвестно")
    directory = tmp_path / "observation-failure"
    assert {path.name for path in directory.iterdir()} == {"window.png", "hp.png", "mp.png", "reason.json"}
    assert "MP неизвестно" in (directory / "reason.json").read_text("utf-8")


def test_release_inputs_goes_through_controller_transport():
    controller = Mock()
    controller.lock = RLock()
    ActionController.release_inputs(controller)
    controller.checkpoint.assert_called_once()
    controller.transport.release.assert_called_once()
