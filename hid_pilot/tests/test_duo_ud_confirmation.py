from unittest.mock import Mock

import pytest

from hid_pilot.controller import Stopped
from test_duo import make_duo, start_ma_attack


@pytest.mark.parametrize("action", ["assist", "heal", "party_heal", "vampiric"])
def test_urgent_second_frame_and_ud_precede_support_visits_and_long_cast(action):
    settings = {"ud_key": "F6", "ud_hp_threshold": .19, "heal_key": None}
    if action == "heal":
        settings["heal_key"] = "F9"
    elif action == "party_heal":
        settings["support_party_heal_key"] = "F7"
    elif action == "vampiric":
        settings["vampiric_key"] = "F2"
    duo, controller = make_duo(**settings)
    if action == "assist":
        start_ma_attack(duo, controller)
    timeline = []
    samples = iter([.174, .061])
    original_key = controller.key

    def capture():
        hp = next(samples, .061)
        timeline.append(("capture", controller.role.hwnd, hp, controller.now))
        return hp, .945, False

    def key(name, duration, **kwargs):
        timeline.append(("key", controller.role.hwnd, name, controller.now))
        original_key(name, duration, **kwargs)

    controller.capture = capture
    controller.key = key
    if action == "assist":
        duo.attacking = duo.support_pending = True
    hp, _, target = duo.observe()
    if action == "vampiric":
        assert duo.use_vampiric(hp, target)
    assert timeline[0][:3] == ("capture", 1, .174)
    assert timeline[1][:3] == ("capture", 1, .061)
    assert timeline[1][3] - timeline[0][3] == pytest.approx(.15)
    assert timeline[2][:3] == ("key", 1, "F6")
    assert controller.events.count((1, "F6")) == 1
    expected = {"assist": (2, duo.config.support_key), "heal": (2, "F9"),
                "party_heal": (2, "F7"), "vampiric": (1, "F2")}[action]
    if action == "assist":
        # При критическом HP обычный ассист остаётся в очереди после УД.
        assert expected not in controller.events
        assert duo.support_pending
    else:
        assert controller.events.index((1, "F6")) < controller.events.index(expected)


def test_second_frame_normalization_cancels_ud_and_returns_newest_state():
    duo, controller = make_duo(ud_key="F6", heal_key=None, ud_hp_threshold=.19)
    controller.frames = iter([(.174, .9, False), (.8, .4, False)])
    hp, _, target = duo._observe()
    assert hp == .8
    assert target == .4
    assert controller.events == []
    assert duo.ud_confirmations == 0


def test_high_damage_first_confirmation_also_requests_second_frame():
    duo, controller = make_duo(ud_key="F6", heal_key=None, ud_damage_per_second=.1)
    duo.on_observation(None, 1., 1., .9)
    controller.wait(.2)
    controller.frames = iter([(.9, .9, False), (.88, .9, False)])
    duo._observe()
    assert controller.events == [(1, "F6")]


@pytest.mark.parametrize("mode", ["unknown_hp", "pause", "gap", "same_time"])
def test_second_frame_does_not_reuse_invalid_confirmation(mode):
    duo, controller = make_duo(ud_key="F6", heal_key=None, ud_hp_threshold=.19)
    controller.frames = iter([(.174, .9, False),
                              (None if mode == "unknown_hp" else .061, .9, False)])
    original_wait = controller.wait

    def wait(seconds):
        if mode == "pause":
            controller.pause_generation += 1
        if mode != "same_time":
            original_wait(2 if mode == "gap" else seconds)

    controller.wait = wait
    duo._observe()
    assert controller.events == []
    assert duo.ud_confirmations == (0 if mode == "unknown_hp" else 1)
    assert not duo._ud_observing


def test_stop_before_confirmation_prevents_second_capture_and_all_actions():
    duo, controller = make_duo(ud_key="F6", heal_key=None, ud_hp_threshold=.19)
    controller.capture = Mock(return_value=(.174, .9, False))
    controller.wait = Mock(side_effect=Stopped("Сценарий остановлен"))
    with pytest.raises(Stopped):
        duo.observe()
    assert controller.capture.call_count == 1
    assert controller.events == []
    assert not duo._ud_observing


@pytest.mark.parametrize("mode", ["disabled", "cooldown", "mana", "recovery"])
def test_blocked_or_recovering_ud_does_not_request_extra_capture(mode):
    duo, controller = make_duo(ud_key=None if mode == "disabled" else "F6",
                               heal_key=None, buff1_key="F5" if mode == "mana" else None)
    if mode == "cooldown":
        duo.ud_ready_at = 900.
    if mode == "recovery":
        duo.observation_recovering = True
    controller.capture = Mock(return_value=(.06, .9, False))
    if mode == "mana":
        duo.profile.read = lambda image, name: .05 if name == "mp" else image[0 if name == "hp" else 1]
    duo._observe()
    assert controller.capture.call_count == 1
    assert controller.events == []


def test_confirmation_works_for_resource_fallback_with_unknown_mp():
    duo, controller = make_duo(ud_key="F6", heal_key=None, ud_hp_threshold=.19)
    duo.profile.read = lambda image, name: None if name == "mp" else .174
    controller.capture = Mock(return_value=(.174, .9, False))
    hp, mp, target = duo._observe(resources_only=True)
    assert hp == .174 and mp is None and target is None
    assert controller.capture.call_count >= 2
    assert controller.events == [(1, "F6")]


def test_fast_target_capture_confirms_ud_before_opening_attack():
    duo, controller = make_duo(ud_key="F6", heal_key=None, ud_hp_threshold=.19,
                               experimental_fast_farm=True)
    controller.frames = iter([(.174, .9, False), (.061, .9, False)])
    duo.switch_fast_target()
    assert controller.events == [(1, duo.config.next_target), (1, "F6"), (1, duo.config.attack)]


def test_after_one_second_ud_hold_returned_state_uses_post_hold_capture():
    duo, controller = make_duo(ud_key="F6", heal_key=None, ud_hp_threshold=.19)
    controller.frames = iter([(.174, .9, False), (.061, .9, False), (.4, .2, False)])
    original_key = controller.key

    def key(name, duration, **kwargs):
        original_key(name, duration, **kwargs)
        controller.now += duration / 1000

    controller.key = key
    hp, _, target = duo._observe()
    assert hp == .4 and target == .2
    assert controller.now == pytest.approx(1.15)
    assert controller.events == [(1, "F6")]


def test_death_on_second_frame_enters_resurrection_without_ud(monkeypatch):
    duo, controller = make_duo(ud_key="F6", heal_key=None, ud_hp_threshold=.19)
    controller.frames = iter([(.174, .9, False), (0., .9, False)])
    resurrection = Mock(side_effect=Stopped("Ожидание resurrection"))
    monkeypatch.setattr("hid_pilot.farm.wait_for_resurrection", resurrection)
    with pytest.raises(Stopped):
        duo.observe()
    resurrection.assert_called_once()
    assert controller.events == []
    assert not duo._ud_observing


def test_nested_resource_fallback_keeps_confirmation_bounded():
    duo, controller = make_duo(ud_key="F6", heal_key=None, ud_hp_threshold=.19)
    controller.capture = Mock(return_value=(.174, .9, False))
    slow = [True]

    def read(image, name):
        if name == "target_hp" and slow[0]:
            slow[0] = False
            controller.wait(1.1)
        return 1. if name == "mp" else image[0 if name == "hp" else 1]

    duo.profile.read = read
    hp, mp, target = duo._observe()
    assert (hp, mp, target) == (.174, 1., .9)
    assert controller.capture.call_count == 4
    assert controller.events == [(1, "F6")]
    assert not duo._ud_observing
