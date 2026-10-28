from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from test_duo import FARMER, make_duo


def observed_duo(monkeypatch, support_hp, **settings):
    settings.setdefault("support_self_heal_key", "F2")
    duo, controller = make_duo(**settings)
    box = object()
    duo.party_profile = SimpleNamespace(box=Mock(return_value=box))
    duo.profile.crop = Mock(return_value=box)
    reader = Mock(return_value=support_hp)
    monkeypatch.setattr("hid_pilot.duo.bar_fraction", reader)
    return duo, controller, reader


def test_low_party_hp_uses_self_macro_and_returns_to_farmer(monkeypatch):
    duo, controller, _ = observed_duo(monkeypatch, .4, support_party_slot=2)
    duo.observe()
    assert controller.events == [("window", 2), (2, "F2"), ("window", 1)]
    duo.party_profile.box.assert_any_call("hp", 1)
    duo.party_profile.box.assert_any_call("mp", 1)
    assert controller.role == FARMER
    assert duo.self_heals == 1
    assert duo.heals == 0


def test_self_heal_precedes_farmer_macro_and_shares_cooldown(monkeypatch):
    duo, controller, reader = observed_duo(monkeypatch, .4)
    controller.capture = lambda: (.4, .9, False)
    duo.observe()
    assert (2, "F2") in controller.events
    assert (2, "F4") not in controller.events
    reader.return_value = .9
    controller.wait(duo.config.heal_cooldown - .1)
    duo.observe()
    assert (2, "F4") not in controller.events
    controller.wait(.2)
    duo.observe()
    assert (2, "F4") in controller.events
    assert duo.self_heals == 0


@pytest.mark.parametrize("hp", [None, .6, 1.])
def test_unknown_dead_or_healthy_support_is_not_healed(monkeypatch, hp):
    duo, controller, _ = observed_duo(monkeypatch, hp)
    duo.observe()
    assert controller.events == []


@pytest.mark.parametrize("key", [None, "", "   "])
def test_empty_self_key_disables_self_heal(monkeypatch, key):
    duo, controller, _ = observed_duo(monkeypatch, .2, support_self_heal_key=key)
    duo.self_heals = duo.config.heal_attempts
    duo.observe()
    assert controller.events == []


def test_self_macro_works_with_farmer_macro_disabled(monkeypatch):
    duo, controller, _ = observed_duo(monkeypatch, .4, heal_key=None)
    controller.capture = lambda: (.4, .9, False)
    duo.observe()
    assert controller.events == [("window", 2), (2, "F2"), ("window", 1)]


@pytest.mark.parametrize("stale,paused", [(True, False), (False, True)])
def test_stale_or_previous_pause_party_hp_does_not_heal(monkeypatch, stale, paused):
    duo, controller, _ = observed_duo(monkeypatch, .2)
    duo.track_damage(None, 1.)
    controller.wait(2 if stale else 0)
    controller.pause_generation += int(paused)
    duo.on_observation = Mock()
    duo.observe()
    assert controller.events == []


def test_self_heal_has_separate_attempt_limit(monkeypatch):
    duo, controller, _ = observed_duo(monkeypatch, .3, heal_attempts=1)
    duo.heals = duo.config.heal_attempts
    duo.observe()
    assert duo.self_heals == 1
    controller.wait(duo.config.heal_cooldown)
    with pytest.raises(RuntimeError, match="HP саппорта"):
        duo.observe()
    assert controller.events.count((2, "F2")) == 1


def test_healing_does_not_interrupt_active_support_buff_above_emergency_threshold(monkeypatch):
    duo, controller, _ = observed_duo(monkeypatch, .7, support_self_heal_threshold=.8)
    duo.buffing = True
    duo.observe()
    assert (2, "F2") not in controller.events


def test_self_heal_config_roundtrips_and_validates():
    assert DuoConfig().support_self_heal_key is None
    config = DuoConfig(support_self_heal_key=" F2 ", support_self_heal_threshold=.7)
    assert parse_duo_settings(format_duo_settings(config)) == config
    assert DuoConfig.model_validate_json(config.model_dump_json()) == config
    with pytest.raises(ValidationError):
        DuoConfig(support_self_heal_key="ALT+TAB")
    with pytest.raises(ValidationError):
        DuoConfig(support_self_heal_threshold=1.1)
