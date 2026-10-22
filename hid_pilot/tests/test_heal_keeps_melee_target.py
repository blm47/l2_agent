from unittest.mock import Mock

from test_melee_farm import harness


def test_self_heal_does_not_end_fight_or_select_next_target_before_death():
    farmer, controller, _, _ = harness(hp_threshold=.2)
    values = iter([(.1, 1., .7), (.4, 1., .7), (.4, 1., .3), (.4, 1., 0.), (.4, 1., 0.)])
    farmer.observe = lambda: next(values)
    farmer.acquire = Mock(side_effect=AssertionError("Живая цель не должна заменяться"))
    farmer.recover = Mock(side_effect=AssertionError("Нельзя садиться в середине боя"))
    farmer.fight(1.)
    assert [call.args[0] for call in controller.key.call_args_list] == ["R", "2", "R", "5"]
    assert sum(farmer.mob_statistics.counts.values()) == 1
    farmer.acquire.assert_not_called()
    farmer.recover.assert_not_called()


def test_persistently_low_hp_does_not_cause_unbounded_heal_or_leave_live_target():
    farmer, controller, _, _ = harness(hp_threshold=.2, heal_attempts=2, pickup_loot=False)
    readings = iter([.8, .7, .6, .4, 0., 0.])
    farmer.observe = lambda: (.1, 1., next(readings))
    farmer.fight(1.)
    assert [call.args[0] for call in controller.key.call_args_list] == ["R", "2", "R", "2", "R"]
    assert sum(farmer.mob_statistics.counts.values()) == 1


def test_heal_followed_by_target_loss_never_counts_death_or_loots():
    farmer, controller, _, _ = harness(hp_threshold=.2)
    readings = iter([.8])
    farmer.observe = lambda: (.1, 1., next(readings, None))
    farmer.fight(1.)
    assert [call.args[0] for call in controller.key.call_args_list] == ["R", "2"]
    assert not farmer.mob_statistics.counts
