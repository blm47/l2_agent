from unittest.mock import Mock

import pytest

from hid_pilot.desktop import Desktop, FocusLost, Target, activate


def test_activation_refreshes_geometry_without_changing_identity(monkeypatch):
    target = Target(hwnd=1, pid=2, title="LU4", rect=(0, 0, 100, 100))
    current = target.model_copy(update={"rect": (10, 10, 200, 200)})
    monkeypatch.setattr("hid_pilot.desktop.snapshot", lambda _: current)
    focus = Mock()
    monkeypatch.setattr("hid_pilot.desktop.win32gui.SetForegroundWindow", focus)
    monkeypatch.setattr("hid_pilot.desktop.win32gui.GetForegroundWindow", lambda: 1)
    assert activate(target) == current
    focus.assert_called_once_with(1)


def test_activation_allows_delayed_focus_but_capture_checks_it(monkeypatch):
    target = Target(hwnd=1, pid=2, title="LU4", rect=(0, 0, 100, 100))
    monkeypatch.setattr("hid_pilot.desktop.snapshot", lambda _: target)
    monkeypatch.setattr("hid_pilot.desktop.win32gui.SetForegroundWindow", Mock())
    monkeypatch.setattr("hid_pilot.desktop.win32gui.GetForegroundWindow", lambda: 3)
    monkeypatch.setattr("hid_pilot.desktop.win32gui.IsWindow", lambda _: True)
    monkeypatch.setattr("hid_pilot.desktop.win32gui.IsIconic", lambda _: False)
    monkeypatch.setattr("hid_pilot.desktop.win32process.GetWindowThreadProcessId", lambda _: (1, 2))
    monkeypatch.setattr("hid_pilot.desktop.win32gui.GetWindowText", lambda _: "GUI")
    capture_dc = Mock()
    monkeypatch.setattr("hid_pilot.desktop.win32gui.GetDC", capture_dc)
    desktop = Desktop(activate(target))
    with pytest.raises(FocusLost):
        desktop.capture()
    capture_dc.assert_not_called()
    monkeypatch.setattr("hid_pilot.desktop.win32gui.GetForegroundWindow", lambda: 1)
    desktop.check()


def test_reused_window_handle_is_not_activated(monkeypatch):
    target = Target(hwnd=1, pid=2, title="LU4", rect=(0, 0, 100, 100))
    monkeypatch.setattr("hid_pilot.desktop.snapshot",
                        lambda _: target.model_copy(update={"pid": 3}))
    focus = Mock()
    monkeypatch.setattr("hid_pilot.desktop.win32gui.SetForegroundWindow", focus)
    with pytest.raises(RuntimeError):
        activate(target)
    focus.assert_not_called()


@pytest.mark.parametrize("minimized", [False, True])
def test_inactive_or_minimized_window_is_recoverable(monkeypatch, minimized):
    target = Target(hwnd=1, pid=2, title="LU4", rect=(0, 0, 100, 100))
    monkeypatch.setattr("hid_pilot.desktop.win32gui.IsWindow", lambda _: True)
    monkeypatch.setattr("hid_pilot.desktop.win32gui.IsIconic", lambda _: minimized)
    monkeypatch.setattr("hid_pilot.desktop.win32process.GetWindowThreadProcessId", lambda _: (1, 2))
    monkeypatch.setattr("hid_pilot.desktop.snapshot", lambda _: target)
    monkeypatch.setattr("hid_pilot.desktop.win32gui.GetForegroundWindow", lambda: 3)
    monkeypatch.setattr("hid_pilot.desktop.win32gui.GetWindowText", lambda _: "Other")
    with pytest.raises(FocusLost):
        Desktop(target).check()
