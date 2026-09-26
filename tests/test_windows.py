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
    manager = windows.GameWindowManager()
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
    assert windows.GameWindowManager().snapshot(42).rect is None
    gui.GetClientRect.assert_not_called()


def test_arbitrary_process_is_available(backend):
    _, process, api = backend
    process.QueryFullProcessImageName.return_value = r"C:\browser.exe"
    assert windows.GameWindowManager().snapshot(42).executable_name == "browser.exe"
    api.OpenProcess.return_value.Close.assert_called_once()


def test_reused_handle_rejected(backend):
    with pytest.raises(ValueError):
        windows.GameWindowManager().snapshot(42, expected_pid=999)


def test_discovery_filters_only_hidden_windows(backend):
    gui, process, _ = backend
    gui.EnumWindows.side_effect = lambda callback, arg: [callback(h, arg) for h in (1, 2, 3)]
    gui.IsWindowVisible.side_effect = lambda hwnd: hwnd != 2
    process.QueryFullProcessImageName.side_effect = [r"C:\parsecd.exe", r"C:\other.exe"]
    assert [item.hwnd for item in windows.GameWindowManager().discover()] == [1, 3]


def test_closed_window_rejected(backend):
    gui, _, _ = backend
    gui.IsWindow.return_value = False
    with pytest.raises(ValueError):
        windows.GameWindowManager().snapshot(42)


@pytest.mark.parametrize("executable", ["lu4.bin.exe", "lu4.bin", "LU4.exe"])
def test_local_lu4_supported(backend, executable):
    gui, process, _ = backend
    gui.GetWindowText.return_value = "LU4 - Player"
    process.QueryFullProcessImageName.return_value = "C:\\Games\\" + executable
    snapshot = windows.GameWindowManager().snapshot(42, expected_pid=123)
    assert snapshot.title == "LU4 - Player"
    assert snapshot.rect.width == 800


def test_title_changes_do_not_invalidate_selected_process(backend):
    gui, process, _ = backend
    gui.GetWindowText.return_value = "LU4 - Player"
    process.QueryFullProcessImageName.return_value = r"C:\browser.exe"
    first = windows.GameWindowManager().snapshot(42, 123)
    gui.GetWindowText.return_value = "Character name"
    second = windows.GameWindowManager().snapshot(42, 123)
    assert first.pid == second.pid
    assert second.title == "Character name"


@pytest.mark.parametrize(
    "executable", ["L2.exe", "l2.bin", "l2.bin.exe", "Lineage2.exe", "LineageII.exe"]
)
def test_lineage_client_supported_without_standard_title(backend, executable):
    gui, process, _ = backend
    gui.GetWindowText.return_value = "Character - Server"
    process.QueryFullProcessImageName.return_value = "C:\\Games\\" + executable
    snapshot = windows.GameWindowManager().snapshot(42, 123)
    assert snapshot.executable_name == executable.lower()


def test_all_lineage_instances_are_listed(backend):
    gui, process, _ = backend
    gui.EnumWindows.side_effect = lambda callback, arg: [callback(h, arg) for h in (1, 2, 3)]
    process.GetWindowThreadProcessId.side_effect = [(1, 101), (1, 102), (1, 103)]
    process.QueryFullProcessImageName.side_effect = [r"C:\l2.exe", r"D:\l2.exe", r"C:\browser.exe"]
    gui.GetWindowText.return_value = "Lineage II"
    discovered = windows.GameWindowManager().discover()
    assert [(w.hwnd, w.pid) for w in discovered] == [(1, 101), (2, 102), (3, 103)]


def test_window_without_title_or_process_access_is_listed(backend):
    gui, _, api = backend
    gui.EnumWindows.side_effect = lambda callback, arg: callback(42, arg)
    gui.GetWindowText.return_value = ""
    api.OpenProcess.side_effect = OSError("Access denied")
    discovered = windows.GameWindowManager().discover()
    assert len(discovered) == 1
    assert discovered[0].hwnd == 42
    assert discovered[0].executable_name == ""
