import pytest

from test_duo import make_duo
from test_melee_farm import harness


@pytest.mark.parametrize("hp,casts", [(.5, False), (.49, False), (.51, True), (.9, True)])
def test_combat_buffs_use_independent_fifty_percent_threshold(hp, casts):
    farmer, controller, _, state = harness(buff1_key="NUM9", hp_threshold=.7)
    farmer.attacking = True
    farmer.under_attack = True
    state[:] = [hp, 1., .9]
    assert farmer.service_combat_buffs(hp) is casts
    assert controller.key.called is casts


def test_due_self_buff_starts_inside_continuous_fight():
    farmer, controller, now, state = harness(
        regen_enabled=False, buff1_key="NUM9", buff1_cast_seconds=.5, hp_threshold=.3)
    farmer.buff_ready[1] = 1.
    state[:] = [.8, 1., .9]
    original_key = controller.key.side_effect

    def key(name, duration):
        original_key(name, duration)
        if name == "NUM9":
            state[2] = 0.

    controller.key.side_effect = key
    farmer.verify_target = lambda hp, mp, target, identity: (hp, mp, target, True)
    farmer.fight(.9)
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys.index(farmer.config.attack) < keys.index("NUM9") < keys.index(farmer.config.loot)
    assert farmer.buff_ready[1] > now[0]


def test_full_buff_can_start_on_timer_during_continuous_duo_combat():
    duo, controller = make_duo(heal_key=None)
    duo.next_buff = .5
    controller.capture = lambda: (.8, 0. if controller.now >= 3 else .9, False)
    duo.fight(.9)
    events = controller.events
    assert events.index((1, duo.config.attack)) < events.index((2, duo.config.buff_key))
    assert events.index((2, duo.config.buff_key)) < events.index((1, duo.config.loot))


def test_incoming_damage_does_not_retarget_dead_mob_before_confirmation_and_loot():
    duo, controller = make_duo(heal_key=None, pre_attack_loot=False)
    duo.next_buff = float("inf")
    controller.frames = iter([(.9, 0., False), (.8, 0., False), (.79, 0., False)])
    duo.fight(.9)
    assert (1, duo.config.next_target) not in controller.events
    assert controller.events.count((1, duo.config.loot)) == 1
    assert duo.training_kills == 1


def test_missing_target_in_active_fight_is_verified_before_defensive_retarget():
    duo, controller = make_duo(heal_key=None)
    duo.attacking = True
    controller.frames = iter([(.9, None, False), (.8, None, False)])
    duo.observe()
    duo.observe()
    assert (1, duo.config.next_target) not in controller.events
