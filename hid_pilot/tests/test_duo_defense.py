import pytest

from test_duo import make_duo, FARMER
from test_melee_farm import harness
from hid_pilot.target import TargetObservation


def test_falling_hp_without_target_selects_and_attacks_before_support_heal():
    farmer, controller = make_duo(heal_seconds=2)
    farmer.last_resources = (.8, None)
    state = [.5, None]
    original_key = controller.key

    def key(name, duration, **kwargs):
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

    def key(name, duration, **kwargs):
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


def test_support_only_damage_selects_aggressor_without_farmer_damage(monkeypatch):
    from test_duo_self_heal import observed_duo

    farmer, controller, reader = observed_duo(monkeypatch, .9, heal_key=None, support_self_heal_key=None)
    live = [False]
    controller.capture = lambda: (1., .9 if live[0] else None, False)
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name == farmer.config.next_target:
            live[0] = True

    controller.key = key
    farmer.observe()
    controller.wait(.2)
    reader.return_value = .7
    farmer.observe()
    assert controller.events[:2] == [(1, farmer.config.next_target), (1, farmer.config.attack)]


def test_party_damage_keeps_existing_living_target_instead_of_next_target(monkeypatch):
    from test_duo_self_heal import observed_duo

    farmer, controller, reader = observed_duo(monkeypatch, .9, heal_key=None, support_self_heal_key=None)
    controller.capture = lambda: (1., .7, False)
    farmer.observe()
    controller.wait(.2)
    reader.return_value = .7
    farmer.observe()
    assert (1, farmer.config.attack) in controller.events
    assert (1, farmer.config.next_target) not in controller.events


@pytest.mark.parametrize("resource", ["hp", "cp"])
def test_other_party_member_damage_is_part_of_defense(monkeypatch, resource):
    from hid_pilot.party import PartyMember
    from test_duo_self_heal import observed_duo

    farmer, controller, _ = observed_duo(monkeypatch, 1., heal_key=None, support_self_heal_key=None)
    farmer.display_data = ({}, {}, [PartyMember(slot=3, hp=1., cp=1.)])
    farmer.track_damage(None, 1.)
    controller.wait(.2)
    farmer.display_data = ({}, {}, [PartyMember(slot=3, **{"hp": 1., "cp": 1., resource: .7})])
    farmer.track_damage(None, 1.)
    assert farmer.party_damage_now
    assert f"party_3_{resource}" in farmer.party_damage_sources
    assert farmer.damage_until > controller.now


@pytest.mark.parametrize("gap,pause,unknown", [(2, False, False), (.2, True, False), (.2, False, True)])
def test_stale_paused_or_missing_party_hp_does_not_invent_damage(monkeypatch, gap, pause, unknown):
    from hid_pilot.party import PartyMember
    from test_duo_self_heal import observed_duo

    farmer, controller, _ = observed_duo(monkeypatch, 1., heal_key=None, support_self_heal_key=None)
    farmer.display_data = ({}, {}, [PartyMember(slot=3, hp=1., cp=1.)])
    farmer.track_damage(None, 1.)
    controller.wait(gap)
    controller.pause_generation += int(pause)
    farmer.display_data = ({}, {}, [PartyMember(slot=3, hp=None if unknown else .7, cp=1.)])
    farmer.track_damage(None, 1.)
    assert not farmer.party_damage_now


def test_target_verification_under_party_damage_does_not_select_another_target(monkeypatch):
    from test_duo_self_heal import observed_duo

    farmer, controller, reader = observed_duo(monkeypatch, .9, heal_key=None, support_self_heal_key=None)
    controller.capture = lambda: (1., None, False)
    farmer.observe()
    controller.wait(.2)
    reader.return_value = .7
    farmer.verify_target(1., 1., None, None)
    assert not farmer.verifying_target
    assert (1, farmer.config.next_target) not in controller.events


def test_damaged_target_is_not_dropped_after_short_attack_gap_under_fire():
    farmer, controller, now, _ = harness(stuck_timeout=7, pickup_loot=False)

    def observe():
        farmer.under_attack = True
        # Атаки раз в три секунды: это дольше старого укороченного таймаута 1,7 с.
        target = max(0., 1. - .2 * (1 + int(now[0] // 3)))
        return .8, 1., target

    farmer.observe = observe
    farmer.fight(1.)
    assert farmer.training_kills == 1
    assert "ESC" not in [call.args[0] for call in controller.key.call_args_list]


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
