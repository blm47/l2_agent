from pathlib import Path
from unittest.mock import Mock

import pytest

from hid_pilot.farm import Farmer
from hid_pilot.farm_melee import MeleeConfig, MeleeFarmer
from hid_pilot.mob_statistics import MobStatistics
from hid_pilot.script import parse_script
from hid_pilot.target import TargetObservation


def harness(**changes):
    now = [0.]
    controller = Mock()
    controller.transport.protocol = "HID_PILOT_V4"
    controller.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
    controller.key.side_effect = lambda key, ms: now.__setitem__(0, now[0] + ms / 1000)
    farmer = MeleeFarmer(controller, Mock(), MeleeConfig(rest="=", humanize=False, **changes),
                         Mock(), lambda: now[0])
    state = [1., 1., 1.]
    farmer.observe = lambda: tuple(state)
    return farmer, controller, now, state


def test_approach_wait_starts_after_attack_and_switches_target():
    farmer, controller, now, _ = harness()
    farmer.fight(1.)
    assert now[0] >= 5.1
    assert controller.key.call_args_list[0].args == ("R", 100)
    assert controller.key.call_count == 1


def test_lost_target_continues_without_loot():
    farmer, controller, _, state = harness()
    state[2] = None
    farmer.fight(1.)
    assert [call.args[0] for call in controller.key.call_args_list] == ["R"]


def test_dead_target_holds_loot_without_approach():
    farmer, controller, _, state = harness()
    state[2] = 0.
    farmer.fight(1.)
    assert [call.args for call in controller.key.call_args_list] == [("R", 100), ("5", 1500)]


def test_next_target_tries_three_moves_before_chat_and_exhaustion():
    farmer, controller, _, state = harness()
    state[2] = None
    farmer.known_mobs = {"Gremlin": 1, "Cloudy Beast": 22}
    with pytest.raises(RuntimeError, match="/target"):
        farmer.acquire()
    keys = [call.args for call in controller.key.call_args_list]
    assert sum(key == "3" for key, _ in keys) == 4
    movements = [(key, ms) for key, ms in keys if key in "WASD"]
    assert len(movements) == 3
    assert all(2000 <= ms <= 3000 for _, ms in movements)
    assert [call.args[0] for call in controller.target_by_name.call_args_list] == ["Gremlin", "Cloudy Beast"]


def test_chat_selection_requires_matching_monster():
    farmer, controller, _, state = harness()
    state[2] = None
    farmer.known_mobs = {"Gremlin": 1}

    def select(name):
        state[2] = 1.
        farmer.last_target = TargetObservation(at=0, kind="monster", name=name, confidence=.95)

    controller.target_by_name.side_effect = select
    assert farmer.acquire() == 1.


def test_damage_and_five_seconds_without_damage(monkeypatch):
    farmer, _, now, state = harness()
    del farmer.observe
    monkeypatch.setattr(Farmer, "observe", lambda self: tuple(state))
    farmer.observe()
    assert not farmer.under_attack
    now[0] = .3
    state[0] = .9
    farmer.observe()
    assert farmer.under_attack
    for _ in range(17):
        now[0] += .3
        farmer.observe()
    assert not farmer.under_attack
    assert now[0] - farmer.quiet_since >= 5


def test_rest_waits_for_quiet_and_wakes_when_attacked():
    farmer, controller, now, state = harness()
    farmer.quiet_since = 0.
    state[:] = [1., .05, None]
    waits = controller.wait.side_effect

    def wait(seconds):
        waits(seconds)
        if farmer.sitting:
            state[1] = .95

    controller.wait.side_effect = wait
    farmer.recover()
    assert now[0] >= 3
    assert [call.args[0] for call in controller.key.call_args_list] == ["=", "="]


def test_rest_kills_aggro_before_sitting():
    farmer, controller, now, state = harness()
    state[:] = [1., .05, None]
    farmer.under_attack = True
    farmer.quiet_since = 0.

    def fight(target=None):
        assert not farmer.sitting
        farmer.under_attack = False
        now[0] = 5.
        state[1] = .95

    farmer.fight = Mock(side_effect=fight)
    farmer.recover()
    farmer.fight.assert_called_once_with()
    controller.key.assert_not_called()


def test_monster_database_persists_observed_levels(tmp_path):
    stats = MobStatistics(0.)
    stats.database = tmp_path / "monsters.sqlite3"
    stats.remember(TargetObservation(at=0, kind="monster", name="Cloudy Beast", level=22, confidence=.95))
    stats.remember(TargetObservation(at=1, kind="monster", name="Cloudy Beast", confidence=.95))
    assert stats.known_names() == {"Cloudy Beast": 22}


def test_new_scripts_and_chat_skill_parse():
    root = Path(__file__).parents[1] / "extensions" / "scripts"
    assert parse_script((root / "farm_melee.pilot").read_text("utf-8"))[0].operation == "farm_melee"
    assert parse_script((root / "farm_mage.pilot").read_text("utf-8"))[0].operation == "farm"
    assert parse_script('target_name "Cloudy Beast"')[0].operation == "target_name"
