import pytest

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
    assert farmer.mob_statistics.unconfirmed_kills == 0


def test_single_false_zero_does_not_trigger_loot_or_kill():
    farmer, controller, _ = run_sequence([.8, 0., .6, .3, None], pickup_loot=False)
    assert sum(farmer.mob_statistics.counts.values()) == 0
    assert controller.key.call_count == 1


def test_two_fresh_death_markers_confirm_missing_numeric_hp():
    farmer, _, _, _ = harness()
    count = [0]

    def observe():
        count[0] += 1
        farmer.last_target = TargetObservation(at=farmer.clock(), hp=None,
                                              death_marker=True, dead=True)
        return 1., 1., None

    farmer.observe = observe
    assert farmer.verify_target(1., 1., None, None)[3]
    assert count[0] == 2


def test_positive_ocr_hp_does_not_override_two_fresh_death_markers():
    farmer, _, _, _ = harness()

    def observe():
        farmer.last_target = TargetObservation(at=farmer.clock(), hp=7 / 4506,
                                              death_marker=True, dead=True)
        return 1., 1., 7 / 4506

    farmer.observe = observe
    assert farmer.verify_target(1., 1., None, None)[3]


def test_zero_then_marker_is_enough_to_confirm_death():
    farmer, _, _, _ = harness()

    def observe():
        farmer.last_target = TargetObservation(at=farmer.clock(), hp=7 / 4506,
                                              death_marker=True, dead=True)
        return 1., 1., 7 / 4506

    farmer.observe = observe
    assert farmer.verify_target(1., 1., 0., None)[3]


def test_dead_marker_blocks_attacks_and_alive_target_checks():
    farmer, controller, _, _ = harness(aggro_key="2", damage_keys=("F7",))
    farmer.last_target = TargetObservation(at=0, hp=7 / 4506, death_marker=True, dead=True)
    assert not farmer.alive_target(7 / 4506)
    assert not farmer.combat_key(farmer.config.attack, 100)
    assert not farmer.combat_key("F7", 100)
    assert not farmer.combat_key("2", 100)
    controller.key.assert_not_called()


def test_same_marker_observation_cannot_confirm_death_twice():
    farmer, _, _, _ = harness()
    farmer.last_target = TargetObservation(at=0, hp=None, death_marker=True, dead=True)
    farmer.observe = lambda: (1., 1., None)
    assert not farmer.verify_target(1., 1., None, None)[3]


def test_death_allows_one_missing_frame_and_picks_up_loot():
    farmer, controller, _ = run_sequence([.8, 0., None, 0.])
    assert sum(farmer.mob_statistics.counts.values()) == 1
    assert [call.args[0] for call in controller.key.call_args_list] == ["R", "5"]


@pytest.mark.parametrize("readings,confirmed", [
    ([None, 0.], True),
    ([None, None, 0.], False),
    ([None, None, 0., 0.], True),
    ([None, .5], False),
    ([None], False),
])
def test_death_confirmation_tolerates_only_one_gap(readings, confirmed):
    farmer, _, _, _ = harness()
    values = iter(readings)
    farmer.observe = lambda: (1., 1., next(values, None))
    _, _, target, dead = farmer.verify_target(1., 1., 0., None)
    assert dead is confirmed
    if confirmed:
        assert target == 0.
    elif readings[-1] == .5:
        assert target == .5


def test_permanent_unknown_hp_has_bounded_wait_and_does_not_count_death():
    farmer, controller, now = run_sequence([.8, None], pickup_loot=False)
    assert now[0] >= 2
    assert now[0] < 3
    assert not farmer.mob_statistics.counts
    assert controller.key.call_count == 1
    assert farmer.mob_statistics.unconfirmed_kills == 1
    assert farmer.training_kills == 0


def test_each_unconfirmed_fight_counts_once_even_when_loot_is_disabled():
    farmer, _, _, _ = harness(pickup_loot=False)
    for _ in range(2):
        readings = iter([.8, None])
        farmer.observe = lambda: (1., 1., next(readings, None))
        farmer.fight(1.)
    assert farmer.mob_statistics.unconfirmed_kills == 2
    assert not farmer.mob_statistics.counts


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


@pytest.mark.parametrize("initial_level,current_level", [(None, 32), (32, None)])
def test_level_becoming_known_or_missing_is_not_target_change(initial_level, current_level):
    farmer, _, _, _ = harness()
    farmer.last_target = TargetObservation(at=0, kind="monster", name="Mob", level=current_level,
                                          confidence=.95)
    farmer.observe = lambda: (1., 1., 0.)
    assert farmer.verify_target(1., 1., 0., ("Mob", initial_level))[3]


@pytest.mark.parametrize("initial_level,current_level", [(None, 32), (32, None)])
def test_full_fight_counts_death_when_same_monster_level_changes_visibility(initial_level, current_level):
    farmer, controller, _, _ = harness(pickup_loot=True)
    farmer.last_target = TargetObservation(at=0, kind="monster", name="Mob", level=initial_level,
                                          hp=1., dead=False, confidence=.99)
    readings = iter([(.8, initial_level), (0., current_level), (0., current_level)])

    def observe():
        hp, level = next(readings, (0., current_level))
        farmer.last_target = TargetObservation(at=farmer.clock(), kind="monster", name="Mob",
                                              level=level, hp=hp, dead=hp == 0, confidence=.99)
        return 1., 1., hp

    farmer.observe = observe
    farmer.fight(1.)
    assert sum(farmer.mob_statistics.counts.values()) == 1
    assert farmer.mob_statistics.unconfirmed_kills == 0
    assert farmer.config.loot in [call.args[0] for call in controller.key.call_args_list]


def test_real_target_change_during_fight_counts_unconfirmed_once():
    farmer, _, _, _ = harness()
    farmer.last_target = TargetObservation(at=0, kind="monster", name="First", level=32,
                                          hp=1., confidence=.99)

    def observe():
        farmer.last_target = TargetObservation(at=farmer.clock(), kind="monster", name="Other",
                                              level=32, hp=.8, confidence=.99)
        return 1., 1., .8

    farmer.observe = observe
    farmer.fight(1.)
    assert farmer.mob_statistics.unconfirmed_kills == 1
    assert not farmer.mob_statistics.counts


def test_unknown_hp_reports_why_enabled_autoloot_was_skipped():
    farmer, controller, _ = run_sequence([.8, None], pickup_loot=True)
    assert not farmer.mob_statistics.counts
    assert farmer.config.loot not in [call.args[0] for call in controller.key.call_args_list]
    assert farmer.current_action == "Автолут после боя пропущен: смерть цели не подтверждена"
