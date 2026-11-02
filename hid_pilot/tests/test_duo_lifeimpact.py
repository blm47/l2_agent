from pathlib import Path

import pytest
from pydantic import ValidationError

from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from hid_pilot.farm_settings import setting_description, setting_group
from test_duo import FARMER
from test_duo_emergency_support import record_keys
from test_duo_self_heal import observed_duo


def lifeimpact_duo(monkeypatch, remaining=5., **settings):
    duo, controller, _ = observed_duo(
        monkeypatch, 1., support_lifeimpact_key="9", support_self_heal_key=None,
        heal_key=None, **settings)
    controller.now = 20.
    controller.capture = lambda: (.5, .9, False)
    duo.observe_ma()
    duo.support_ma_chant_until = controller.now + remaining
    duo.support_party_regen_until = duo.support_ma_chant_until
    return duo, controller


@pytest.mark.parametrize("hp,remaining,expected", [
    (.6999, 9.999, True), (.7, 5., False), (.71, 5., False),
    (.5, 10., False), (.5, 10.001, False), (.5, .1, True),
    (.1999, 15., True), (.2, 15., False), (.2, 9.99, True),
    (.1, 15.01, False), (.1, 0., False), (.1, -.1, False),
    (0., 5., False), (None, 5., False),
])
def test_lifeimpact_strict_hp_and_chant_time_boundaries(monkeypatch, hp, remaining, expected):
    duo, controller = lifeimpact_duo(monkeypatch, remaining)
    presses = record_keys(controller)
    party_until = duo.support_party_regen_until
    assert duo.use_support_lifeimpact(hp) is expected
    if not expected:
        assert controller.events == []
        return
    assert controller.events == [("window", 2), (2, "9"), (2, "9"), ("window", 1)]
    assert [item[:3] for item in presses] == [("9", 80, {"exact": True})] * 2
    assert presses[1][3] - presses[0][3] == pytest.approx(.085)
    assert controller.now == pytest.approx(21.)
    assert controller.role == FARMER
    assert duo.support_lifeimpact_ready == pytest.approx(35.)
    assert duo.support_ma_chant_until == float("-inf")
    assert duo.support_party_regen_until == party_until
    assert duo.following


@pytest.mark.parametrize("condition", [
    "no_chant", "stale", "pause", "pause_after_fresh_frame", "buff", "lost",
    "unknown_support", "dead_support", "no_party", "disabled", "cooldown",
])
def test_lifeimpact_requires_safe_state_and_unconsumed_chant(monkeypatch, condition):
    duo, controller = lifeimpact_duo(monkeypatch)
    if condition == "no_chant":
        duo.support_ma_chant_until = float("-inf")
    elif condition == "stale":
        duo.resources_at -= 1.01
    elif condition.startswith("pause"):
        controller.pause_generation += 1
        if condition == "pause_after_fresh_frame":
            duo.observe_ma()
    elif condition == "buff":
        duo.buffing = True
    elif condition == "lost":
        duo.support_lost = True
    elif condition in {"unknown_support", "dead_support"}:
        duo.previous_resources["support_hp"] = None if condition == "unknown_support" else 0.
    elif condition == "no_party":
        duo.party_profile = None
    elif condition == "disabled":
        duo.config.support_lifeimpact_key = None
    else:
        duo.support_lifeimpact_ready = controller.now + .01
    assert not duo.use_support_lifeimpact(.1)
    assert controller.events == []


@pytest.mark.parametrize("interruption", ["expiry", "pause", "stale"])
def test_lifeimpact_rechecks_chant_after_window_switch(monkeypatch, interruption):
    duo, controller = lifeimpact_duo(monkeypatch, remaining=.2)
    switch = controller.switch_window

    def delayed_switch(target):
        switch(target)
        if target != duo.support:
            return
        if interruption == "pause":
            controller.pause_generation += 1
        else:
            controller.now += .3 if interruption == "expiry" else 1.1

    controller.switch_window = delayed_switch
    assert not duo.use_support_lifeimpact(.1)
    assert (2, "9") not in controller.events
    assert duo.support_lifeimpact_ready == float("-inf")
    assert controller.role == FARMER


def test_lifeimpact_needs_new_chant_and_its_own_fifteen_second_cooldown(monkeypatch):
    duo, controller = lifeimpact_duo(monkeypatch)
    assert duo.use_support_lifeimpact(.1)
    ready = duo.support_lifeimpact_ready
    duo.observe_ma()
    assert not duo.use_support_lifeimpact(.1)
    duo.support_ma_chant_until = ready + 10
    controller.now = ready - .01
    duo.observe_ma()
    assert not duo.use_support_lifeimpact(.1)
    controller.now = ready
    duo.observe_ma()
    assert duo.use_support_lifeimpact(.1)
    assert controller.events.count((2, "9")) == 4


def test_chant_cast_enables_emergency_lifeimpact_on_next_observation(monkeypatch):
    duo, controller, _ = observed_duo(
        monkeypatch, 1., heal_key=None, support_self_heal_key=None,
        support_party_heal_key="F9", support_lifeimpact_key="9")
    controller.capture = lambda: (.19, .9, False)
    duo.observe()
    assert controller.events.count((2, "F9")) == 2
    assert (2, "9") not in controller.events
    assert duo.support_ma_chant_until == duo.support_party_regen_until
    duo.observe()
    assert controller.events.count((2, "9")) == 2
    assert controller.events.count((2, "F9")) == 2
    assert duo.support_ma_chant_until == float("-inf")


def test_available_lifeimpact_precedes_chant_refresh(monkeypatch):
    duo, controller = lifeimpact_duo(monkeypatch, support_party_heal_key="F9")
    assert duo.support_maintenance(.5, .9)
    assert controller.events.count((2, "9")) == 2
    assert (2, "F9") not in controller.events


@pytest.mark.parametrize("hp", [.7, 1.])
def test_lifeimpact_does_not_treat_summon_or_support(monkeypatch, hp):
    duo, controller = lifeimpact_duo(monkeypatch)
    duo.previous_resources.update(support_hp=.1, summon_hp=.1)
    assert not duo.support_maintenance(hp, None)
    assert (2, "9") not in controller.events


def test_chant_interrupted_by_pause_does_not_enable_lifeimpact(monkeypatch):
    duo, controller, _ = observed_duo(
        monkeypatch, 1., heal_key=None, support_self_heal_key=None,
        support_party_heal_key="F9", support_lifeimpact_key="9")
    controller.capture = lambda: (.19, .9, False)
    press = controller.key

    def pause_after_chant(key, duration, **kwargs):
        press(key, duration, **kwargs)
        if key == "F9":
            controller.pause_generation += 1

    controller.key = pause_after_chant
    duo.observe()
    duo.observe_ma()
    assert not duo.use_support_lifeimpact(.19)
    assert (2, "9") not in controller.events


def test_lifeimpact_settings_and_profile_are_exposed():
    config = DuoConfig()
    assert (config.support_lifeimpact_key, config.support_lifeimpact_cast_seconds,
            config.support_lifeimpact_cooldown) == ("9", 1., 15.)
    assert parse_duo_settings(format_duo_settings(config)) == config
    for name in DuoConfig.model_fields:
        if name.startswith("support_lifeimpact_"):
            assert setting_group(name) == "Саппорт — LifeImpact"
            assert setting_description(name)
    assert DuoConfig(support_lifeimpact_key=" ").support_lifeimpact_key is None
    with pytest.raises(ValidationError):
        DuoConfig(support_lifeimpact_key="ALT+TAB")
    with pytest.raises(ValidationError):
        DuoConfig(support_lifeimpact_cooldown=14.99)
    path = Path(__file__).parents[1] / "extensions/scripts/farmer-healer.duo.pilot"
    profile = parse_duo_settings(path.read_text("utf-8"))
    assert profile.support_lifeimpact_key == "9"
    assert profile.support_lifeimpact_hp_threshold == .7
