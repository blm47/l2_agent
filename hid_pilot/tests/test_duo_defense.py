import pytest

from test_duo import make_duo, FARMER
from test_melee_farm import harness
from hid_pilot.target import TargetObservation


def test_falling_hp_without_target_selects_and_attacks_before_support_heal():
    farmer, controller = make_duo(heal_seconds=2)
    farmer.last_resources = (.8, None)
    state = [.5, None]
    original_key = controller.key

    def key(name, duration):
        original_key(name, duration)
        if controller.role == FARMER and name == farmer.config.next_target:
            state[1] = .9
        if name == farmer.config.heal_key:
            state[0] = .95

    controller.key = key
    controller.capture = lambda: (state[0], state[1], False)
    assert farmer.observe() == (.95, 1., .9)
    selection = controller.events.index((1, farmer.config.next_target))
    attack = controller.events.index((1, farmer.config.attack))
    heal = controller.events.index((2, farmer.config.heal_key))
    assert selection < attack < heal
    assert (1, farmer.config.loot) not in controller.events


def test_disabled_support_heal_does_not_disable_defense():
    farmer, controller = make_duo(heal_key=None)
    farmer.last_resources = (.8, None)
    live = [False]
    original_key = controller.key

    def key(name, duration):
        original_key(name, duration)
        if name == farmer.config.next_target:
            live[0] = True

    controller.key = key
    controller.capture = lambda: (.5, .9 if live[0] else None, False)
    farmer.observe()
    assert controller.events == [(1, farmer.config.next_target), (1, farmer.config.attack)]


def test_constant_low_hp_does_not_invent_incoming_damage():
    farmer, controller = make_duo(heal_key=None)
    farmer.last_resources = (.2, None)
    controller.capture = lambda: (.2, None, False)
    farmer.observe()
    assert controller.events == []


def test_pre_pause_hp_is_not_used_to_trigger_defense():
    farmer, controller = make_duo(heal_key=None)
    farmer.last_resources = (.8, None)
    controller.pause_generation += 1
    controller.capture = lambda: (.5, None, False)
    farmer.observe()
    assert controller.events == []


def test_defense_keeps_high_level_filter_and_returns_unknown_after_esc():
    farmer, controller = make_duo(heal_key=None)
    farmer.last_resources = (.8, None)
    farmer.display_data = ({"level": 20}, {}, [])
    farmer.last_target = TargetObservation(at=0, kind="monster", name="Strong Mob", level=30, confidence=.99)
    controller.capture = lambda: (.5, .9, False)
    assert farmer.observe()[2] is None
    assert controller.events == [(1, "ESC")]


def test_search_does_not_run_wasd_while_under_attack():
    farmer, controller, _, state = harness(regen_enabled=False, search_moves=2)
    farmer.under_attack = True
    state[:] = [.5, 1., None]
    with pytest.raises(RuntimeError, match="не нашли мобов"):
        farmer.acquire()
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target] * 3


def test_unreachable_target_is_dropped_quickly_when_taking_damage():
    farmer, controller, now, _ = harness(regen_enabled=False, approach_delay=11)
    farmer.under_attack = True
    farmer.fight(.9)
    assert now[0] < 3
    assert controller.key.call_args_list[-1].args[0] == "ESC"


def test_existing_defensive_attack_is_not_delayed_by_opening_loot():
    farmer, controller, _, state = harness(regen_enabled=False, pre_attack_loot=True, pickup_loot=True)
    farmer.attacking = True
    state[2] = None
    farmer.fight(.9)
    assert farmer.config.loot not in [call.args[0] for call in controller.key.call_args_list]


def test_buff_defense_does_not_use_overhit_as_target_selection():
    farmer, controller = make_duo(next_target="6", defense_target_key="2", overhit_keys=("2",))
    controller.frames = iter([(.9, None, False), (.8, None, False), (.8, .9, False)])
    farmer.observe()
    farmer.buff()
    assert (1, "6") in controller.events
    assert (1, "2") not in controller.events
