import pytest
from pydantic import ValidationError

from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from hid_pilot.farm_settings import setting_group, setting_description
from test_duo_self_heal import observed_duo
from test_duo_summon import summon_duo
from test_summon import summon_scene


def ready_preserv(monkeypatch, hp=.46, rate=None, **settings):
    duo, controller, _ = observed_duo(
        monkeypatch, 1., support_preserv_key="=", support_self_heal_key=None,
        heal_key=None, **settings)
    duo.previous_resources["support_hp"] = 1.
    duo.resources_at = controller.now
    duo.resource_generation = controller.pause_generation
    duo.ud_damage_rate = rate
    return duo, controller


@pytest.mark.parametrize("hp,rate,expected", [
    (.46, None, True), (.47, None, False), (.9, .051, True),
    (.9, .05, False), (0., .2, False),
])
def test_preserv_threshold_and_damage(monkeypatch, hp, rate, expected):
    duo, controller = ready_preserv(monkeypatch, hp, rate)
    assert duo.use_support_preserv(hp) is expected
    assert ((2, "=") in controller.events) is expected
    if expected:
        assert controller.events == [("window", 2), (2, "="), (2, "="), ("window", 1)]
        assert controller.now == pytest.approx(1)


def test_preserv_cooldown_is_twenty_seconds_from_send(monkeypatch):
    duo, controller = ready_preserv(monkeypatch)
    assert duo.use_support_preserv(.4)
    assert duo.support_preserv_ready == 20
    duo.resources_at = controller.now
    assert not duo.use_support_preserv(.4)
    controller.now = 20
    duo.resources_at = controller.now
    assert duo.use_support_preserv(.4)


@pytest.mark.parametrize("condition", ["stale", "pause", "buff", "lost", "no_party", "unknown_support", "disabled"])
@pytest.mark.parametrize("wounded", ["ma", "summon"])
def test_preserv_requires_safe_fresh_party_state(monkeypatch, condition, wounded):
    duo, controller = ready_preserv(monkeypatch)
    duo.previous_resources["summon_hp"] = .4 if wounded == "summon" else 1.
    if condition == "stale":
        controller.now = 2
    elif condition == "pause":
        controller.pause_generation += 1
    elif condition == "buff":
        duo.buffing = True
    elif condition == "lost":
        duo.support_lost = True
    elif condition == "no_party":
        duo.party_profile = None
    elif condition == "disabled":
        duo.config.support_preserv_key = None
    else:
        duo.previous_resources["support_hp"] = None
    assert not duo.use_support_preserv(.4 if wounded == "ma" else 1.)
    assert controller.events == []


def test_preserv_precedes_party_regen(monkeypatch):
    duo, controller = ready_preserv(monkeypatch, support_party_heal_key="F9")
    assert duo.support_maintenance(.4, .9)
    assert (2, "=") in controller.events
    assert (2, "F9") not in controller.events


def test_preserv_settings_roundtrip_and_descriptions():
    config = DuoConfig()
    assert config.support_preserv_key == "="
    assert config.support_preserv_cast_seconds == 1
    assert config.support_preserv_hp_threshold == .47
    assert parse_duo_settings(format_duo_settings(config)) == config
    for field in DuoConfig.model_fields:
        if field.startswith("support_preserv_"):
            assert setting_group(field) == "Саппорт — Preserv"
            assert setting_description(field)
    assert DuoConfig(support_preserv_key="").support_preserv_key is None
    with pytest.raises(ValidationError):
        DuoConfig(support_preserv_key="ALT+TAB")
    assert "саммона" in setting_description("support_preserv_hp_threshold")
    assert "10 секунд" in setting_description("support_preserv_key")
    assert "пантеры" in setting_description("support_preserv_damage_per_second")


@pytest.mark.parametrize("summon_hp,threshold,expected", [
    (.46, .47, True), (.47, .47, False), (.48, .47, False),
    (.59, .6, True), (.6, .6, False), (None, .47, False), (0., .47, False),
])
def test_summon_uses_same_configured_hp_threshold_as_ma(monkeypatch, summon_hp, threshold, expected):
    duo, controller = ready_preserv(monkeypatch, support_preserv_hp_threshold=threshold)
    duo.previous_resources["summon_hp"] = summon_hp
    assert duo.use_support_preserv(1.) is expected
    assert controller.events == ([("window", 2), (2, "="), (2, "="), ("window", 1)] if expected else [])


@pytest.mark.parametrize("hp,rate", [(.4, None), (.9, .06)])
@pytest.mark.parametrize("summon_hp", [None, 0., 1.])
def test_summon_state_does_not_disable_ma_preserv(monkeypatch, hp, rate, summon_hp):
    duo, _ = ready_preserv(monkeypatch, rate=rate)
    duo.previous_resources["summon_hp"] = summon_hp
    assert duo.use_support_preserv(hp)


def test_summon_and_ma_share_one_macro_and_cooldown(monkeypatch):
    duo, controller = ready_preserv(monkeypatch)
    duo.previous_resources["summon_hp"] = .2
    assert duo.use_support_preserv(.3)
    assert duo.support_preserv_ready == 20
    assert controller.now == 1
    message = duo.notify.call_args.args[0]
    assert "МА HP 30.0%" in message
    assert "саммон HP 20.0%" in message
    assert "длительность баффа 10 с" in message
    for now in (1., 11., 19.9):
        controller.now = duo.resources_at = now
        assert not duo.use_support_preserv(1.)
    controller.now = duo.resources_at = 20.
    assert duo.use_support_preserv(1.)
    assert controller.events.count((2, "=")) == 4


@pytest.mark.parametrize("missing_panel", [False, True])
def test_summon_panel_in_ma_frame_drives_preserv_before_party_regen(missing_panel):
    duo, controller, frame = summon_duo(.4, support_preserv_key="=", support_party_heal_key="F9")
    duo.party_profile = object()
    duo.support_resources = lambda image: {"support_hp": 1., "support_mp": 1.}
    if missing_panel:
        frame[100:150] = 20
    duo.observe()
    assert duo.previous_resources["summon_hp"] == (None if missing_panel else .4)
    assert controller.events == ([] if missing_panel else [("window", 2), (2, "="), (2, "="), ("window", 1)])
    assert controller.now == (0 if missing_panel else 1)


def summon_damage_duo(**settings):
    duo, controller, frame = summon_duo(
        1., support_preserv_key="=", support_party_heal_key=None, **settings)
    duo.party_profile = object()
    duo.support_resources = lambda image: {"support_hp": 1., "support_mp": 1.}
    duo.track_damage(None, 1.)
    return duo, controller, frame


@pytest.mark.parametrize("elapsed,loss,threshold,expected", [
    (.25, .05, .15, True), (.25, .05, .2, False), (.25, .05, .21, False),
    (.5, .05, .07, True), (.5, .03, .07, False), (1.01, .2, .05, False),
])
def test_summon_damage_can_trigger_preserv_above_hp_threshold(elapsed, loss, threshold, expected):
    duo, controller, frame = summon_damage_duo(support_preserv_damage_per_second=threshold)
    controller.wait(elapsed)
    frame[:] = summon_scene(1. - loss)[0]
    duo.track_damage(None, 1.)
    assert duo.previous_resources["summon_hp"] > duo.config.support_preserv_hp_threshold
    assert duo.use_support_preserv(1.) is expected
    assert controller.events.count((2, "=")) == (2 if expected else 0)
    if expected:
        assert "сильный урон по пантере" in duo.notify.call_args.args[0]
        assert duo.support_preserv_ready == pytest.approx(elapsed + 20)


def test_summon_damage_preserv_survives_mass_aggro_cast_and_refresh():
    duo, controller, frame = summon_damage_duo(mass_aggro_key="Q")
    controller.wait(.25)
    frame[:] = summon_scene(.9)[0]
    duo.observe()
    assert controller.events.index((1, "Q")) < controller.events.index((2, "="))
    assert controller.events.count((2, "=")) == 2
    assert duo.previous_resources["summon_hp"] == .9
    assert (1, "ESC") not in controller.events


@pytest.mark.parametrize("interruption", [
    "pause", "gap", "missing", "healed", "dead", "ma_unknown", "expired", "stale",
])
def test_summon_damage_cannot_use_invalid_or_expired_observations(interruption):
    duo, controller, frame = summon_damage_duo()
    controller.wait(.25)
    frame[:] = summon_scene(.9)[0]
    duo.track_damage(None, 1.)
    assert duo.summon_damage_rate == pytest.approx(.4)
    if interruption == "ma_unknown":
        duo.on_observation(frame, None, 1., .9)
    elif interruption == "expired":
        for _ in range(8):
            controller.wait(.25)
            duo.track_damage(None, 1.)
    elif interruption == "stale":
        controller.wait(1.01)
    else:
        controller.wait(1.01 if interruption == "gap" else .25)
        if interruption == "pause":
            controller.pause_generation += 1
        elif interruption == "missing":
            frame[100:150] = 20
        elif interruption in {"healed", "dead"}:
            frame[:] = summon_scene(1. if interruption == "healed" else 0.)[0]
        duo.track_damage(None, 1.)
    assert not duo.use_support_preserv(1.)
    assert (2, "=") not in controller.events


def test_summon_mp_spending_does_not_trigger_preserv():
    duo, controller, frame = summon_damage_duo()
    controller.wait(.25)
    frame[:] = summon_scene(1., .4)[0]
    duo.track_damage(None, 1.)
    assert duo.summon_damage_rate is None
    assert not duo.use_support_preserv(1.)
    assert controller.events == []


def test_summon_damage_preserv_obeys_shared_cooldown():
    duo, controller, frame = summon_damage_duo()
    controller.wait(.25)
    frame[:] = summon_scene(.9)[0]
    duo.track_damage(None, 1.)
    assert duo.use_support_preserv(1.)
    controller.wait(.25)
    frame[:] = summon_scene(.8)[0]
    duo.track_damage(None, 1.)
    assert not duo.use_support_preserv(1.)
    assert controller.events.count((2, "=")) == 2
