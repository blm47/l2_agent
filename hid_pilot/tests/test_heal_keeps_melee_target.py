from unittest.mock import Mock

import pytest

from test_melee_farm import harness
from test_duo import make_duo


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
    farmer, controller, _, _ = harness(hp_threshold=.2, heal_delay=.5,
                                     heal_attempts=2, pickup_loot=False)
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


def test_fourteen_second_potion_cooldown_keeps_observing_and_allows_urgent_ud():
    duo, controller = make_duo(heal_key=None, heal="F1", heal_delay=14,
                               hp_threshold=.3, ud_key="F6", ud_hp_threshold=.19,
                               ud_damage_per_second=.15, pickup_loot=False)
    captures = []

    def capture():
        potion = (1, "F1") in controller.events
        if potion:
            captures.append(controller.now)
        ud = (1, "F6") in controller.events
        return (.18 if potion else .226, 0. if ud else .225, False)

    controller.capture = capture
    duo.fight(.9)
    assert controller.events.count((1, "F1")) == 1
    assert controller.events.count((1, "F6")) == 1
    assert controller.events.index((1, "F1")) < controller.events.index((1, "F6"))
    assert controller.now < 3
    assert max(after - before for before, after in zip(captures, captures[1:])) <= 1
    assert duo.own_heal_ready > controller.now


def test_fast_next_target_does_not_wait_for_potion_cooldown():
    farmer, controller, now, _ = harness(experimental_fast_farm=True,
                                        hp_threshold=.3, heal_delay=14)
    farmer.observe = lambda: (.226, 1., .06 if farmer.own_heal_ready > now[0] else .225)
    farmer.switch_fast_target = Mock()
    farmer.verify_target = Mock()
    farmer.fight(.9)
    farmer.switch_fast_target.assert_called_once_with()
    farmer.verify_target.assert_not_called()
    assert now[0] < 2
    assert [call.args[0] for call in controller.key.call_args_list].count(farmer.config.heal) == 1


def test_potion_cooldown_survives_next_fight_and_expires_at_configured_time():
    farmer, controller, now, _ = harness(hp_threshold=.3, heal_delay=14,
                                        target_verify_seconds=.5, pickup_loot=False)

    def fight():
        readings = iter([.5])
        farmer.observe = lambda: (.2, 1., next(readings, None))
        farmer.fight(.9)

    fight()
    ready = farmer.own_heal_ready
    fight()
    assert [call.args[0] for call in controller.key.call_args_list].count(farmer.config.heal) == 1
    now[0] = ready
    fight()
    assert [call.args[0] for call in controller.key.call_args_list].count(farmer.config.heal) == 2


@pytest.mark.parametrize("skill,settings,role", [
    ("F9", {"heal_key": "F9"}, 2),
    ("F7", {"support_party_heal_key": "F7"}, 2),
    ("F2", {"vampiric_key": "F2"}, 1),
])
def test_other_heals_remain_available_during_fifteen_second_potion_regen(skill, settings, role):
    duo, controller = make_duo(heal="F1", heal_key=None, heal_delay=15,
                               hp_threshold=.3, pickup_loot=False)
    original_key = controller.key

    def key(name, duration, **kwargs):
        original_key(name, duration, **kwargs)
        if controller.role.hwnd == 1 and name == "F1":
            duo.config = duo.config.model_copy(update=settings)

    controller.key = key
    controller.capture = lambda: (.226, 0. if (role, skill) in controller.events else .9, False)
    duo.fight(.9)
    assert controller.events.index((1, "F1")) < controller.events.index((role, skill))
    assert controller.events.count((1, "F1")) == 1
    assert controller.now < 15
    assert duo.own_heal_ready > controller.now
