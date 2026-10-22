import threading
from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.chat_preview import latest_chat_preview
from hid_pilot.farm_observer import FarmIdentityCache
from hid_pilot.system_chat import ChatMessage, ChatTracker, parse_message
from test_melee_farm import harness


@pytest.fixture(autouse=True)
def enable_dormant_chat_for_unit_tests(monkeypatch):
    monkeypatch.setattr("hid_pilot.system_chat.SYSTEM_CHAT_ENABLED", True)


@pytest.mark.parametrize("text, kind, amount", [
    ("You cannot move while sitting.", "sitting_block", None),
    ("Вы не можете двигаться, пока сидите", "sitting_block", None),
    ("You use Power Strike.", "skill_used", None),
    ("Over-hit!", "overhit", None),
    ("You have acquired 530 XP and 42 SP.", "experience", 530),
    ("78 HP has been restored.", "hp_restored", 78),
    ("Use Soulshot: No Grade.", "soulshot", None),
    ("Power of the spirits enabled.", "unknown", None),
    ("Someone says You cannot move while sitting", "unknown", None),
])
def test_sample_chat_messages_are_parsed_without_guessing(text, kind, amount):
    event = parse_message(text, 10.)
    assert (event.kind, event.amount, event.at) == (kind, amount, 10.)
    if kind == "experience":
        assert event.sp == 42


def test_tracker_ignores_history_repeated_frames_and_recognizes_new_scrolled_line():
    tracker = ChatTracker()
    old = ["You use Power Strike.", "You cannot move while sitting."]
    assert tracker.update(old, 0.) == []
    assert tracker.update(old, .5) == []
    messages = tracker.update(old[1:] + ["78 HP has been restored."], 1.)
    assert [message.kind for message in messages] == ["hp_restored"]
    messages = tracker.update([old[1], "78 HP has been restored.", old[1]], 1.5)
    assert [message.kind for message in messages] == ["sitting_block"]


def test_tracker_rebases_after_gap_clear_or_complete_history_change():
    tracker = ChatTracker()
    tracker.update(["old"], 0.)
    assert tracker.update(["old", "You cannot move while sitting."], 4.) == []
    assert tracker.update(["different", "You cannot move while sitting."], 4.5) == []
    assert tracker.update([], 5.) == []
    assert tracker.update(["You cannot move while sitting."], 5.5) == []


def chat_farmer(monkeypatch):
    farmer, controller, _, _ = harness()
    farmer.profile = BarProfile(width=200, height=100, system_chat_enabled=True,
                               system_chat=BarBox(x1=0, y1=0, x2=1, y2=1))
    cache = Mock()
    cache.chat_ready.return_value = True
    cache.take_chat_events.return_value = []
    farmer.identity_cache = cache
    now = [10.]
    monkeypatch.setattr("hid_pilot.farm_melee.time.monotonic", lambda: now[0])
    return farmer, controller, cache, now


def test_fresh_sitting_error_after_attack_stands_once(monkeypatch):
    farmer, controller, cache, now = chat_farmer(monkeypatch)
    farmer.press(farmer.config.attack)
    cache.take_chat_events.return_value = [ChatMessage("sitting_block", "You cannot move while sitting.", 10.5)]
    now[0] = 11.
    farmer.reconcile_system_chat()
    farmer.reconcile_system_chat()
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.attack, farmer.config.stand]
    assert not farmer.sitting


@pytest.mark.parametrize("reason", ["no_command", "old_line", "stale_result", "sitting", "disabled", "pause", "no_baseline"])
def test_ambiguous_or_old_chat_never_toggles_posture(monkeypatch, reason):
    farmer, controller, cache, now = chat_farmer(monkeypatch)
    if reason == "no_baseline":
        cache.chat_ready.return_value = False
    if reason != "no_command":
        farmer.press(farmer.config.attack)
    at = 9. if reason == "old_line" else 10.5
    now[0] = 15. if reason == "stale_result" else 11.
    cache.take_chat_events.return_value = [ChatMessage("sitting_block", "error", at)]
    if reason == "sitting":
        farmer.sitting = True
    if reason == "disabled":
        farmer.profile.system_chat_enabled = False
    if reason == "pause":
        controller.pause_generation = 1
    farmer.reconcile_system_chat()
    assert all(call.args[0] != farmer.config.stand for call in controller.key.call_args_list)


def test_correction_cooldown_blocks_repeated_new_errors(monkeypatch):
    farmer, controller, cache, now = chat_farmer(monkeypatch)
    for stamp in (10., 12., 16.):
        now[0] = stamp
        farmer.combat_key("F1", 700)
        cache.take_chat_events.return_value = [ChatMessage("sitting_block", "error", stamp + .1)]
        now[0] += .5
        farmer.reconcile_system_chat()
    assert sum(call.args[0] == farmer.config.stand for call in controller.key.call_args_list) == 2


def test_chat_ocr_is_optional_uses_only_roi_and_resets_on_uncertainty(monkeypatch, caplog):
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    monkeypatch.setattr("hid_pilot.farm_observer.time.monotonic", lambda: 1.)
    box = BarBox(x1=.1, y1=.2, x2=.9, y2=.8)
    profile = BarProfile(width=200, height=100, system_chat=box)
    cache = FarmIdentityCache(profile, None)
    cache.numbers = Mock()
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    cache.read_chat(image, 0.)
    cache.numbers.detect.assert_not_called()
    profile.system_chat_enabled = True
    caplog.set_level("INFO", logger="hid_pilot.system_chat")
    bounds = [[0, 0], [100, 0], [100, 10], [0, 10]]
    cache.numbers.detect.return_value = [(bounds, "old", .99)]
    cache.read_chat(image, 0.)
    assert cache.numbers.detect.call_args.args[0].shape == (60, 160, 3)
    assert latest_chat_preview().lines == ("old",)
    assert cache.take_chat_events() == []
    next_bounds = [[0, 20], [100, 20], [100, 30], [0, 30]]
    cache.numbers.detect.return_value = [(bounds, "old", .99),
                                        (next_bounds, "You cannot move", .99),
                                        ([[105, 19], [155, 19], [155, 29], [105, 29]], "while sitting.", .99)]
    cache.read_chat(image, .5)
    assert [event.kind for event in cache.take_chat_events()] == ["sitting_block"]
    assert len([record for record in caplog.records if record.name == "hid_pilot.system_chat"]) == 1
    cache.read_chat(image, .7)
    assert len([record for record in caplog.records if record.name == "hid_pilot.system_chat"]) == 1
    assert cache.take_chat_events() == []
    cache.numbers.detect.return_value = [(bounds, "uncertain", .7)]
    cache.read_chat(image, 1.)
    assert not cache.chat_ready()
    assert latest_chat_preview().lines == ("uncertain",)
    assert "Низкая уверенность" in latest_chat_preview().status


def test_system_chat_profile_defaults_are_disabled_and_round_trip():
    profile = BarProfile(width=200, height=100)
    assert not profile.system_chat_enabled and profile.system_chat is None
    profile.system_chat = BarBox(x1=0, y1=.5, x2=.5, y2=1)
    profile.system_chat_enabled = True
    assert BarProfile.model_validate_json(profile.model_dump_json()) == profile
