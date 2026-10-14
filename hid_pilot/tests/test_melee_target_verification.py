from test_melee_farm import harness

from hid_pilot.target import TargetObservation


def run_sequence(values, **changes):
    farmer, controller, now, _ = harness(**changes)
    readings = iter(values)
    farmer.observe = lambda: (1., 1., next(readings, None))
    farmer.fight(1.)
    return farmer, controller, now


def test_transient_ocr_loss_keeps_fighting_same_target_until_confirmed_death():
    farmer, controller, _ = run_sequence([.8, None, None, .4, .2, 0., 0.])
    assert sum(farmer.mob_statistics.counts.values()) == 1
    assert [call.args[0] for call in controller.key.call_args_list] == ["R", "5"]


def test_single_false_zero_does_not_trigger_loot_or_kill():
    farmer, controller, _ = run_sequence([.8, 0., .6, .3, None], pickup_loot=False)
    assert sum(farmer.mob_statistics.counts.values()) == 0
    assert controller.key.call_count == 1


def test_death_with_missing_frame_requires_two_new_zero_readings():
    farmer, _, _ = run_sequence([.8, 0., None, 0., 0.], pickup_loot=False)
    assert sum(farmer.mob_statistics.counts.values()) == 1


def test_permanent_unknown_hp_has_bounded_wait_and_does_not_count_death():
    farmer, controller, now = run_sequence([.8, None], pickup_loot=False)
    assert now[0] >= 2
    assert now[0] < 3
    assert not farmer.mob_statistics.counts
    assert controller.key.call_count == 1


def test_incoming_damage_does_not_turn_unknown_hp_into_confirmed_death():
    farmer, controller, _, _ = harness()
    farmer.under_attack = True
    hp, mp, target, dead = farmer.verify_target(.8, 1., None, None)
    assert target == 1. and not dead
    controller.key.assert_not_called()
    farmer.observe = lambda: (.7, 1., None)
    hp, mp, target, dead = farmer.verify_target(.8, 1., None, None)
    assert target is None and not dead
    assert hp == .7 and mp == 1.
    assert "получаем урон" in farmer.current_action


def test_different_known_target_cannot_confirm_old_targets_death():
    farmer, controller, _, _ = harness()
    farmer.last_target = TargetObservation(at=0, kind="monster", name="Other", level=1, confidence=.95)
    farmer.observe = lambda: (1., 1., 0.)
    assert farmer.verify_target(1., 1., 0., ("Gremlin", 1))[3] is False
    controller.key.assert_not_called()
