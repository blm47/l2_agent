from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.farm import FarmConfig, Farmer
from hid_pilot.target import TargetObservation, TargetReader


@pytest.mark.parametrize("hp", [.75, 0., None])
def test_hp_without_name_panel_stays_available_and_type_unknown(monkeypatch, hp):
    box = BarBox(x1=.1, y1=.1, x2=.8, y2=.2)
    profile = BarProfile(width=200, height=100, numbers={"target_hp": box})
    monkeypatch.setattr(BarProfile, "read", lambda self, image, name: hp)
    result = TargetReader(profile).read(np.zeros((100, 200, 3), dtype=np.uint8), 10.)
    assert result.hp == hp
    assert result.dead == (None if hp is None else hp == 0)
    assert result.name is None and result.kind == "unknown"


@pytest.mark.parametrize("numeric_hp,panel_hp,expected", [(.75, None, .75), (.75, 0., .75), (None, .6, .6)])
def test_control_and_observation_panel_share_fresh_target_hp(monkeypatch, numeric_hp, panel_hp, expected):
    box = BarBox(x1=.1, y1=.1, x2=.8, y2=.2)
    profile = BarProfile(width=200, height=100, numbers={"hp": box, "mp": box, "target_hp": box})
    monkeypatch.setattr("hid_pilot.numbers.shared_numbers", lambda: Mock())
    monkeypatch.setattr(BarProfile, "read_detail", lambda self, image, name:
                        (numeric_hp if name == "target_hp" else 1., "OCR"))
    controller, notify = Mock(), Mock()
    controller.capture.return_value = np.zeros((100, 200, 3), dtype=np.uint8)
    farm = Farmer(controller, profile, FarmConfig(rest="="), notify, lambda: 0.)
    farm.target_reader = Mock()
    farm.target_reader.read.return_value = TargetObservation(at=0, hp=panel_hp)
    farm.target_reader.death.side_effect = TargetReader.death
    assert farm._observe()[2] == expected
    assert farm.last_target.hp == expected
    assert f"HP цели: {expected:.0%}" in notify.state.call_args.args[0]["text"]
    controller.key.assert_not_called()
