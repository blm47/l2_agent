import threading
from ctypes import wintypes
from unittest.mock import Mock

from l2_agent.hard_stop import HardStopHotkey


def test_hotkey_dispatches_in_its_thread_and_unregisters(monkeypatch):
    api = Mock()
    api.RegisterHotKey.return_value = 1
    called = threading.Event()
    delivered = False

    def peek(message, *_args):
        nonlocal delivered
        if delivered:
            return 0
        delivered = True
        assert isinstance(message._obj, wintypes.MSG)
        message._obj.message = 0x0312
        message._obj.wParam = 1
        return 1

    api.PeekMessageW.side_effect = peek
    monkeypatch.setattr("l2_agent.hard_stop.ctypes.WinDLL", lambda *_args, **_kwargs: api)
    hotkey = HardStopHotkey(called.set)
    try:
        assert hotkey.available
        assert called.wait(1)
        api.RegisterHotKey.assert_called_once_with(None, 1, 0x4000, 0xDC)
    finally:
        hotkey.close()
    assert not hotkey.available
    api.UnregisterHotKey.assert_called_once_with(None, 1)


def test_registration_failure_disables_input(monkeypatch):
    api = Mock()
    api.RegisterHotKey.return_value = 0
    called = threading.Event()
    monkeypatch.setattr("l2_agent.hard_stop.ctypes.WinDLL", lambda *_args, **_kwargs: api)
    hotkey = HardStopHotkey(called.set)
    try:
        assert not hotkey.available
        assert called.wait(1)
        assert hotkey.error
    finally:
        hotkey.close()
    api.UnregisterHotKey.assert_not_called()
