import pytest

from hid_pilot.controller import Stopped
from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from test_duo import FARMER, make_duo
from test_duo_self_heal import observed_duo
from test_melee_farm import harness


KEYS = [f"NUM{slot}" for slot in (3, 4, 5, 6, 7, 8, 9, 0)] + ["NUMDIVIDE", "NUMMULTIPLY"]


def mass_team():
    duo, controller = make_duo(support_mass_buff_enabled=True, heal_key=None)
    duo.attacking = True
    return duo, controller


def test_each_buff_is_double_pressed_in_one_visit_and_timer_starts_after_last():
    duo, controller = mass_team()
    timeline = []
    press = controller.key

    def key(name, duration, **kwargs):
        timeline.append((name, controller.now, duration, kwargs))
        press(name, duration, **kwargs)

    controller.key = key
    for index, name in enumerate(KEYS):
        before = len(controller.events)
        duo.cast_buffs()
        assert controller.events[before:] == [("window", 2), (2, name), (2, name), ("window", 1)]
        assert controller.role == FARMER
        assert not duo.buffing
        first, second = timeline[-2:]
        assert second[1] - first[1] == pytest.approx(.085)
        assert first[2:] == (80, {"exact": True})
        if index < len(KEYS) - 1:
            assert duo.next_buff == 0
        controller.wait(duo.config.support_mass_buff_cast_seconds)
    assert duo.next_buff == pytest.approx(controller.now - 1 + duo.config.rebuff_minutes * 60)
    assert not duo.mass_buff_active
    assert duo.mass_buff_index == 0


def test_cast_gap_blocks_next_buff_and_ordinary_assist():
    duo, controller = mass_team()
    duo.cast_buffs()
    events = list(controller.events)
    duo.support_attack_started = duo.support_attack_confirmed = True
    duo.assist_support()
    duo.cast_buffs()
    assert controller.events == events
    controller.wait(1)
    duo.cast_buffs()
    assert (2, "NUM4") in controller.events


def test_partial_series_continues_when_combat_ends():
    duo, controller = mass_team()
    duo.cast_buffs()
    duo.attacking = False
    controller.wait(1)
    duo.cast_buffs()
    assert (2, "NUM4") in controller.events
    assert (2, duo.config.buff_key) not in controller.events


def test_pause_restarts_partial_series_without_marking_timer():
    duo, controller = mass_team()
    duo.cast_buffs()
    controller.pause_generation += 1
    controller.wait(1)
    duo.cast_buffs()
    assert controller.events.count((2, "NUM3")) == 4
    assert duo.next_buff == 0


def test_stop_between_presses_does_not_advance_series_or_timer():
    duo, controller = mass_team()

    def stop(seconds):
        raise Stopped("STOP")

    controller.wait = stop
    with pytest.raises(Stopped):
        duo.cast_buffs()
    assert controller.events.count((2, "NUM3")) == 1
    assert duo.mass_buff_index == 0
    assert duo.next_buff == 0


@pytest.mark.parametrize("mp", [None, .59])
def test_start_requires_known_sufficient_support_mana(monkeypatch, mp):
    duo, controller, reader = observed_duo(
        monkeypatch, 1., support_mass_buff_enabled=True, heal_key=None,
        support_self_heal_key=None)
    reader.side_effect = lambda image, kind: mp if kind == "mp" else 1.
    duo.attacking = True
    duo.cast_buffs()
    assert (2, "NUM3") not in controller.events
    assert duo.next_buff == 0


def test_critical_hp_cancels_series_and_allows_retry_after_recovery(monkeypatch):
    duo, controller, reader = observed_duo(
        monkeypatch, 1., support_mass_buff_enabled=True, heal_key=None,
        support_self_heal_key=None)
    duo.attacking = True
    duo.cast_buffs()
    reader.side_effect = lambda image, kind: .3 if kind == "hp" else 1.
    duo.observe()
    assert (2, "ESC") in controller.events
    assert duo.mass_buff_index == 0
    assert not duo.mass_buff_active
    reader.side_effect = lambda image, kind: 1.
    duo.cast_buffs()
    assert controller.events.count((2, "NUM3")) == 4
    assert duo.next_buff == 0


def test_disabled_buttons_are_skipped_and_config_roundtrips():
    config = DuoConfig(support_mass_buff3_key="", support_mass_buff_divide_key=None)
    assert parse_duo_settings(format_duo_settings(config)) == config
    with pytest.raises(ValueError):
        DuoConfig(support_mass_buff3_key="ALT+F4")
    duo, controller = mass_team()
    duo.config = duo.config.model_copy(update={"support_mass_buff3_key": None})
    duo.cast_buffs()
    assert (2, "NUM4") in controller.events
    assert (2, "NUM3") not in controller.events


def test_ma_resumes_attack_between_two_buffs_in_continuous_combat():
    farmer, controller, now, _ = harness(
        buff1_key="NUM9", buff2_key="NUM0", regen_enabled=False)
    farmer.observe = lambda: (1., 1., 0. if now[0] > 4 else .8)
    farmer.verify_target = lambda hp, mp, target, identity: (hp, mp, target, True)
    farmer.fight(.9)
    keys = [call.args[0] for call in controller.key.call_args_list]
    first, second = keys.index("NUM9"), keys.index("NUM0")
    assert farmer.config.attack in keys[first + 1:second]


def test_complete_support_series_inside_continuous_fight():
    duo, controller = make_duo(support_mass_buff_enabled=True, heal_key=None)
    duo.next_buff = .5
    finished = [False]
    press = controller.key

    def key(name, duration, **kwargs):
        press(name, duration, **kwargs)
        if controller.role.hwnd == 2 and name == "NUMMULTIPLY":
            finished[0] = True

    controller.key = key
    controller.capture = lambda: (1., 0. if finished[0] else .9, False)
    duo.fight(.9)
    for name in KEYS:
        assert controller.events.count((2, name)) == 2
    assert (2, duo.config.buff_key) not in controller.events
    for first, second in zip(KEYS, KEYS[1:]):
        start = controller.events.index((2, first)) + 2
        end = controller.events.index((2, second))
        assert (1, duo.config.attack) in controller.events[start:end]
    assert duo.next_buff > controller.now
    assert controller.role == FARMER
