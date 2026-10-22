from unittest.mock import Mock

import pytest

from hid_pilot.farm_melee import MeleeConfig, MeleeFarmer
from test_melee_farm import harness
from test_duo import make_duo


def test_defaults_disable_regen_and_enable_opening_loot():
    config = MeleeConfig(rest="=")
    assert not config.regen_enabled
    assert config.pre_attack_loot
    assert config.loot_hold == 1


def test_duo_post_kill_loot_is_one_second_without_repeats_or_waits():
    farmer, controller = make_duo(loot_attempts=3, loot_delay=.7, loot_hold=2.5)
    before = controller.now
    farmer.pickup_loot()
    assert controller.events == [(1, "5")]
    assert controller.now == before
    assert type(farmer).pickup_loot is MeleeFarmer.pickup_loot


def test_both_loot_holds_are_exactly_one_second_after_selection_and_death():
    farmer, controller, _, state = harness(pre_attack_loot=True, regen_enabled=False, loot_hold=2.5)
    original_key = controller.key.side_effect

    def key(name, duration):
        original_key(name, duration)
        if name == farmer.config.attack:
            state[2] = 0.

    controller.key.side_effect = key
    farmer.fight()
    assert [call.args for call in controller.key.call_args_list] == [
        (farmer.config.next_target, 100), (farmer.config.loot, 1000),
        (farmer.config.attack, 100), (farmer.config.loot, 1000)]


@pytest.mark.parametrize("hp,mp", [(.2, .8), (.9, .05), (.2, .05)])
def test_disabled_regen_keeps_farming_with_low_resources(hp, mp):
    farmer, _, _, state = harness(regen_enabled=False, cycles=1)
    state[:] = [hp, mp, .9]
    farmer.recover = Mock()
    farmer.fight = Mock()
    farmer.run()
    farmer.before_target_selection()
    farmer.recover.assert_not_called()
    farmer.fight.assert_called_once_with()


def test_explicit_recovery_call_does_not_sit_when_disabled():
    farmer, controller, _, _ = harness(regen_enabled=False)
    farmer.rest_requested = True
    farmer.recover()
    controller.key.assert_not_called()
    assert not farmer.rest_requested


def test_next_target_then_one_second_loot_then_attack():
    farmer, controller, _, state = harness(pre_attack_loot=True, regen_enabled=False)
    original_key = controller.key.side_effect

    def key(name, duration):
        original_key(name, duration)
        if name == farmer.config.attack:
            state[2] = 0.

    controller.key.side_effect = key
    farmer.fight()
    assert [call.args for call in controller.key.call_args_list[:3]] == [
        (farmer.config.next_target, 100), (farmer.config.loot, 1000), (farmer.config.attack, 100)]
    assert farmer.training_kills == 1


@pytest.mark.parametrize("target", [None, 0.])
def test_missing_live_target_after_opening_loot_prevents_attack(target):
    farmer, controller, _, state = harness(pre_attack_loot=True, regen_enabled=False)
    original_key = controller.key.side_effect

    def key(name, duration):
        original_key(name, duration)
        if name == farmer.config.loot:
            state[2] = target

    controller.key.side_effect = key
    farmer.fight(.9)
    assert [call.args for call in controller.key.call_args_list] == [(farmer.config.loot, 1000)]
    assert farmer.training_kills == 0


def test_duo_inherits_opening_loot_before_attack_and_assist():
    farmer, controller = make_duo(pre_attack_loot=True)
    controller.capture = lambda: (1., 0. if (2, "F3") in controller.events else .9, False)
    farmer.fight(.9)
    assert controller.events.index((1, "5")) < controller.events.index((1, "R"))
    assert controller.events.index((1, "R")) < controller.events.index((2, "F3"))
