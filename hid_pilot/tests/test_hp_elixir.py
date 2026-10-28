import pytest
from pydantic import ValidationError

from hid_pilot.duo import DuoConfig, SupportDied, format_duo_settings, parse_duo_settings
from hid_pilot.farm_melee import MeleeConfig
from test_duo import FARMER, make_duo
from test_duo_self_heal import observed_duo


def test_both_roles_default_to_numpad_2_and_five_minutes():
    config = DuoConfig()
    assert config.hp_elixir_key == config.support_hp_elixir_key == "NUM2"
    assert config.hp_elixir_cooldown == config.support_hp_elixir_cooldown == 300
    assert config.hp_elixir_hp_threshold == config.support_hp_elixir_hp_threshold == .5
    assert parse_duo_settings(format_duo_settings(config)) == config
    assert MeleeConfig(rest="=").hp_elixir_key == "NUM2"


def test_n2_alias_and_empty_keys_roundtrip():
    config = DuoConfig(hp_elixir_key="n2", support_hp_elixir_key=" N2 ")
    assert config.hp_elixir_key == config.support_hp_elixir_key == "NUM2"
    config = DuoConfig(hp_elixir_key="", support_hp_elixir_key=" ")
    assert config.hp_elixir_key is config.support_hp_elixir_key is None


@pytest.mark.parametrize("settings", [
    {"hp_elixir_key": "ALT+TAB"}, {"support_hp_elixir_key": "ALT+TAB"},
    {"hp_elixir_cooldown": 299}, {"support_hp_elixir_cooldown": 299},
    {"hp_elixir_hp_threshold": 0}, {"support_hp_elixir_hp_threshold": 1.1},
])
def test_invalid_keys_thresholds_and_short_cooldowns_are_rejected(settings):
    with pytest.raises(ValidationError):
        DuoConfig(**settings)


@pytest.mark.parametrize("hp,expected", [(None, False), (0, False), (.49, True), (.5, False), (1, False)])
def test_farmer_elixir_requires_known_living_low_hp(hp, expected):
    farmer, controller = make_duo(hp_elixir_key="NUM2")
    assert farmer.use_hp_elixir(hp) is expected
    assert controller.events == ([(FARMER.hwnd, "NUM2")] if expected else [])


def test_farmer_elixir_does_not_repeat_before_300_real_seconds():
    farmer, controller = make_duo(hp_elixir_key="NUM2")
    wall = [0.]
    farmer.hp_elixir_clock = lambda: wall[0]
    assert farmer.use_hp_elixir(.3)
    controller.wait(500)
    assert not farmer.use_hp_elixir(.3)
    wall[0] = 299.9
    assert not farmer.use_hp_elixir(.3)
    # Реальное время идёт и при паузе активного игрового таймера.
    wall[0] = 300
    assert farmer.use_hp_elixir(.3)
    assert controller.events == [(FARMER.hwnd, "NUM2"), (FARMER.hwnd, "NUM2")]


def test_farmer_elixir_refreshes_hp_instead_of_assuming_fifty_percent_heal():
    farmer, controller = make_duo(hp_elixir_key="NUM2", heal_key=None)
    farmer.hp_elixir_clock = lambda: 0
    hp = [.3]
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name == "NUM2":
            hp[0] = .77

    controller.key = key
    controller.capture = lambda: (hp[0], None, False)
    observed_hp, _, _ = farmer.observe()
    assert observed_hp == .77
    assert controller.events == [(FARMER.hwnd, "NUM2")]


def test_instant_elixir_precedes_ud_and_ud_uses_refreshed_hp():
    farmer, controller = make_duo(hp_elixir_key="NUM2", ud_key="F8", heal_key=None)
    hp = [.04]
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name == "NUM2":
            hp[0] = .8

    controller.key = key
    controller.capture = lambda: (hp[0], None, False)
    assert farmer.observe()[0] == .8
    assert controller.events == [(FARMER.hwnd, "NUM2")]


def test_support_elixir_has_priority_over_regular_potion_and_works_during_buff(monkeypatch):
    farmer, controller, _ = observed_duo(
        monkeypatch, .3, support_hp_elixir_key="NUM2", support_self_heal_key=None,
        support_potion_key="F5", support_potion_hp_threshold=.4)
    farmer.hp_elixir_clock = lambda: 0
    farmer.buffing = True
    farmer.observe()
    assert (2, "NUM2") in controller.events
    assert controller.events.index((2, "NUM2")) < controller.events.index((2, "F5"))
    assert controller.role == FARMER


def test_elixir_cooldowns_are_separate_for_each_character(monkeypatch):
    farmer, controller, _ = observed_duo(
        monkeypatch, .3, hp_elixir_key="NUM2", support_hp_elixir_key="NUM2",
        heal_key=None, support_self_heal_key=None)
    wall = [0.]
    farmer.hp_elixir_clock = lambda: wall[0]
    controller.capture = lambda: (.3, None, False)
    farmer.observe()
    assert controller.events.count((1, "NUM2")) == 1
    assert controller.events.count((2, "NUM2")) == 1
    controller.wait(1000)
    farmer.observe()
    assert controller.events.count((1, "NUM2")) == 1
    assert controller.events.count((2, "NUM2")) == 1
    wall[0] = 300
    farmer.observe()
    assert controller.events.count((1, "NUM2")) == 2
    assert controller.events.count((2, "NUM2")) == 2


@pytest.mark.parametrize("hp", [None, 0, .5, 1])
def test_support_does_not_use_elixir_for_unknown_dead_or_healthy_hp(monkeypatch, hp):
    farmer, controller, _ = observed_duo(
        monkeypatch, hp, support_hp_elixir_key="NUM2", support_self_heal_key=None)
    if hp == 0:
        with pytest.raises(SupportDied):
            farmer.observe()
    else:
        farmer.observe()
    assert (2, "NUM2") not in controller.events


def test_stale_support_hp_does_not_authorize_elixir():
    farmer, controller = make_duo(support_hp_elixir_key="NUM2")
    farmer.previous_resources = {"support_hp": .2}
    farmer.resources_at = -2
    assert not farmer.support_maintenance(1., None)
    assert controller.events == []
