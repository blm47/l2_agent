from unittest.mock import Mock

import pytest

from l2_agent import windows


@pytest.fixture
def backend(monkeypatch):
    gui = Mock()
    gui.IsWindow.return_value = True
    gui.IsWindowVisible.return_value = True
    gui.IsIconic.return_value = False
    gui.GetClientRect.return_value = (0, 0, 800, 600)
    gui.ClientToScreen.side_effect = lambda hwnd, point: (point[0] - 900, point[1] + 50)
    gui.GetForegroundWindow.return_value = 42
    gui.GetWindowText.return_value = "Parsec"
    process = Mock()
    process.GetWindowThreadProcessId.return_value = (1, 123)
    process.QueryFullProcessImageName.return_value = r"C:\Program Files\Parsec\parsecd.exe"
    api = Mock()
    api.OpenProcess.return_value = Mock(__int__=Mock(return_value=100))
    monkeypatch.setattr(windows, "process_image_name", process.QueryFullProcessImageName)
    monkeypatch.setattr(windows, "win32gui", gui)
    monkeypatch.setattr(windows, "win32process", process)
    monkeypatch.setattr(windows, "win32api", api)
    return gui, process, api


def test_snapshot_refreshes_geometry(backend):
    gui, _, api = backend
    manager = windows.ParsecWindowManager()
    first = manager.snapshot(42)
    assert first.focused
    assert first.rect.left == -900
    assert first.rect.width == 800
    gui.GetClientRect.return_value = (0, 0, 1200, 700)
    assert manager.snapshot(42).rect.width == 1200
    assert api.OpenProcess.return_value.Close.call_count == 2


def test_minimized_has_no_geometry(backend):
    gui, _, _ = backend
    gui.IsIconic.return_value = True
    assert windows.ParsecWindowManager().snapshot(42).rect is None
    gui.GetClientRect.assert_not_called()


def test_foreign_process_rejected_even_with_parsec_title(backend):
    _, process, api = backend
    process.QueryFullProcessImageName.return_value = r"C:\browser.exe"
    with pytest.raises(ValueError):
        windows.ParsecWindowManager().snapshot(42)
    api.OpenProcess.return_value.Close.assert_called_once()


def test_reused_handle_rejected(backend):
    with pytest.raises(ValueError):
        windows.ParsecWindowManager().snapshot(42, expected_pid=999)


def test_discovery_filters_hidden_and_foreign_windows(backend):
    gui, process, _ = backend
    gui.EnumWindows.side_effect = lambda callback, arg: [callback(h, arg) for h in (1, 2, 3)]
    gui.IsWindowVisible.side_effect = lambda hwnd: hwnd != 2
    process.QueryFullProcessImageName.side_effect = [r"C:\parsecd.exe", r"C:\other.exe"]
    assert [item.hwnd for item in windows.ParsecWindowManager().discover()] == [1]


def test_closed_window_rejected(backend):
    gui, _, _ = backend
    gui.IsWindow.return_value = False
    with pytest.raises(ValueError):
        windows.ParsecWindowManager().snapshot(42)
