from unittest.mock import Mock

import pytest

from hid_pilot.controller import ActionController


def controller_with_clock(monkeypatch):
    transport = Mock(protocol="HID_PILOT_V4")
    controller = ActionController(Mock(), transport, lambda: True)
    controller.wait = Mock()
    controller.stopped.wait = Mock(return_value=False)
    now = [0.]

    def clock():
        now[0] += .02
        return now[0]

    monkeypatch.setattr("hid_pilot.controller.time.monotonic", clock)
    return controller, transport


@pytest.mark.parametrize("kind,name", [("KEY", "F6"), ("BUTTON", "left"), ("CHORD", "SHIFT+A")])
def test_release_and_windows_keyup_precede_press(monkeypatch, kind, name):
    controller, transport = controller_with_clock(monkeypatch)
    events = []
    transport.release.side_effect = lambda: events.append("release")
    transport.hold.side_effect = lambda *args: events.append("press")

    def state(key):
        events.append("alt-check")
        return 0

    monkeypatch.setattr("hid_pilot.controller.win32api.GetAsyncKeyState", state)
    controller._hold(kind, name, 80)
    assert events[0] == "release"
    assert events[1:4] == ["alt-check"] * 3
    assert events[4] == "press"


@pytest.mark.parametrize("alt_key", [0x12, 0xA4, 0xA5])
def test_stuck_alt_stops_without_any_farm_key(monkeypatch, alt_key):
    controller, transport = controller_with_clock(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.win32api.GetAsyncKeyState",
                        lambda key: 0x8000 if key == alt_key else 0)
    with pytest.raises(RuntimeError, match="ALT остаётся зажатым"):
        controller.key("R", 100)
    transport.hold.assert_not_called()
    transport.stop.assert_called()
    assert controller.stopped.is_set()


def test_release_ack_failure_never_allows_next_key(monkeypatch):
    controller, transport = controller_with_clock(monkeypatch)
    transport.release.side_effect = OSError("RELEASE timeout")
    with pytest.raises(OSError, match="RELEASE timeout"):
        controller.key("R", 100)
    transport.hold.assert_not_called()
    assert controller.stopped.is_set()


def test_waits_for_delayed_alt_keyup_before_press(monkeypatch):
    controller, transport = controller_with_clock(monkeypatch)
    checks = [0]

    def state(key):
        checks[0] += 1
        return 0x8000 if checks[0] <= 2 else 0

    monkeypatch.setattr("hid_pilot.controller.win32api.GetAsyncKeyState", state)
    controller.key("R", 100)
    transport.hold.assert_called_once_with("KEY", "R", 100)
    assert checks[0] >= 5
