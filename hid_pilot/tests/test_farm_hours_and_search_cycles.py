from unittest.mock import Mock

import pytest
from test_melee_farm import harness

from hid_pilot.farm import FarmConfig
from hid_pilot.farm_melee import MeleeConfig
from hid_pilot.runner import Runner
from hid_pilot.script import parse_script
from hid_pilot.target import TargetObservation


def test_default_duration_and_search_cycles():
    config = MeleeConfig(rest="=")
    assert config.max_hours == 3 and config.max_seconds == 10800
    assert config.search_cycles == 30
    assert MeleeConfig(rest="=", search_cycles=10001).search_cycles == 10001
    with pytest.raises(ValueError):
        MeleeConfig(rest="=", search_cycles=0)


def test_hours_parser_runner_and_config_use_same_duration(monkeypatch, tmp_path):
    text = 'set rest =\nset max_hours 1.5\nset search_cycles 30\nfarm_melee "farm-bars.json"'
    assert parse_script(text)[0].operation == "farm_melee"
    monkeypatch.setattr("hid_pilot.runner.time.monotonic", lambda: 10.)
    runner = Runner(Mock(), tmp_path, Mock(), {"max_hours": "1.5"})
    config = MeleeConfig(rest="=", max_hours="1.5")
    assert runner.deadline == 5410
    assert config.max_seconds == runner.max_seconds == 5400
    assert FarmConfig(rest=config.rest, max_seconds=config.max_seconds).max_hours == 1.5


@pytest.mark.parametrize("hours", ["0", "-1", "nan", "inf", "101"])
def test_invalid_hours_are_rejected(hours, tmp_path):
    with pytest.raises(ValueError):
        MeleeConfig(rest="=", max_hours=hours)
    with pytest.raises(ValueError):
        Runner(Mock(), tmp_path, Mock(), {"max_hours": hours})


def test_legacy_seconds_work_but_conflicting_units_are_rejected(tmp_path):
    assert MeleeConfig(rest="=", max_seconds=7200).max_hours == 2
    with pytest.raises(ValueError):
        MeleeConfig(rest="=", max_hours=3, max_seconds=36000)
    with pytest.raises(ValueError):
        Runner(Mock(), tmp_path, Mock(), {"max_hours": "3", "max_seconds": "36000"})


def test_full_search_cycle_repeats_movement_next_target_and_chat():
    farmer, controller, _, state = harness(search_cycles=2)
    state[2] = None
    farmer.known_mobs = {"Gremlin": 1}
    with pytest.raises(RuntimeError, match="циклов"):
        farmer.acquire()
    keys = [call.args for call in controller.key.call_args_list]
    assert sum(key == farmer.config.next_target for key, _ in keys) == 8
    assert sum(key in "WASD" for key, _ in keys) == 6
    assert controller.target_by_name.call_count == 8


def test_target_found_on_second_full_cycle_stops_search():
    farmer, controller, _, state = harness(search_cycles=3)
    state[2] = None
    farmer.known_mobs = {"Gremlin": 1}

    def select(name):
        if controller.target_by_name.call_count == 2:
            state[2] = .7
            farmer.last_target = TargetObservation(at=0, name=name, kind="monster", confidence=.95)

    controller.target_by_name.side_effect = select
    assert farmer.acquire() == .7
    assert controller.target_by_name.call_count == 2


def test_search_without_movement_retries_next_target_and_names():
    farmer, controller, _, state = harness(search_cycles=2, search_movement=False)
    state[2] = None
    farmer.known_mobs = {"Gremlin": 1}
    with pytest.raises(RuntimeError, match="циклов"):
        farmer.acquire()
    assert controller.key.call_count == 8
    assert all(call.args[0] == farmer.config.next_target for call in controller.key.call_args_list)
    assert controller.target_by_name.call_count == 8


@pytest.mark.parametrize("kind,maximum", [("unknown", 30250), ("player", 1000), ("npc", 1000)])
def test_rejected_next_target_still_tries_safe_named_monster(monkeypatch, kind, maximum):
    from hid_pilot.farm import Farmer

    farmer, controller, _, state = harness(search_movement=False, target_max_hp_multiplier=8)
    del farmer.observe
    farmer.known_mobs = {"Gremlin": 1}

    def select_next(key, ms, **kwargs):
        if key == farmer.config.next_target:
            state[2] = 1.
            farmer.last_target = TargetObservation(at=0, kind=kind, hp=1.)
            farmer.observation_details = {"hp": "OCR 2625/2625", "target_hp": f"OCR 1/{maximum}"}

    def select_name(name):
        state[2] = .7
        farmer.last_target = TargetObservation(at=0, name=name, kind="monster", hp=.7, confidence=.99)
        farmer.observation_details = {"hp": "OCR 2625/2625", "target_hp": "OCR 700/1000"}

    monkeypatch.setattr(Farmer, "observe", lambda self: tuple(state))
    controller.key.side_effect = select_next
    controller.target_by_name.side_effect = select_name
    assert farmer.acquire() == .7
    controller.target_by_name.assert_called_once_with("Gremlin")
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target, "ESC"]
    assert not farmer.unsafe_target_rejected
