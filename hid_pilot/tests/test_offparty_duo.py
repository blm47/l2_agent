from pathlib import Path
from unittest.mock import Mock

import pytest

from hid_pilot.controller import Stopped
from hid_pilot.duo import parse_duo_settings
from hid_pilot.farm_settings import setting_visible
from test_duo import FARMER, SUPPORT, make_duo


def offparty(**values):
    values.setdefault("buff_key", "F11")
    values.setdefault("support_buff2_key", "F12")
    values.setdefault("heal_seconds", 2)
    values.setdefault("heal_key", "F9")
    return make_duo(offparty_support=True, **values)


@pytest.mark.parametrize("key", ["F11", "F9"])
def test_each_action_invites_before_one_macro_and_returns(key):
    duo, controller = offparty()
    assert duo.offparty_action(key, 2) == 1.5
    expected = [("window", FARMER.hwnd), (FARMER.hwnd, "F7"),
                ("window", SUPPORT.hwnd), (SUPPORT.hwnd, key)]
    if key == "F11":
        expected.append((SUPPORT.hwnd, "F12"))
    expected.extend([("window", FARMER.hwnd), (FARMER.hwnd, "F8")])
    assert controller.events == expected
    assert controller.now == (13.5 if key == "F11" else 3.5)
    assert not duo.offparty_busy


def test_no_assist_follow_consumables_or_unrelated_visits():
    duo, controller = offparty()
    duo.support_pending = True
    duo.assist_support()
    duo.ensure_support_follow()
    assert not duo.support_consumables()
    assert duo.visit("F2", 1) is None
    assert duo.offparty_action("F2", 1) is None
    assert controller.events == []


@pytest.mark.parametrize("pause", [True, False])
def test_pause_or_stop_after_invite_does_not_send_macro(pause):
    duo, controller = offparty()

    def wait(seconds):
        if pause:
            controller.pause_generation += 1
        else:
            raise Stopped()

    controller.wait = wait
    with pytest.raises(RuntimeError if pause else Stopped):
        duo.offparty_action("F11", 1)
    assert controller.events == [("window", FARMER.hwnd), (FARMER.hwnd, "F7")]
    assert not duo.offparty_busy


def test_low_ma_hp_triggers_invited_heal_and_observes_again():
    duo, controller = offparty(heal_threshold=.6)
    duo.observe_ma = Mock(side_effect=[(.4, 1., .8), (.9, 1., .7)])
    assert duo.observe() == (.9, 1., .7)
    assert (FARMER.hwnd, "F7") in controller.events
    assert (SUPPORT.hwnd, "F9") in controller.events
    assert duo.heals == 1


def test_heal_cooldown_continues_beyond_attempt_limit():
    duo, controller = offparty(heal_attempts=1, heal_cooldown=10)
    duo.observe_ma = Mock(return_value=(.4, 1., .8))
    duo.observe()
    controller.events.clear()
    duo.observe()
    assert controller.events == []
    for _ in range(3):
        controller.now += 10
        assert duo.observe() == (.4, 1., .8)
    assert duo.heals == 4
    assert controller.events.count((SUPPORT.hwnd, "F9")) == 3
    assert controller.events.count((FARMER.hwnd, "F8")) == 3
    assert not setting_visible("heal_attempts", duo.config)


def test_rebuff_timer_and_no_other_support_macros():
    duo, controller = offparty(rebuff_minutes=10)
    duo.observe_ma = Mock(return_value=(1., 1., .8))
    duo.cast_buffs()
    assert [event for event in controller.events if event[0] == SUPPORT.hwnd] == [(SUPPORT.hwnd, "F11"), (SUPPORT.hwnd, "F12")]
    assert duo.next_buff == 601.5
    controller.events.clear()
    duo.cast_buffs()
    assert controller.events == []


def test_offparty_does_not_use_party_roi_or_autosummon():
    duo, controller = offparty(summon_key="NUM6")
    duo.service_summon()
    assert not duo.saving_mana_for_summon()
    assert duo.party_profile is None
    assert controller.events == []


def test_shipped_script_and_settings():
    path = Path(__file__).parents[1] / "extensions/scripts/farmer-offparty-support.duo.pilot"
    config = parse_duo_settings(path.read_text("utf-8"))
    assert config.offparty_support
    assert config.offparty_invite_key == "F7"
    assert config.offparty_kick_key == "F8"
    assert (config.buff_key, config.support_buff2_key) == ("F11", "F12")
    assert config.heal_key == "F9"
    assert config.heal_seconds == 2
    assert setting_visible("support_buff2_key", config)
    assert setting_visible("support_buff2_seconds", config)
    assert not setting_visible("support_key", config)
    assert not setting_visible("summon_key", config)
    assert setting_visible("offparty_invite_seconds", config)


@pytest.mark.parametrize("heal", [False, True])
def test_macro_timers_before_ma_kicks_support(heal):
    duo, controller = offparty()
    duo.config = duo.config.model_copy(update={"buff_seconds": 15, "support_buff2_seconds": 10})
    sent = []
    key = controller.key

    def record(name, duration, **kwargs):
        sent.append((controller.role.hwnd, name, controller.now))
        key(name, duration, **kwargs)

    controller.key = record
    duo.offparty_action("F9" if heal else "F11", 2 if heal else 15)
    if heal:
        assert sent == [(1, "F7", 0), (2, "F9", 1.5), (1, "F8", 3.5)]
    else:
        assert sent == [(1, "F7", 0), (2, "F11", 1.5),
                        (2, "F12", 16.5), (1, "F8", 26.5)]


@pytest.mark.parametrize("stage", [3, 4])
def test_stop_during_buff_wait_never_kicks_or_sends_later_macro(stage):
    duo, controller = offparty()
    wait = controller.wait
    calls = 0

    def stop(seconds):
        nonlocal calls
        calls += 1
        if calls == stage:
            raise Stopped()
        wait(seconds)

    controller.wait = stop
    with pytest.raises(Stopped):
        duo.offparty_action("F11", 1)
    assert (FARMER.hwnd, "F8") not in controller.events
    if stage == 3:
        assert (SUPPORT.hwnd, "F12") not in controller.events


@pytest.mark.parametrize("value,fresh,generation,enabled,expected", [
    (.59, True, True, True, True),
    (.6, True, True, True, False),
    (.9, True, True, True, False),
    (0., True, True, True, False),
    (None, True, True, True, False),
    (.2, False, True, True, False),
    (.2, True, False, True, False),
    (.2, True, True, False, False),
])
def test_party_member_hp_requests_heal_even_when_ma_healthy(value, fresh, generation, enabled, expected):
    duo, controller = offparty(heal_key="F9" if enabled else None, heal_threshold=.6)
    duo.observe_ma = Mock(return_value=(1., 1., .8))
    duo.previous_resources = {"party_1_hp": .9, "party_8_hp": value}
    duo.resources_at = controller.now if fresh else controller.now - 2
    duo.resource_generation = controller.pause_generation if generation else -1
    duo.observe()
    assert ((SUPPORT.hwnd, "F9") in controller.events) is expected


def test_party_hp_is_read_from_current_image_without_support_slot(monkeypatch):
    from hid_pilot.party import PartyMember

    duo, controller = offparty()
    duo.party_profile = Mock()
    duo.display_data = None
    image = Mock()
    reader = Mock(return_value=[PartyMember(slot=2, hp=.4), PartyMember(slot=8, hp=.9)])
    monkeypatch.setattr("hid_pilot.duo.read_party_bars", reader)
    duo.track_damage(image, 1.)
    reader.assert_called_once_with(image, duo.party_profile)
    assert duo.previous_resources["party_2_hp"] == .4
    assert "support_hp" not in duo.previous_resources


def test_monitoring_party_requires_profile_before_input():
    duo, controller = offparty()
    with pytest.raises(RuntimeError, match="панель пати"):
        duo.run()
    assert controller.events == []


def test_low_party_hp_defers_buff_while_heal_is_on_cooldown():
    duo, controller = offparty()
    duo.observe_ma = Mock(return_value=(1., 1., .8))
    duo.previous_resources = {"party_8_hp": .2}
    duo.resources_at = controller.now
    duo.last_heal = controller.now
    duo.buff()
    assert controller.events == []


@pytest.mark.parametrize("threshold,expected", [(.3, False), (.8, True)])
@pytest.mark.parametrize("member", [False, True])
def test_same_threshold_controls_ma_and_party(threshold, expected, member):
    duo, controller = offparty(heal_threshold=threshold)
    duo.observe_ma = Mock(return_value=(1. if member else .5, 1., .8))
    duo.previous_resources = {"party_2_hp": .5 if member else 1.}
    duo.resources_at = controller.now
    duo.observe()
    assert ((SUPPORT.hwnd, "F9") in controller.events) is expected


def test_legacy_party_settings_do_not_override_shared_threshold():
    config = parse_duo_settings('set offparty_party_heal false\n'
                                'set offparty_party_hp_threshold 0.9\n'
                                'set heal_threshold 0.4\n')
    assert config.heal_threshold == .4
    assert "offparty_party_heal" not in type(config).model_fields
    assert "offparty_party_hp_threshold" not in type(config).model_fields
