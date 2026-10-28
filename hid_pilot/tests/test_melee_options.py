from unittest.mock import Mock

import pytest

from hid_pilot.farm_melee import MeleeConfig, MeleeFarmer
from test_melee_farm import harness
from test_duo import make_duo


def test_defaults_disable_regen_and_opening_loot():
    config = MeleeConfig(rest="=")
    assert not config.regen_enabled
    assert not config.loot_before_farm
    assert config.loot_hold == .05
    assert config.loot_attempts == 1


def test_duo_post_kill_loot_uses_configured_count_without_extra_waits():
    farmer, controller = make_duo(loot_attempts=3, loot_delay=.7, loot_hold=2.5)
    before = controller.now
    farmer.pickup_loot()
    assert controller.events == [(1, "5")] * 3
    assert controller.now == before
    assert type(farmer).pickup_loot is MeleeFarmer.pickup_loot
    assert farmer.current_action == "Автолут: кнопка 5 отправлена 3/3, удержание 2500 мс"


def test_both_loot_phases_use_configured_count_and_duration():
    farmer, controller, _, state = harness(pre_attack_loot=True, regen_enabled=False, loot_hold=.15, loot_attempts=2)
    original_key = controller.key.side_effect

    def key(name, duration, **kwargs):
        original_key(name, duration)
        if name == farmer.config.attack:
            state[2] = 0.

    controller.key.side_effect = key
    farmer.fight()
    assert [call.args for call in controller.key.call_args_list] == [
        (farmer.config.next_target, 100), (farmer.config.loot, 150), (farmer.config.loot, 150),
        (farmer.config.attack, 100), (farmer.config.loot, 150), (farmer.config.loot, 150)]
    assert all(call.kwargs == {"exact": True} for call in controller.key.call_args_list
               if call.args[0] == farmer.config.loot)


def test_default_autoloot_only_runs_after_confirmed_death():
    farmer, controller, _, state = harness(regen_enabled=False)
    original_key = controller.key.side_effect

    def key(name, duration, **kwargs):
        original_key(name, duration, **kwargs)
        if name == farmer.config.attack:
            state[2] = 0.

    controller.key.side_effect = key
    farmer.fight()
    assert [call.args for call in controller.key.call_args_list] == [
        (farmer.config.next_target, 100), (farmer.config.attack, 100), (farmer.config.loot, 50)]


def test_disabling_autoloot_disables_both_phases():
    farmer, controller, _, state = harness(pickup_loot=False, loot_before_farm=True, regen_enabled=False)
    original_key = controller.key.side_effect

    def key(name, duration, **kwargs):
        original_key(name, duration, **kwargs)
        if name == farmer.config.attack:
            state[2] = 0.

    controller.key.side_effect = key
    farmer.fight()
    assert farmer.config.loot not in [call.args[0] for call in controller.key.call_args_list]


def test_old_pre_attack_setting_is_read_and_new_setting_takes_priority():
    assert MeleeConfig(rest="=", pre_attack_loot=True).loot_before_farm
    config = MeleeConfig(rest="=", pre_attack_loot=True, loot_before_farm=False)
    assert not config.loot_before_farm
    assert "pre_attack_loot" not in config.model_dump()


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


def test_next_target_then_configured_loot_then_attack():
    farmer, controller, _, state = harness(pre_attack_loot=True, regen_enabled=False)
    original_key = controller.key.side_effect

    def key(name, duration, **kwargs):
        original_key(name, duration)
        if name == farmer.config.attack:
            state[2] = 0.

    controller.key.side_effect = key
    farmer.fight()
    assert [call.args for call in controller.key.call_args_list[:3]] == [
        (farmer.config.next_target, 100), (farmer.config.loot, 50), (farmer.config.attack, 100)]
    assert farmer.training_kills == 1


@pytest.mark.parametrize("target", [None, 0.])
def test_missing_live_target_after_opening_loot_prevents_attack(target):
    farmer, controller, _, state = harness(pre_attack_loot=True, regen_enabled=False)
    original_key = controller.key.side_effect

    def key(name, duration, **kwargs):
        original_key(name, duration)
        if name == farmer.config.loot:
            state[2] = target

    controller.key.side_effect = key
    farmer.fight(.9)
    assert [call.args for call in controller.key.call_args_list] == [(farmer.config.loot, 50)]
    assert farmer.training_kills == 0


def test_duo_inherits_opening_loot_before_attack_and_assist():
    farmer, controller = make_duo(pre_attack_loot=True)
    # Обычный ассист требует наблюдаемого урона после команды атаки МА.
    controller.capture = lambda: (1., 0. if (2, "F3") in controller.events
                                 else .8 if (1, "R") in controller.events else .9, False)
    farmer.fight(.9)
    assert controller.events.index((1, "5")) < controller.events.index((1, "R"))
    assert controller.events.index((1, "R")) < controller.events.index((2, "F3"))
