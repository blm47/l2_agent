import ctypes
import time
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from l2_agent.action_controller import KEY_SCAN_CODES, ActionController, Input
from l2_agent.action_validator import ALLOWED_KEYS, ActionRequest
from l2_agent.geometry import ClientRect
from l2_agent.windows import WindowSnapshot


def wait_for(predicate):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    pytest.fail("Watchdog не отработал за секунду")


@pytest.fixture
def setup_controller(monkeypatch):
    environment = Mock()
    environment.snapshot.return_value = WindowSnapshot(
        hwnd=42,
        pid=123,
        title="Parsec",
        minimized=False,
        focused=True,
        rect=ClientRect(left=100, top=100, right=900, bottom=700),
    )
    environment.desktop.return_value = ClientRect(left=0, top=0, right=1920, bottom=1080)
    environment.modifiers_pressed.return_value = False
    environment.pressed.return_value = False
    environment.cursor.return_value = (500, 400)
    environment.owns_point.return_value = True
    events = []
    # Все тесты подменяют единственную точку отправки системного ввода.
    monkeypatch.setattr(ActionController, "_send", lambda self, event: events.append(event))
    controller = ActionController(environment)
    controller.hard_stop_ready = lambda: True
    controller.arm((42, 123))
    yield controller, environment, events
    controller.close()


@pytest.mark.parametrize(
    "key", ["WIN", "ALT", "CTRL", "SHIFT", "ALT+F4", "F10", "F12", "DELETE", "unknown"]
)
def test_unknown_and_shortcut_keys_rejected(key):
    with pytest.raises(ValidationError):
        ActionRequest(kind="key_down", key=key)


@pytest.mark.parametrize("duration", [0, -1, 1001, 100.0, True])
def test_duration_is_bounded(duration):
    with pytest.raises(ValidationError):
        ActionRequest(kind="key_down", key="W", duration_ms=duration)


def test_input_struct_matches_windows_abi():
    assert ctypes.sizeof(Input) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_watchdog_releases_bounded_key(setup_controller):
    controller, _environment, events = setup_controller
    controller.key_down("W", 30)
    wait_for(lambda: len(events) == 2)
    assert events[0].value.keyboard.vk == 0
    assert events[0].value.keyboard.scan == 0x11
    assert events[0].value.keyboard.flags == 0x08
    assert events[1].value.keyboard.scan == 0x11
    assert events[1].value.keyboard.flags == 0x0A
    assert not controller._keys


@pytest.mark.parametrize("button,flags", [("left", (2, 4)), ("right", (8, 16))])
def test_watchdog_releases_mouse(setup_controller, button, flags):
    controller, _environment, events = setup_controller
    controller.mouse_button_down(button, 30)
    wait_for(lambda: len(events) == 2)
    assert [event.value.mouse.flags for event in events] == list(flags)


@pytest.mark.parametrize("reason", ["STOP", "PAUSE", "EMERGENCY — F10"])
def test_stop_releases_and_blocks_future_actions(setup_controller, reason):
    controller, _environment, events = setup_controller
    controller.key_down("W", 1000)
    controller.stop(reason)
    assert len(events) == 2
    assert not controller.armed
    assert controller.state == reason
    with pytest.raises(ValueError):
        controller.key_down("I")
    assert len(events) == 2


@pytest.mark.parametrize("condition", ["focus", "minimized", "modifiers", "hotkey", "outside"])
def test_invalid_environment_cannot_send_down(setup_controller, condition):
    controller, environment, events = setup_controller
    snapshot = environment.snapshot.return_value
    if condition == "focus":
        environment.snapshot.return_value = snapshot.model_copy(update={"focused": False})
    elif condition == "minimized":
        environment.snapshot.return_value = snapshot.model_copy(update={"minimized": True})
    elif condition == "modifiers":
        environment.modifiers_pressed.return_value = True
    elif condition == "hotkey":
        controller.hard_stop_ready = lambda: False
    else:
        environment.desktop.return_value = ClientRect(left=0, top=0, right=800, bottom=600)
    with pytest.raises(ValueError):
        controller.key_down("W")
    assert not events


def test_focus_loss_during_hold_releases_without_requiring_focus(setup_controller):
    controller, environment, events = setup_controller
    controller.key_down("W", 1000)
    environment.snapshot.return_value = environment.snapshot.return_value.model_copy(
        update={"focused": False}
    )
    wait_for(lambda: len(events) == 2)
    assert not controller.armed


@pytest.mark.parametrize("point,visible", [((50, 50), True), ((500, 400), False)])
def test_click_outside_or_occluded_is_rejected(setup_controller, point, visible):
    controller, environment, events = setup_controller
    environment.cursor.return_value = point
    environment.owns_point.return_value = visible
    with pytest.raises(ValueError):
        controller.mouse_button_down("left")
    assert not events


def test_absolute_move_maps_virtual_desktop_and_current_window(setup_controller):
    controller, environment, events = setup_controller
    environment.desktop.return_value = ClientRect(left=-1920, top=0, right=1920, bottom=1080)
    controller.mouse_move_absolute_client(1, 1)
    event = events[0].value.mouse
    assert event.flags == 0xC001
    assert event.dx == int((899 + 1920 + 0.5) * 65536 / 3840)
    assert event.dy == int((699 + 0.5) * 65536 / 1080)


def test_out_of_bounds_move_is_rejected(setup_controller):
    controller, _environment, events = setup_controller
    with pytest.raises(ValidationError):
        controller.mouse_move_absolute_client(1.01, 0)
    assert not events


def test_rate_limit_blocks_second_move(setup_controller):
    controller, _environment, events = setup_controller
    controller.mouse_move_absolute_client(0.5, 0.5)
    with pytest.raises(ValueError):
        controller.mouse_move_absolute_client(0.6, 0.6)
    assert len(events) == 1


def test_release_failure_is_retained_and_retried(setup_controller, monkeypatch):
    controller, _environment, events = setup_controller
    controller.key_down("W", 1000)
    send = controller._send
    monkeypatch.setattr(controller, "_send", Mock(side_effect=OSError("SendInput failed")))
    controller.stop()
    assert "W" in controller._keys
    assert not controller.armed
    monkeypatch.setattr(controller, "_send", send)
    wait_for(lambda: not controller._keys)
    assert events[-1].value.keyboard.flags == 0x0A


def test_send_down_failure_attempts_release(setup_controller, monkeypatch):
    controller, _environment, _events = setup_controller
    send = Mock(side_effect=[OSError("SendInput failed"), None])
    monkeypatch.setattr(controller, "_send", send)
    with pytest.raises(OSError):
        controller.key_down("W")
    assert send.call_count == 2
    assert not controller._keys
    assert not controller.armed


def test_construct_bypass_is_revalidated(setup_controller):
    controller, _environment, events = setup_controller
    request = ActionRequest.model_construct(kind="key_down", key="ALT", duration_ms=5000)
    with pytest.raises(ValidationError):
        controller.execute(request)
    assert not events


def test_user_held_key_is_not_released(setup_controller):
    controller, environment, events = setup_controller
    environment.pressed.return_value = True
    with pytest.raises(ValueError):
        controller.key_down("W")
    assert not events


def test_missing_hotkey_blocks_arm(setup_controller):
    controller, _environment, _events = setup_controller
    controller.stop()
    controller.hard_stop_ready = lambda: False
    with pytest.raises(ValueError):
        controller.arm((42, 123))
    assert not controller.armed


@pytest.mark.parametrize("sent", [0, 1])
def test_sendinput_return_count_checked_without_native_input(sent):
    controller = ActionController.__new__(ActionController)
    controller._user32 = Mock()
    controller._user32.SendInput.return_value = sent
    event = Input()
    if sent == 0:
        with pytest.raises(OSError):
            controller._send(event)
    else:
        controller._send(event)
    args = controller._user32.SendInput.call_args.args
    assert args[0] == 1
    assert args[2] == ctypes.sizeof(Input)


def test_invalid_request_releases_existing_hold(setup_controller):
    controller, _environment, events = setup_controller
    controller.key_down("W", 1000)
    with pytest.raises(ValidationError):
        controller.key_down("ALT")
    assert len(events) == 2
    assert not controller._keys
    assert not controller.armed


def test_stop_releases_mouse(setup_controller):
    controller, _environment, events = setup_controller
    controller.mouse_button_down("right", 1000)
    controller.stop()
    assert [event.value.mouse.flags for event in events] == [8, 16]
    assert not controller._buttons


def test_cursor_leaving_window_interrupts_mouse_hold(setup_controller):
    controller, environment, events = setup_controller
    controller.mouse_button_down("right", 1000)
    environment.cursor.return_value = (0, 0)
    wait_for(lambda: len(events) == 2)
    assert not controller.armed


def test_window_change_before_dispatch_never_sends_down(setup_controller):
    controller, environment, events = setup_controller
    snapshot = environment.snapshot.return_value
    moved = snapshot.model_copy(
        update={"rect": ClientRect(left=101, top=100, right=901, bottom=700)}
    )
    environment.snapshot.side_effect = [snapshot, moved]
    with pytest.raises(ValueError):
        controller.key_down("W")
    assert all(event.value.keyboard.flags == 0x0A for event in events)


def test_key_press_and_click_release_without_waiting_for_watchdog(setup_controller):
    controller, _environment, events = setup_controller
    controller.key_press("I", 10)
    assert len(events) == 2
    assert not controller._keys
    time.sleep(0.06)
    controller.click("left", 10)
    assert len(events) == 4
    assert not controller._buttons


def test_relative_move_outside_releases_current_hold(setup_controller):
    controller, _environment, events = setup_controller
    controller.key_down("W", 1000)
    with pytest.raises(ValueError):
        controller.mouse_move_relative(-1000, 0)
    assert len(events) == 2
    assert not controller.armed


@pytest.mark.parametrize("key,scan", [("W", 0x11), ("R", 0x13)])
def test_scan_code_down_and_stop_release_match_parsec_example(setup_controller, key, scan):
    controller, _environment, events = setup_controller
    controller.key_down(key, 1000)
    controller.stop("EMERGENCY — F10")
    assert len(events) == 2
    assert [
        (event.type, event.value.keyboard.vk, event.value.keyboard.scan, event.value.keyboard.flags)
        for event in events
    ] == [(1, 0, scan, 0x08), (1, 0, scan, 0x0A)]


def test_scan_code_table_covers_allowlist():
    assert KEY_SCAN_CODES.keys() == ALLOWED_KEYS.keys()
    assert len(set(KEY_SCAN_CODES.values())) == len(KEY_SCAN_CODES)
