from pathlib import Path
from unittest.mock import Mock

import pytest

from hid_pilot.farm import Farmer
from hid_pilot.farm_melee import MeleeConfig, MeleeFarmer
from hid_pilot.mob_statistics import MobStatistics
from hid_pilot.script import parse_script
from hid_pilot.target import TargetObservation


def harness(**changes):
    changes.setdefault("hp_elixir_key", None)
    changes.setdefault("buff_delay_minutes", 0)
    changes.setdefault("aggro_key", None)
    # Сценарии боя и восстановления ниже изолированы от стартового подбора.
    changes.setdefault("regen_enabled", True)
    changes.setdefault("pre_attack_loot", False)
    changes.setdefault("overhit_early_chance", 0.)
    changes.setdefault("search_cycles", 1)
    now = [0.]
    controller = Mock()
    controller.transport.protocol = "HID_PILOT_V4"
    controller.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
    controller.key.side_effect = lambda key, ms, **kwargs: now.__setitem__(0, now[0] + ms / 1000)
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
    assert controller.key.call_args_list[-1].args == ("ESC", 100)
    assert controller.key.call_count > 2
    assert all(call.args == ("R", 100) for call in controller.key.call_args_list[:-1])


def test_unreachable_target_is_cleared_before_next_selection_without_loot():
    farmer, controller, _, _ = harness()
    farmer.target_pending = True
    farmer.last_target = TargetObservation(at=0, kind="monster", name="Gremlin", confidence=.99)
    farmer.fight(1.)
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys[-1] == "ESC" and set(keys[:-1]) == {"R"}
    assert not farmer.target_pending
    assert farmer.last_target is None
    assert farmer.training_kills == 0


def test_target_stops_taking_damage_and_is_cleared_after_stuck_timeout():
    farmer, controller, _, state = harness(stuck_timeout=1)
    state[2] = .8
    farmer.fight(1.)
    assert controller.key.call_args_list[-1].args == ("ESC", 100)
    assert farmer.training_kills == 0


def test_attack_repeats_every_one_and_half_seconds_without_extending_approach_timeout():
    farmer, controller, now, _ = harness(attack_delay=1.5, approach_delay=5)
    attacks = []

    def key(name, duration, **kwargs):
        if name == farmer.config.attack:
            attacks.append(now[0])
        now[0] += duration / 1000

    controller.key.side_effect = key
    farmer.fight(1.)
    assert attacks == pytest.approx([0., 1.5, 3., 4.5])
    assert controller.key.call_args_list[-1].args == ("ESC", 100)
    assert now[0] < 6


def test_dead_target_does_not_receive_scheduled_attack():
    farmer, controller, now, _ = harness(pickup_loot=False)
    farmer.observe = lambda: (1., 1., 0. if now[0] >= 1.4 else .8)
    farmer.fight(1.)
    assert [call.args[0] for call in controller.key.call_args_list] == ["R"]
    assert farmer.training_kills == 1


def test_recovery_can_continue_beyond_old_two_minute_limit():
    farmer, controller, now, state = harness()
    assert farmer.config.rest_timeout == 180
    state[:] = [1., .05, None]
    farmer.quiet_since = 0
    wait = controller.wait.side_effect

    def recover_mana(seconds):
        wait(seconds)
        if now[0] >= 130:
            state[1] = .95

    controller.wait.side_effect = recover_mana
    farmer.recover()
    assert 130 <= now[0] < 180
    assert not farmer.sitting


def test_recovery_stops_after_three_minutes_if_mana_does_not_recover():
    farmer, _controller, now, state = harness()
    state[:] = [1., .05, None]
    farmer.quiet_since = 0
    with pytest.raises(RuntimeError, match="Таймаут восстановления"):
        farmer.recover()
    assert 180 <= now[0] < 181


def test_lost_target_continues_without_loot():
    farmer, controller, _, state = harness()
    state[2] = None
    farmer.fight(1.)
    assert [call.args[0] for call in controller.key.call_args_list] == ["R"]


def test_dead_target_holds_loot_without_approach():
    farmer, controller, _, state = harness()
    state[2] = 0.
    farmer.fight(1.)
    assert [call.args for call in controller.key.call_args_list] == [("R", 100), ("5", 50)]


def test_next_target_tries_names_before_each_of_three_moves_and_exhaustion():
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
    assert sorted(call.args[0] for call in controller.target_by_name.call_args_list) == (
        ["Cloudy Beast"] * 4 + ["Gremlin"] * 4)


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
    assert now[0] >= farmer.config.quiet_seconds
    assert [call.args[0] for call in controller.key.call_args_list] == ["=", "-"]


def test_rest_kills_aggro_before_sitting():
    farmer, controller, now, state = harness()
    state[:] = [.99, .05, None]
    farmer.last_resources = (1., None)
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
