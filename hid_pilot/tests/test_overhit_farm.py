from collections import deque

import pytest
from test_melee_farm import harness

from hid_pilot.farm_melee import MeleeConfig
from hid_pilot.script import parse_script
from hid_pilot.target import TargetObservation


def monster(farmer, name="Gremlin", level=1):
    farmer.last_target = TargetObservation(at=farmer.clock(), kind="monster", name=name,
                                           level=level, confidence=.95)


def fight_sequence(farmer, values):
    readings = iter(values)
    farmer.observe = lambda: (1., 1., next(readings, None))
    farmer.fight(1.)


def test_three_kills_calibrate_normal_damage_then_overhit_finishes():
    farmer, controller, _, _ = harness(overhit_keys="F3,F4", pickup_loot=False)
    monster(farmer)
    for _ in range(3):
        fight_sequence(farmer, [.8, .6, .4, .2, 0., 0.])
    assert farmer.training_kills == 3
    assert farmer.average_damage(("Gremlin", 1)) == pytest.approx(.2)
    assert [call.args[0] for call in controller.key.call_args_list] == ["R"] * 3
    fight_sequence(farmer, [.8, .6, .4, .22, 0., 0.])
    assert [call.args[0] for call in controller.key.call_args_list][-2:] == ["R", "F3"]
    assert farmer.average_damage(("Gremlin", 1)) == pytest.approx((12 * .2 + .2 + .2 + .2 + .18) / 16)
    assert "3/3" in farmer.damage_status()


def test_additional_skills_excluded_from_mean_and_overhit_has_priority():
    farmer, controller, _, _ = harness(damage_keys="F1,F2", overhit_keys="F3", pickup_loot=False)
    monster(farmer)
    farmer.training_kills = 3
    farmer.damage_samples[("Gremlin", 1)] = deque([.2] * 4, maxlen=128)
    fight_sequence(farmer, [.8, .5, .2, 0., 0.])
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys == ["R", "F1", "F2", "F3"]
    assert list(farmer.damage_samples[("Gremlin", 1)]) == pytest.approx([.2] * 5)


def test_skill_intervals_and_reserving_overhit_when_on_cooldown():
    farmer, controller, now, _ = harness(damage_keys="F1", overhit_keys="F3,F4")
    farmer.training_kills = 3
    farmer.damage_samples[("Gremlin", 1)] = deque([.2])
    assert farmer.use_damage_skill(.2, ("Gremlin", 1))
    assert farmer.use_damage_skill(.2, ("Gremlin", 1))
    assert not farmer.use_damage_skill(.2, ("Gremlin", 1))
    assert [call.args[0] for call in controller.key.call_args_list] == ["F3", "F4"]
    now[0] += 5
    assert farmer.use_damage_skill(.2, ("Gremlin", 1))


def test_unknown_monster_does_not_reuse_other_monsters_percentage():
    farmer, controller, _, _ = harness(overhit_keys="F3")
    farmer.training_kills = 3
    farmer.damage_samples[("Gremlin", 1)] = deque([.2])
    assert not farmer.use_damage_skill(.1, ("Cloudy Beast", 22))
    controller.key.assert_not_called()


def test_target_loss_or_unconfirmed_death_does_not_train():
    farmer, _, _, _ = harness(overhit_keys="F3", pickup_loot=False)
    monster(farmer)
    fight_sequence(farmer, [.8, None])
    fight_sequence(farmer, [.8, 0., None])
    assert farmer.training_kills == 0
    assert farmer.damage_samples == {}


def test_identity_change_discards_damage_samples():
    farmer, controller, _, _ = harness(pickup_loot=False)
    monster(farmer)
    count = [0]

    def observe():
        count[0] += 1
        if count[0] == 2:
            monster(farmer, "Other", 10)
        return 1., 1., .8 if count[0] == 1 else 0.

    farmer.observe = observe
    farmer.fight(1.)
    assert not farmer.damage_samples
    assert farmer.training_kills == 0
    assert controller.key.call_count == 1


def test_low_hp_target_opens_with_overhit_instead_of_autoattack():
    farmer, controller, _, state = harness(overhit_keys="F3", pickup_loot=False)
    monster(farmer)
    farmer.training_kills = 3
    farmer.damage_samples[("Gremlin", 1)] = deque([.2])
    state[2] = 0.
    farmer.fight(.15)
    assert [call.args[0] for call in controller.key.call_args_list] == ["F3"]


@pytest.mark.parametrize("changes", [{"damage_keys": "F1,F1"}, {"overhit_keys": "ALT+TAB"},
                                    {"damage_keys": "F1", "overhit_keys": "F1"}, {"overhit_keys": "R"}])
def test_invalid_key_lists_rejected(changes):
    with pytest.raises(ValueError):
        MeleeConfig(rest="=", **changes)


def test_key_lists_parse_from_script_and_empty_defaults():
    code = 'set rest =\nset damage_keys "F1,F2"\nset overhit_keys "F3 F4"\nfarm_melee "farm-bars.json"'
    assert parse_script(code)[0].operation == "farm_melee"
    assert MeleeConfig(rest="=", damage_keys="").damage_keys == ()
