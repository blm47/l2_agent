from collections import deque

import pytest

from hid_pilot.farm_melee import MeleeConfig
from hid_pilot.script import parse_script
from test_melee_farm import harness


def test_combat_overhit_is_opt_in_and_does_not_change_old_default():
    assert MeleeConfig(rest="=").overhit_during_combat is False
    farmer, controller, _, _ = harness(overhit_keys="F3")
    assert not farmer.use_damage_skill(.9, None)
    controller.key.assert_not_called()


def test_enabled_flag_uses_overhit_as_regular_damage_before_training():
    farmer, controller, now, _ = harness(overhit_keys="F3", overhit_during_combat=True)
    assert farmer.use_damage_skill(.9, None)
    controller.key.assert_called_once_with("F3", 700)
    assert farmer.skill_ready["F3"] == pytest.approx(now[0] + farmer.config.overhit_interval)
    assert not farmer.use_damage_skill(.8, None)
    now[0] = farmer.skill_ready["F3"]
    assert farmer.use_damage_skill(.8, None)
    assert controller.key.call_count == 2


def test_ready_overhit_finisher_precedes_regular_damage():
    farmer, controller, _, _ = harness(overhit_keys="F3", damage_keys="F1", overhit_during_combat=True)
    farmer.training_kills = 3
    farmer.damage_samples[("Gremlin", 1)] = deque([.2])
    assert farmer.use_damage_skill(.15, ("Gremlin", 1))
    controller.key.assert_called_once_with("F3", 700)


def test_shared_overhit_cooldown_survives_transition_from_damage_to_finishing():
    farmer, controller, _, _ = harness(overhit_keys="F3", overhit_during_combat=True)
    assert farmer.use_damage_skill(.9, None)
    assert not farmer.use_damage_skill(.01, None)
    controller.key.assert_called_once()


def test_other_ready_overhit_skill_can_be_used_while_first_is_on_cooldown():
    farmer, controller, _, _ = harness(overhit_keys="F3,F4", overhit_during_combat=True)
    assert farmer.use_damage_skill(.9, None)
    assert farmer.use_damage_skill(.8, None)
    assert [call.args[0] for call in controller.key.call_args_list] == ["F3", "F4"]


def test_cooldown_does_not_block_ordinary_finishing_attack():
    farmer, controller, _, _ = harness(overhit_keys="F3", overhit_during_combat=True, pickup_loot=False)
    farmer.skill_ready["F3"] = 100
    readings = iter([.01, .01, .01, .01, .01, .01, None])
    farmer.observe = lambda: (1., 1., next(readings, None))
    farmer.fight(.01)
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys and all(key == farmer.config.attack for key in keys)
    assert len(keys) >= 2


def test_opening_overhit_damage_is_not_learned_as_an_ordinary_hit():
    farmer, controller, _, _ = harness(overhit_keys="F3", overhit_during_combat=True, pickup_loot=False)
    readings = iter([.8, .4, 0., 0.])
    farmer.observe = lambda: (1., 1., next(readings, None))
    farmer.fight(1.)
    assert controller.key.call_args_list[0].args == ("F3", 700)
    assert not farmer.current_damage_samples
    assert not farmer.damage_samples


@pytest.mark.parametrize("target", [0., -.1])
def test_enabled_flag_still_requires_living_target(target):
    farmer, controller, _, _ = harness(overhit_keys="F3", overhit_during_combat=True)
    assert not farmer.use_damage_skill(target, None)
    controller.key.assert_not_called()


def test_flag_parses_from_script():
    script = 'set rest =\nset overhit_during_combat true\nfarm_melee "farm-bars.json"'
    assert parse_script(script)[0].operation == "farm_melee"
