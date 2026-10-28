from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.controller import Stopped
from hid_pilot.duo import SupportDied
from hid_pilot.party import PartyProfile
from hid_pilot.support_life import SupportLifeObserver
from test_duo import FARMER, SUPPORT, make_duo
from test_melee_escape import named


def party_duo(**settings):
    duo, controller = make_duo(**settings)
    profile = BarProfile(width=800, height=800)
    duo.profile.crop = profile.crop
    duo.profile.read = lambda image, name: .9 if name == "target_hp" else 1.
    duo.profile.dead_marker_visible = lambda image: False
    duo.party_profile = PartyProfile(
        width=800, height=800, row_step=80,
        name=BarBox(x1=.1, y1=.01, x2=.4, y2=.02),
        cp=BarBox(x1=.1, y1=.03, x2=.4, y2=.04),
        hp=BarBox(x1=.1, y1=.05, x2=.4, y2=.06),
        mp=BarBox(x1=.1, y1=.07, x2=.4, y2=.08))
    frame = np.zeros((800, 800, 3), np.uint8)
    profile.crop(frame, duo.party_profile.mp)[:] = (200, 60, 10)
    death = Mock()
    duo.support_life = SupportLifeObserver(death, Mock(), reader=Mock(), button_reader=Mock())
    controller.capture = lambda: frame.copy()
    return duo, controller, frame, death


def test_empty_party_hp_detects_death_from_farmer_before_any_input():
    duo, controller, _, death = party_duo()
    with pytest.raises(SupportDied):
        duo.observe()
    assert duo.support_lost
    assert duo.previous_resources["support_hp"] == 0.
    death.assert_called_once_with()
    duo.support_life.reader.assert_not_called()
    duo.support_life.button_reader.assert_not_called()
    assert controller.role == FARMER
    assert controller.events == []
    duo.observe()
    death.assert_called_once_with()


def test_empty_hp_with_empty_mp_still_detects_death_when_cp_is_visible():
    duo, controller, frame, death = party_duo()
    duo.profile.crop(frame, duo.party_profile.mp)[:] = 0
    duo.profile.crop(frame, duo.party_profile.cp)[:] = (10, 190, 220)
    with pytest.raises(SupportDied):
        duo.observe()
    death.assert_called_once_with()
    assert controller.events == []


@pytest.mark.parametrize("target", [None, .9])
def test_party_death_finishes_only_current_target_without_visiting_support(target):
    duo, controller, _, death = party_duo()
    current = [target]
    duo.profile.read = lambda image, name: current[0] if name == "target_hp" else 1.
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name == duo.config.attack and current[0] is not None:
            current[0] = max(0., current[0] - .3)

    controller.key = key
    with pytest.raises(SupportDied, match="новые цели запрещены"):
        duo.run()
    death.assert_called_once_with()
    assert current[0] == (None if target is None else 0.)
    assert duo.training_kills == int(target is not None)
    assert (FARMER.hwnd, duo.config.next_target) not in controller.events
    assert ("window", SUPPORT.hwnd) not in controller.events


@pytest.mark.parametrize("missing_own", ["hp", "mp"])
def test_party_death_does_not_depend_on_recognizing_farmer_resources(missing_own):
    duo, controller, _, death = party_duo()
    duo.profile.read = lambda image, name: None if name == missing_own else 1.
    with pytest.raises(SupportDied):
        duo.observe()
    death.assert_called_once_with()
    assert controller.events == []


@pytest.mark.parametrize("kind", ["hidden", "broken_hp", "critical_hp"])
def test_missing_panel_or_remaining_red_hp_does_not_mean_death(kind):
    duo, controller, frame, death = party_duo(heal_key=None, support_self_heal_key=None)
    if kind == "hidden":
        frame[:] = 0
    else:
        crop = duo.profile.crop(frame, duo.party_profile.hp)
        if kind == "broken_hp":
            crop[:, 50:100] = (10, 10, 200)
        else:
            crop[:, :1] = (10, 10, 200)
    duo.observe()
    assert not duo.support_lost
    assert duo.previous_resources["support_hp"] is None
    death.assert_not_called()
    assert controller.events == []


def test_death_during_escape_stops_movement_and_new_target_selection(monkeypatch):
    duo, controller, frame, death = party_duo(regen_enabled=False)
    hp = duo.profile.crop(frame, duo.party_profile.hp)
    hp[:] = (10, 10, 200)
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if controller.role == FARMER and name in {"W", "A", "S", "D"}:
            hp[:] = 0

    controller.key = key
    monkeypatch.setattr("hid_pilot.farm_melee.random.choice", lambda choices: "WA")
    with pytest.raises(SupportDied):
        duo.escape_from_unreachable("Mob")
    death.assert_called_once_with()
    assert (SUPPORT.hwnd, duo.config.follow_key) in controller.events
    assert controller.events[-1] == (FARMER.hwnd, "W")
    assert (FARMER.hwnd, duo.config.next_target) not in controller.events
    assert not duo.escaping


def test_escape_follows_before_movement_and_defers_assist(monkeypatch):
    duo, controller = make_duo(regen_enabled=False, experimental_fast_farm=True)
    duo.attacking = True
    duo.support_pending = True
    duo.support_attack_started = True
    original = duo.observe

    def observe():
        result = original()
        named(duo, "Mob")
        return result

    duo.observe = observe
    monkeypatch.setattr("hid_pilot.farm_melee.random.choice", lambda choices: "WA")
    monkeypatch.setattr("hid_pilot.farm_melee.random.randint", lambda low, high: 20)
    assert duo.escape_from_unreachable("Mob") is None
    assert controller.events[:3] == [
        ("window", SUPPORT.hwnd), (SUPPORT.hwnd, duo.config.follow_key), ("window", FARMER.hwnd)]
    assert controller.events[3] == (FARMER.hwnd, "W")
    assert controller.events.count((SUPPORT.hwnd, duo.config.follow_key)) == 1
    assert (SUPPORT.hwnd, duo.config.support_key) not in controller.events
    assert (FARMER.hwnd, duo.config.rest) not in controller.events
    assert not duo.escaping


def test_healing_during_escape_resumes_follow_before_further_movement(monkeypatch):
    duo, controller = make_duo(regen_enabled=False, heal_key="F4", heal_cooldown=1)
    def moves():
        return [event for event in controller.events if event[0] == FARMER.hwnd
                and event[1] in {"W", "A", "S", "D"}]

    controller.capture = lambda: (.3 if moves() else 1., .9, False)
    original = duo.observe

    def observe():
        result = original()
        named(duo, "Mob")
        return result

    duo.observe = observe
    monkeypatch.setattr("hid_pilot.farm_melee.random.choice", lambda choices: "WA")
    assert duo.escape_from_unreachable("Mob") == .9
    healed = controller.events.index((SUPPORT.hwnd, "F4"))
    assert (SUPPORT.hwnd, duo.config.follow_key) in controller.events[healed + 1:]
    assert controller.role == FARMER


def test_stop_during_follow_prevents_escape_movement():
    duo, controller = make_duo()
    controller.key = Mock(side_effect=Stopped("STOP"))
    with pytest.raises(Stopped):
        duo.escape_from_unreachable("Mob")
    controller.key.assert_called_once_with(duo.config.follow_key, 80, exact=False)
    assert not duo.escaping


def test_pause_during_follow_cancels_escape_before_movement():
    duo, controller = make_duo()
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        controller.pause_generation += 1

    controller.key = key
    assert duo.escape_from_unreachable("Mob") is None
    assert controller.events == [
        ("window", SUPPORT.hwnd), (SUPPORT.hwnd, duo.config.follow_key), ("window", FARMER.hwnd)]
    assert not duo.following
    assert not duo.escaping
