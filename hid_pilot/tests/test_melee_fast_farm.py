from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from hid_pilot.bars import BarProfile
from hid_pilot.controller import Stopped
from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from hid_pilot.farm import Farmer
from hid_pilot.farm_melee import MeleeConfig
from hid_pilot.target import TargetObservation
from test_duo_self_heal import observed_duo
from test_melee_farm import harness


def test_fast_farm_defaults_and_settings_roundtrip():
    assert not MeleeConfig(rest="=").experimental_fast_farm
    assert MeleeConfig(rest="=").fast_target_hp_threshold == .07
    config = DuoConfig(experimental_fast_farm=True, fast_target_hp_threshold=.09)
    assert parse_duo_settings(format_duo_settings(config)) == config
    for threshold in (0, 1, -.01):
        with pytest.raises(ValidationError):
            MeleeConfig(rest="=", fast_target_hp_threshold=threshold)


@pytest.mark.parametrize("hp,switched", [(None, True), (0., True), (.069, True),
                                         (.07, False), (.08, False)])
def test_fast_farm_switches_without_verification_loot_or_confirmed_kill(hp, switched):
    farmer, _, _, state = harness(experimental_fast_farm=True)
    state[2] = hp
    farmer.switch_fast_target = Mock()
    farmer.verify_target = Mock(side_effect=AssertionError("Проверка добивания запрещена"))
    farmer.pickup_loot = Mock()
    farmer.fight(.9)
    assert farmer.switch_fast_target.call_count == int(switched)
    farmer.verify_target.assert_not_called()
    farmer.pickup_loot.assert_not_called()
    assert not farmer.mob_statistics.counts
    assert farmer.training_kills == 0
    assert not farmer.damage_samples
    if switched:
        assert farmer.mob_statistics.unconfirmed_kills == 1


def test_fast_farm_uses_configurable_threshold():
    farmer, _, _, state = harness(experimental_fast_farm=True, fast_target_hp_threshold=.12)
    state[2] = .119
    farmer.switch_fast_target = Mock()
    farmer.fight(.9)
    farmer.switch_fast_target.assert_called_once_with()


def test_fast_farm_skips_marker_verification_even_with_positive_hp():
    farmer, _, _, _ = harness(experimental_fast_farm=True)

    def observe():
        farmer.last_target = TargetObservation(at=1., kind="monster", hp=.5, death_marker=True)
        return 1., 1., .5

    farmer.observe = observe
    farmer.switch_fast_target = Mock()
    farmer.verify_target = Mock()
    farmer.fight(.9)
    farmer.switch_fast_target.assert_called_once_with()
    farmer.verify_target.assert_not_called()


@pytest.mark.parametrize("hp,overhit", [(.199, True), (.2, False), (.4, False)])
def test_fast_overhit_ignores_training_and_average_but_requires_below_twenty_percent(hp, overhit):
    farmer, controller, _, _ = harness(experimental_fast_farm=True, overhit_keys=("F7",),
                                     damage_keys=("F8",), overhit_during_combat=True)
    farmer.damage_samples[("Gremlin", 1)] = [.9]
    farmer.use_damage_skill(hp, ("Gremlin", 1))
    assert controller.key.call_args.args[0] == ("F7" if overhit else "F8")
    assert farmer.training_kills == 0
    assert not farmer.use_damage_skill(hp, ("Gremlin", 1))


@pytest.mark.parametrize("kind,hp,level,allowed", [
    ("monster", .9, 1, True), ("player", .9, 1, False),
    ("npc", .9, 1, False), ("monster", 0., 1, False),
    ("monster", None, 1, False), ("monster", .9, 25, False),
])
def test_fast_selection_checks_fresh_target_before_immediate_attack(monkeypatch, kind, hp, level, allowed):
    farmer, controller, _, _ = harness(experimental_fast_farm=True)
    controller.pause_generation = 0
    farmer.profile = BarProfile(width=100, height=100)
    farmer.last_target = TargetObservation(at=0., kind="monster", hp=.05)
    farmer.display_data = ({"level": 20}, {}, [])

    def capture(self):
        assert self.last_target is None
        self.last_target = TargetObservation(at=1., kind=kind, hp=hp, level=level)
        return 1., 1., hp

    monkeypatch.setattr(Farmer, "observe", capture)
    farmer.switch_fast_target()
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys[0] == farmer.config.next_target
    assert (farmer.config.attack in keys) is allowed
    assert farmer.fast_target_ready is allowed
    controller.wait.assert_not_called()


def test_fast_selection_does_not_attack_after_pause_during_capture(monkeypatch):
    farmer, controller, _, _ = harness(experimental_fast_farm=True)
    controller.pause_generation = 0

    def capture(self):
        controller.pause_generation += 1
        return 1., 1., .9

    monkeypatch.setattr(Farmer, "observe", capture)
    farmer.switch_fast_target()
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target]
    assert not farmer.fast_target_ready


def test_fast_selection_rejects_tenfold_target_hp(monkeypatch):
    farmer, controller, _, _ = harness(experimental_fast_farm=True)
    controller.pause_generation = 0

    def capture(self):
        self.last_target = TargetObservation(at=1., kind="monster", hp=1.)
        self.observation_details = {"hp": "OCR 100/100", "target_hp": "OCR 1000/1000"}
        return 1., 1., 1.

    monkeypatch.setattr(Farmer, "observe", capture)
    farmer.switch_fast_target()
    assert farmer.config.attack not in [call.args[0] for call in controller.key.call_args_list]
    assert not farmer.fast_target_ready


def test_stop_after_next_target_prevents_fast_attack():
    farmer, controller, _, _ = harness(experimental_fast_farm=True)
    controller.key.side_effect = Stopped("Сценарий остановлен")
    with pytest.raises(Stopped):
        farmer.switch_fast_target()
    assert controller.key.call_count == 1
    assert not farmer.fast_target_ready


def test_duo_fast_attack_precedes_assist_and_reuses_selected_target(monkeypatch):
    duo, controller, _ = observed_duo(monkeypatch, .9, experimental_fast_farm=True,
                                    support_self_heal_key=None)
    controller.capture = lambda: (1., .9 if (1, duo.config.next_target) in controller.events else .06, False)
    duo.fight(.9)
    next_index = controller.events.index((1, duo.config.next_target))
    assert controller.events[next_index + 1] == (1, duo.config.attack)
    assert duo.fast_target_ready
    assert duo.attacking
    before = controller.events.count((1, duo.config.next_target))
    assert duo.acquire() == .9
    assert controller.events.count((1, duo.config.next_target)) == before
    assert (2, duo.config.support_key) in controller.events[next_index + 2:]
    assists = controller.events.count((2, duo.config.support_key))
    duo.fast_progress_at = controller.now
    duo.switch_fast_target = Mock()
    controller.capture = lambda: (1., .06, False)
    duo.fight(.9)
    assert controller.events.count((2, duo.config.support_key)) == assists
    assert controller.events.count((1, duo.config.attack)) == 2
    assert not duo.fast_target_ready


def test_fast_carry_does_not_duplicate_opening_attack_after_mode_is_disabled(monkeypatch):
    duo, controller, _ = observed_duo(monkeypatch, .9, experimental_fast_farm=True,
                                    support_self_heal_key=None)
    controller.capture = lambda: (1., .9 if (1, duo.config.next_target) in controller.events else .06, False)
    duo.fight(.9)
    duo.config = duo.config.model_copy(update={"experimental_fast_farm": False})
    controller.capture = lambda: (1., 0., False)
    duo.fight(.9)
    assert controller.events.count((1, duo.config.attack)) == 2
    assert not duo.fast_target_ready


def test_fast_progress_prevents_false_no_kill_escape_without_inventing_kills():
    farmer, _, now, state = harness(experimental_fast_farm=True)
    now[0] = 150.
    state[2] = .06
    farmer.switch_fast_target = Mock()
    farmer.fight(.9)
    farmer.escape_from_unreachable = Mock(return_value=None)
    assert not farmer.escape_if_no_kills()
    assert not farmer.mob_statistics.counts
    assert farmer.mob_statistics.unconfirmed_kills == 1
    farmer.kill_watch_attempts = 2
    now[0] += 121
    assert farmer.escape_if_no_kills()


def test_reselecting_already_low_hp_target_does_not_reset_stall_timer():
    farmer, _, now, state = harness(experimental_fast_farm=True)
    now[0] = 150.
    state[2] = .06
    farmer.switch_fast_target = Mock()
    farmer.fight(.06)
    assert farmer.fast_progress_at is None
