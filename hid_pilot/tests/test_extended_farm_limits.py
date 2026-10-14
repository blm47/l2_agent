from unittest.mock import Mock

import pytest

from hid_pilot.farm import FarmConfig
from hid_pilot.farm_melee import MeleeConfig
from hid_pilot.runner import Runner
from hid_pilot.script import Instruction, parse_script


@pytest.mark.parametrize("config", [FarmConfig, MeleeConfig])
def test_farm_accepts_ten_thousand_cycles_and_one_hundred_hours(config):
    result = config(rest="=", cycles="10000", max_seconds="360000")
    assert result.cycles == 10000
    assert result.max_seconds == 360000
    with pytest.raises(ValueError):
        config(rest="=", cycles=10001)
    with pytest.raises(ValueError):
        config(rest="=", max_seconds=360001)


def test_script_and_outer_runner_use_requested_duration(monkeypatch, tmp_path):
    text = 'set rest =\nset cycles 10000\nset max_seconds 360000\nfarm_melee "farm-bars.json"'
    assert parse_script(text)[0].operation == "farm_melee"
    now = [10.]
    monkeypatch.setattr("hid_pilot.runner.time.monotonic", lambda: now[0])
    runner = Runner(Mock(), tmp_path, Mock(), {"max_seconds": "360000"})
    assert runner.deadline == 360010
    now[0] = 36011.
    runner.run((Instruction(line=1, operation="wait", args=("0",)),))
    now[0] = 360011.
    with pytest.raises(RuntimeError, match="100"):
        runner.run((Instruction(line=1, operation="wait", args=("0",)),))


@pytest.mark.parametrize("duration", ["nan", "inf", "0", "360001"])
def test_outer_runner_rejects_invalid_duration(tmp_path, duration):
    with pytest.raises(ValueError):
        Runner(Mock(), tmp_path, Mock(), {"max_seconds": duration})


def test_repeated_basic_attack_is_allowed_in_damage_list():
    result = MeleeConfig(rest="=", next_target="6", attack="3", damage_keys="3", overhit_keys="2", heal="F2")
    assert result.damage_keys == ("3",)
