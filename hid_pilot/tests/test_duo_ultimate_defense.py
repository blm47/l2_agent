import pytest
from pydantic import ValidationError

from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from test_duo import make_duo
from test_duo_self_heal import observed_duo


def test_ud_triggers_on_high_damage_before_attack_or_support_heal():
    duo, controller = make_duo(ud_key="F6", heal_key="F4")
    controller.frames = iter([(.6, .9, False), (.54, .9, False)])
    duo.observe()
    controller.wait(1)
    duo.observe()
    assert controller.events[0] == (1, "F6")
    assert controller.events.index((1, "F6")) < controller.events.index((1, duo.config.attack))
    assert controller.events.index((1, "F6")) < controller.events.index((2, "F4"))


@pytest.mark.parametrize("hp,expected", [(.069, True), (.07, False), (.5, False)])
def test_ud_absolute_hp_threshold_does_not_require_damage_history(hp, expected):
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    controller.capture = lambda: (hp, .9, False)
    duo.observe()
    assert ((1, "F6") in controller.events) is expected


@pytest.mark.parametrize("loss,expected", [(.049, False), (.05, False), (.051, True)])
def test_ud_damage_threshold_is_strictly_above_five_percent_per_second(loss, expected):
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    controller.frames = iter([(1., .9, False), (1 - loss, .9, False)])
    duo.observe()
    controller.wait(1)
    duo.observe()
    assert ((1, "F6") in controller.events) is expected


def test_ud_damage_rate_accounts_for_actual_observation_interval():
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    controller.frames = iter([(1., .9, False), (.98, .9, False)])
    duo.observe()
    controller.wait(.2)
    duo.observe()
    assert duo.ud_damage_rate == pytest.approx(.1)
    assert (1, "F6") in controller.events


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_ud_key_disables_low_hp_and_high_damage_triggers(key):
    duo, controller = make_duo(ud_key=key, heal_key=None)
    controller.frames = iter([(1., .9, False), (.01, .9, False)])
    duo.observe()
    controller.wait(.2)
    duo.observe()
    assert (1, "F6") not in controller.events
    assert duo.ud_ready_at == float("-inf")


def test_ud_cooldown_is_900_active_seconds_and_survives_pause_generation():
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    controller.capture = lambda: (.06, .9, False)
    duo.observe()
    controller.pause_generation += 1
    controller.now = 899.9
    duo.observe()
    assert controller.events.count((1, "F6")) == 1
    controller.now = 900
    duo.observe()
    assert controller.events.count((1, "F6")) == 2


@pytest.mark.parametrize("gap,paused", [(2., False), (.2, True)])
def test_old_damage_history_after_gap_or_pause_cannot_trigger_ud(gap, paused):
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    controller.frames = iter([(1., .9, False), (.8, .9, False)])
    duo.observe()
    controller.wait(gap)
    controller.pause_generation += int(paused)
    duo.observe()
    assert duo.ud_damage_rate is None
    assert (1, "F6") not in controller.events


def test_unknown_observation_resets_ud_damage_baseline():
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    duo.on_observation(None, 1., 1., .9)
    duo.on_observation(None, None, 1., .9)
    controller.wait(.2)
    duo.on_observation(None, .8, 1., .9)
    assert duo.ud_damage_rate is None
    assert controller.events == []


def test_support_damage_alone_does_not_trigger_ma_ud(monkeypatch):
    duo, controller, reader = observed_duo(monkeypatch, 1., ud_key="F6",
                                          support_self_heal_key=None, heal_key=None)
    duo.observe()
    controller.wait(.2)
    reader.return_value = .4
    duo.observe()
    assert (1, "F6") not in controller.events


def test_ud_does_not_press_skill_for_dead_character():
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    duo.on_observation(None, 0., 1., .9)
    assert controller.events == []


@pytest.mark.parametrize("low_hp", [True, False])
def test_ud_can_protect_known_hp_even_when_mp_is_unknown(low_hp):
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    if not low_hp:
        duo.on_observation(None, 1., None, .9)
        controller.wait(.2)
    duo.on_observation(None, .06 if low_hp else .98, None, .9)
    assert controller.events == [(1, "F6")]


def test_ud_settings_roundtrip_and_invalid_keys_are_rejected():
    assert DuoConfig().ud_key is None
    config = DuoConfig(ud_key="F6", ud_cooldown=900)
    assert parse_duo_settings(format_duo_settings(config)) == config
    for values in ({"ud_key": "ALT+F4"}, {"ud_cooldown": 0}, {"ud_hp_threshold": -1}):
        with pytest.raises(ValidationError):
            DuoConfig(**values)
