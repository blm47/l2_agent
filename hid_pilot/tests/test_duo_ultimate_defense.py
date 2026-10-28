import pytest
from pydantic import ValidationError

from hid_pilot.controller import Stopped
from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from test_duo import make_duo
from test_duo_self_heal import observed_duo


@pytest.mark.parametrize("key", ["F6", "F7"])
def test_ud_holds_configured_key_for_one_exact_second_in_farmer_window(key):
    duo, controller = make_duo(ud_key=key, heal_key=None)
    duo.config = duo.config.model_copy(update={"humanize": True})
    presses = []
    original_key = controller.key

    def press(name, duration, **kwargs):
        presses.append((controller.role, name, duration, kwargs))
        original_key(name, duration, **kwargs)

    controller.key = press
    duo.on_observation(None, .06, 1., .9)
    controller.wait(.2)
    duo.on_observation(None, .06, 1., .9)
    assert presses == [(duo.farmer, key, 1000, {"exact": True})]


def test_stop_during_ud_hold_does_not_start_cooldown():
    duo, controller = make_duo(ud_key="F6", heal_key=None)

    def stop(name, duration, **kwargs):
        assert (name, duration, kwargs) == ("F6", 1000, {"exact": True})
        raise Stopped("Сценарий остановлен")

    controller.key = stop
    duo.on_observation(None, .06, 1., .9)
    controller.wait(.2)
    with pytest.raises(Stopped):
        duo.on_observation(None, .06, 1., .9)
    assert duo.ud_ready_at == float("-inf")


def test_ud_triggers_on_high_damage_before_attack_or_support_heal():
    duo, controller = make_duo(ud_key="F6", heal_key="F4")
    duo.on_observation(None, .6, 1., .9)
    controller.wait(.2)
    duo.on_observation(None, .58, 1., .9)
    assert controller.events == []
    controller.wait(.2)
    controller.capture = lambda: (.54, .9, False)
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
    controller.wait(.2)
    duo.observe()
    assert ((1, "F6") in controller.events) is expected


@pytest.mark.parametrize("loss,expected", [(.049, False), (.05, False), (.051, True)])
def test_ud_damage_threshold_is_strictly_above_five_percent_per_second(loss, expected):
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    duo.on_observation(None, 1., 1., .9)
    controller.wait(1)
    duo.on_observation(None, 1 - loss, 1., .9)
    assert (1, "F6") not in controller.events
    controller.wait(.2)
    duo.on_observation(None, 1 - loss * 1.2, 1., .9)
    assert ((1, "F6") in controller.events) is expected


def test_ud_damage_rate_accounts_for_actual_observation_interval():
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    duo.on_observation(None, 1., 1., .9)
    controller.wait(.2)
    duo.on_observation(None, .98, 1., .9)
    assert duo.ud_damage_rate == pytest.approx(.1)
    assert (1, "F6") not in controller.events
    controller.wait(.2)
    duo.on_observation(None, .96, 1., .9)
    assert (1, "F6") in controller.events


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_ud_key_disables_low_hp_and_high_damage_triggers(key):
    duo, controller = make_duo(ud_key=key, heal_key=None)
    controller.frames = iter([(1., .9, False), (.01, .9, False)])
    duo.observe()
    controller.wait(.2)
    duo.observe()
    controller.capture = lambda: (.01, .9, False)
    controller.wait(.2)
    duo.observe()
    assert (1, "F6") not in controller.events
    assert duo.ud_ready_at == float("-inf")


def test_ud_cooldown_is_900_active_seconds_and_survives_pause_generation():
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    controller.capture = lambda: (.06, .9, False)
    duo.observe()
    controller.wait(.2)
    duo.observe()
    ready_at = duo.ud_ready_at
    controller.pause_generation += 1
    controller.now = ready_at - .1
    duo.observe()
    assert controller.events.count((1, "F6")) == 1
    controller.now = ready_at
    duo.observe()
    assert controller.events.count((1, "F6")) == 2
    controller.wait(.1)
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
    assert controller.events == []
    controller.wait(.2)
    duo.on_observation(None, .06 if low_hp else .96, None, .9)
    assert controller.events == [(1, "F6")]


@pytest.mark.parametrize("reset", ["normal", "unknown", "dead", "pause", "gap", "clock"])
def test_ud_requires_new_pair_after_confirmation_is_broken(reset):
    duo, controller = make_duo(ud_key="F6", heal_key=None, ud_damage_per_second=1.)
    duo.on_observation(None, .06, 1., .9)
    assert duo.ud_confirmations == 1
    controller.wait(.2)
    if reset in {"normal", "unknown", "dead"}:
        hp = {"normal": .08, "unknown": None, "dead": 0.}[reset]
        duo.on_observation(None, hp, 1., .9)
        controller.wait(.2)
    elif reset == "pause":
        controller.pause_generation += 1
    elif reset == "gap":
        controller.wait(2)
    elif reset == "clock":
        controller.now = 0.
    duo.on_observation(None, .06, 1., .9)
    assert (1, "F6") not in controller.events
    controller.wait(.2)
    duo.on_observation(None, .06, 1., .9)
    assert controller.events == [(1, "F6")]


def test_repeated_ud_check_of_same_observation_does_not_confirm_twice():
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    duo.on_observation(None, .06, 1., .9)
    controller.wait(.1)
    duo.use_ultimate_defense(.06)
    assert controller.events == []
    assert duo.ud_confirmations == 1
    duo.on_observation(None, .06, 1., .9)
    assert controller.events == [(1, "F6")]


def test_single_high_damage_observation_does_not_survive_normal_rate():
    duo, controller = make_duo(ud_key="F6", heal_key=None)
    duo.on_observation(None, 1., 1., .9)
    for hp in (.98, .98, .96):
        controller.wait(.2)
        duo.on_observation(None, hp, 1., .9)
        assert (1, "F6") not in controller.events
    assert duo.ud_confirmations == 1
    controller.wait(.2)
    duo.on_observation(None, .94, 1., .9)
    assert controller.events == [(1, "F6")]


def test_ud_settings_roundtrip_and_invalid_keys_are_rejected():
    assert DuoConfig().ud_key is None
    config = DuoConfig(ud_key="F6", ud_cooldown=900)
    assert parse_duo_settings(format_duo_settings(config)) == config
    for values in ({"ud_key": "ALT+F4"}, {"ud_cooldown": 0}, {"ud_hp_threshold": -1}):
        with pytest.raises(ValidationError):
            DuoConfig(**values)
