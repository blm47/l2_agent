import pytest
from pydantic import ValidationError

from hid_pilot.controller import Stopped
from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from hid_pilot.farm_settings import setting_description, setting_group
from hid_pilot.party import PartyMember
from hid_pilot.target import TargetObservation
from test_duo import FARMER, make_duo
from test_duo_self_heal import observed_duo
from test_duo_summon import summon_duo
from test_summon import summon_scene


def test_mass_aggro_settings_roundtrip_and_have_gui_descriptions():
    config = DuoConfig(mass_aggro_key="Q")
    assert parse_duo_settings(format_duo_settings(config)) == config
    assert config.mass_aggro_cast_seconds == 1
    assert config.mass_aggro_cooldown == 40
    assert config.mass_aggro_hp_threshold == .33
    assert config.mass_aggro_damage_per_second == .15
    for field in DuoConfig.model_fields:
        if field.startswith("mass_aggro_"):
            assert setting_group(field) == "МА — масс-агр"
            assert setting_description(field)


@pytest.mark.parametrize("changes", [
    {"mass_aggro_key": "ALT+TAB"}, {"mass_aggro_cooldown": 39.9},
    {"mass_aggro_cast_seconds": 0}, {"mass_aggro_hp_threshold": 0},
    {"mass_aggro_damage_per_second": 0},
])
def test_invalid_mass_aggro_settings_are_rejected(changes):
    with pytest.raises(ValidationError):
        DuoConfig(**changes)


@pytest.mark.parametrize("hp,expected", [(.329, True), (.33, False), (.7, False), (None, False)])
def test_low_support_hp_triggers_mass_aggro_without_selecting_target(monkeypatch, hp, expected):
    duo, controller, _ = observed_duo(
        monkeypatch, hp, heal_key=None, support_self_heal_key=None, mass_aggro_key="Q")
    presses = []
    original = controller.key

    def key(name, duration, **kwargs):
        presses.append((controller.role, name, duration, kwargs))
        original(name, duration, **kwargs)

    controller.key = key
    duo.observe()
    assert ((1, "Q") in controller.events) is expected
    if expected:
        assert presses == [(FARMER, "Q", 300, {"exact": True})]
        assert controller.now == pytest.approx(1)
        assert any("саппорт: HP 32.9%" in call.args[0] for call in duo.notify.call_args_list)


def test_summon_counts_as_party_and_does_not_need_a_selected_target():
    duo, controller, _ = summon_duo(.06, support_party_heal_key=None, mass_aggro_key="Q")
    controller.capture = lambda: (1., None, False)
    duo.observe()
    assert controller.events == [(1, "Q")]


@pytest.mark.parametrize("elapsed,loss,expected", [
    (.2, .029, False), (.2, .03, True), (.2, .04, True),
    (1., .15, True), (2., .3, False),
])
def test_support_damage_rate_uses_actual_fresh_interval(monkeypatch, elapsed, loss, expected):
    duo, controller, reader = observed_duo(
        monkeypatch, .9, heal_key=None, support_self_heal_key=None, mass_aggro_key="Q")
    duo.observe()
    controller.wait(elapsed)
    reader.return_value = .9 - loss
    duo.observe()
    assert ((1, "Q") in controller.events) is expected


@pytest.mark.parametrize("resource", ["hp", "mp"])
def test_summon_hp_damage_triggers_mass_aggro_but_mp_spending_does_not(resource):
    duo, controller, frame = summon_duo(1., support_party_heal_key=None, mass_aggro_key="Q")
    duo.observe()
    controller.wait(.2)
    frame[:] = summon_scene(**{resource: .95})[0]
    duo.observe()
    assert ((1, "Q") in controller.events) is (resource == "hp")


def test_other_party_member_hp_damage_is_tracked_independently(monkeypatch):
    duo, controller, _ = observed_duo(
        monkeypatch, 1., heal_key=None, support_self_heal_key=None, mass_aggro_key="Q")
    duo.display_data = ({}, {}, [PartyMember(slot=3, hp=.9)])
    duo.track_damage(None, 1.)
    controller.wait(.2)
    duo.display_data = ({}, {}, [PartyMember(slot=3, hp=.85)])
    duo.track_damage(None, 1.)
    duo.last_mp = 1.
    assert duo.party_hp_damage_rates["party_3_hp"] == pytest.approx(.25)
    assert duo.use_mass_aggro()
    assert controller.events == [(1, "Q")]


def test_only_farmer_damage_does_not_trigger_party_rescue():
    duo, controller = make_duo(heal_key=None, mass_aggro_key="Q")
    controller.capture = lambda: (.1, .9, False)
    duo.observe()
    assert (1, "Q") not in controller.events


@pytest.mark.parametrize("blocked", ["disabled", "unknown_hp", "unknown_mp", "no_mp", "stale", "support_dead"])
def test_mass_aggro_requires_known_safe_fresh_resources(blocked):
    duo, controller, _ = summon_duo(.06, support_party_heal_key=None, mass_aggro_key="Q")
    duo.track_damage(None, 1.)
    duo.last_mp = 1.
    if blocked == "disabled":
        duo.config.mass_aggro_key = None
    elif blocked == "unknown_hp":
        duo.previous_resources["farmer_hp"] = None
    elif blocked == "unknown_mp":
        duo.last_mp = None
    elif blocked == "no_mp":
        duo.last_mp = 0.
    elif blocked == "stale":
        controller.wait(2)
    else:
        duo.support_lost = True
    assert not duo.use_mass_aggro()
    assert controller.events == []


@pytest.mark.parametrize("interruption", ["pause", "gap", "unknown", "healing"])
def test_damage_rate_does_not_cross_pause_gaps_unknown_or_healing(monkeypatch, interruption):
    duo, controller, reader = observed_duo(
        monkeypatch, .9, heal_key=None, support_self_heal_key=None, mass_aggro_key="Q")
    duo.observe()
    controller.wait(.2)
    if interruption == "pause":
        controller.pause_generation += 1
    elif interruption == "gap":
        controller.wait(2)
    elif interruption == "unknown":
        reader.return_value = None
        duo.observe()
        controller.wait(.2)
    else:
        reader.return_value = .6
        duo.track_damage(None, 1.)
        controller.wait(.2)
    reader.return_value = .8
    duo.observe()
    assert (1, "Q") not in controller.events


def test_cooldown_starts_at_send_and_prevents_recursive_casts():
    duo, controller, _ = summon_duo(.06, support_party_heal_key=None, mass_aggro_key="Q")
    duo.observe()
    assert controller.events.count((1, "Q")) == 1
    assert duo.mass_aggro_ready_at == 40
    assert not duo.mass_aggro_casting
    controller.now = 39.99
    duo.observe()
    assert controller.events.count((1, "Q")) == 1
    controller.pause_generation += 1
    controller.now = 40
    duo.observe()
    assert controller.events.count((1, "Q")) == 2


@pytest.mark.parametrize("kind", ["monster", "player", "npc"])
def test_mass_aggro_keeps_current_target_and_blocks_unsafe_selected_panel(kind):
    duo, controller, _ = summon_duo(.06, support_party_heal_key=None, mass_aggro_key="Q")
    target = TargetObservation(at=0, kind=kind, name="Target", hp=.9)
    duo.last_target = target
    duo.observe()
    assert ((1, "Q") in controller.events) is (kind == "monster")
    if kind == "monster":
        assert duo.last_target is target
        assert controller.events == [(1, "Q")]


def test_mass_aggro_precedes_f9_and_does_not_drop_live_target():
    duo, controller, _ = summon_duo(.06, support_party_heal_key="F9", mass_aggro_key="Q")
    duo.last_target = TargetObservation(at=0, kind="monster", hp=.9)
    duo.observe()
    assert controller.events.index((1, "Q")) < controller.events.index((2, "F9"))
    assert (1, duo.config.next_target) not in controller.events


def test_stop_during_mass_aggro_hold_does_not_start_cooldown():
    duo, controller, _ = summon_duo(.06, support_party_heal_key=None, mass_aggro_key="Q")

    def stop(*args, **kwargs):
        raise Stopped("STOP")

    controller.key = stop
    with pytest.raises(Stopped):
        duo.observe()
    assert duo.mass_aggro_ready_at == float("-inf")
    assert not duo.mass_aggro_casting


def test_mass_aggro_cast_wait_still_services_farmer_elixir():
    duo, controller, _ = summon_duo(
        .06, support_party_heal_key=None, mass_aggro_key="Q",
        hp_elixir_key="NUM2", hp_elixir_hp_threshold=.12)
    hp = [1.]
    controller.capture = lambda: (hp[0], .9, False)
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        hp[0] = .1 if name == "Q" else .7

    controller.key = key
    duo.observe()
    assert controller.events.index((1, "Q")) < controller.events.index((1, "NUM2"))
