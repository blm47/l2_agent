import os
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from hid_pilot.__main__ import MainWindow
from hid_pilot.duo import DuoConfig
from hid_pilot.desktop import Target


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    farmer = Target(hwnd=101, pid=11, title="LU4 фармер", rect=(0, 0, 100, 100))
    support = Target(hwnd=102, pid=22, title="LU4 хилер", rect=(100, 0, 200, 100))
    monkeypatch.setattr("hid_pilot.__main__.user_data_dir", lambda: tmp_path)
    monkeypatch.setattr("hid_pilot.__main__.HardStopHotkey", lambda *args: Mock(available=True))
    monkeypatch.setattr("hid_pilot.__main__.windows", lambda: [farmer, support])
    monkeypatch.setattr("hid_pilot.duo_panel.snapshot", lambda hwnd: farmer if hwnd == farmer.hwnd else support)
    result = MainWindow()
    result.targets.setCurrentIndex(0)
    result.duo.targets.setCurrentIndex(1)
    result.duo.capture_second()
    result.duo.select_script(tmp_path / "team.duo.json", DuoConfig())
    yield result, app, farmer, support
    result.close()


def test_two_roles_have_same_controls_and_separate_profiles(window):
    gui, _, farmer, support = window
    first, second = gui.duo.panels.values()
    assert first.target() == farmer
    assert second.target() == support
    assert first.path().parent.name == "farmer"
    assert second.path().parent.name == "support"
    assert first.path() != second.path()
    setup = Mock()
    gui.auto_numbers = setup
    second.own_requested.emit()
    setup.assert_called_once_with(False, True, second)


def test_configuration_is_separate_from_single_window_script(window):
    gui, _, _, _ = window
    gui.editor.setPlainText("invalid_single_window_script")
    assert gui.duo.config().heal_threshold == .6
    assert gui.duo.config().rebuff_minutes == 19
    assert "set rebuff_minutes" in gui.duo.editor.toPlainText()
    gui.duo.editor.setPlainText('{"heal_threshold": 0.75, "heal_key": "F8"}')
    assert gui.duo.config().heal_key == "F8"
    assert gui.duo.config().heal_threshold == .75


def test_saving_script_preserves_comments_values_and_profile_directory(window, monkeypatch):
    gui, _, _, _ = window
    previous_base = gui.duo.base()
    destination = gui.duo.path.with_suffix(".pilot")
    text = '# Мои настройки\nset buff_key F11\nset heal_key ""\nset rebuff_minutes 17\n'
    gui.duo.editor.setPlainText(text)
    monkeypatch.setattr("hid_pilot.duo_panel.QFileDialog.getSaveFileName",
                        lambda *args: (str(destination), ""))
    gui.duo.save_script()
    assert destination.read_text("utf-8") == text
    assert gui.duo.editor.toPlainText() == text
    assert gui.duo.config().buff_key == "F11"
    assert gui.duo.config().heal_key is None
    assert gui.duo.config().rebuff_minutes == 17
    assert gui.duo.base() == previous_base


def test_refresh_preserves_second_selection(window):
    gui, _, _, support = window
    gui.refresh()
    assert gui.duo.targets.currentData() == support
    assert gui.duo.captured == support


def test_live_statuses_stay_enabled_and_update_while_running(window):
    gui, _, _, _ = window
    gui.duo.set_running(True)
    gui.duo.show_observation({"hp": .8, "target_hp": .5, "support_hp": .4,
                              "support_mp": .65, "support_buff_active": True})
    assert gui.duo.live_status.isEnabled()
    assert "65%" in gui.duo.live_status.text()
    assert "40%" in gui.duo.panels["support"].status.text()
    assert "80%" in gui.duo.panels["farmer"].status.text()
    assert gui.duo.roles.isEnabled()
    assert not gui.duo.capture_button.isEnabled()
    gui.duo.set_running(False)
    assert gui.duo.capture_button.isEnabled()


def test_repeated_save_does_not_append_duo_suffix(window, monkeypatch):
    gui, _, _, _ = window
    destination = gui.duo.path.with_suffix(".pilot")
    previous_base = gui.duo.base()
    calls = []

    def choose(parent, title, suggested, filters):
        calls.append((suggested, filters))
        # Native-диалог использует простые расширения, без составного .duo.pilot.
        assert "*.duo." not in filters
        return suggested, "Сценарии (*.pilot)"

    monkeypatch.setattr("hid_pilot.duo_panel.QFileDialog.getSaveFileName", choose)
    for _ in range(3):
        gui.duo.save_script()
        assert gui.duo.path == destination
        assert gui.duo.base() == previous_base
    assert all(suggested == str(destination) for suggested, _ in calls)


@pytest.mark.parametrize("name,selected,suffix", [
    ("new", "Сценарии (*.pilot)", ".pilot"),
    ("new.duo", "Сценарии (*.pilot)", ".pilot"),
    ("new.duo", "JSON (*.json)", ".json"),
])
def test_save_adds_only_missing_final_extension(window, monkeypatch, name, selected, suffix):
    gui, _, _, _ = window
    destination = gui.duo.path.parent / name
    monkeypatch.setattr("hid_pilot.duo_panel.QFileDialog.getSaveFileName",
                        lambda *args: (str(destination), selected))
    gui.duo.save_script()
    expected = destination.with_name(name + suffix)
    assert gui.duo.path == expected
    assert expected.exists()
    if suffix == ".json":
        assert DuoConfig.model_validate_json(expected.read_text("utf-8")) == gui.duo.config()


def test_changed_role_context_invalidates_pending_calibration(window):
    gui, _, _, support = window
    panel = gui.duo.panels["support"]
    payload = {"panel": panel, "path": panel.path(), "target": support}
    assert gui.setup_context_matches(payload)
    gui.duo.select_script(Path("different.duo.json"), DuoConfig())
    assert not gui.setup_context_matches(payload)


def test_manual_calibration_captures_second_window_into_its_profile(window, monkeypatch):
    gui, _, _, support = window
    review = Mock()
    monkeypatch.setattr(gui, "review_auto_numbers", review)
    monkeypatch.setattr("hid_pilot.__main__.activate", lambda target: target)
    monkeypatch.setattr("hid_pilot.__main__.Desktop", lambda target: Mock(capture=lambda: "frame"))
    monkeypatch.setattr("hid_pilot.__main__.QTimer.singleShot", lambda delay, callback: callback())
    gui.setup_duo("support", False, True)
    payload = review.call_args.args[0]
    assert payload["target"] == support
    assert payload["path"] == gui.duo.role_path("support")
    assert payload["panel"] is gui.duo.panels["support"]
    assert payload["path"].parent.is_dir()
    assert gui.path is None


def test_second_capture_rejects_farmer_window(window):
    gui, _, _, support = window
    gui.duo.targets.setCurrentIndex(0)
    gui.duo.capture_second()
    assert gui.duo.captured == support
    assert "должно отличаться" in gui.status.text()


def test_duo_launch_uses_one_transport_and_ignores_single_window_script(window, monkeypatch):
    gui, app, farmer, support = window
    gui.editor.setPlainText("invalid_single_window_script")
    gui.tabs.setCurrentWidget(gui.duo)
    prepare = Mock(return_value=Mock())
    runtime = Mock()
    transport = Mock()
    controller = Mock()
    monkeypatch.setattr("hid_pilot.__main__.prepare_duo", prepare)
    monkeypatch.setattr("hid_pilot.__main__.load_duo_party", Mock())
    monkeypatch.setattr("hid_pilot.__main__.snapshot", lambda hwnd: farmer if hwnd == farmer.hwnd else support)
    monkeypatch.setattr("hid_pilot.__main__.activate", lambda target: target)
    monkeypatch.setattr("hid_pilot.__main__.PicoTransport", Mock(return_value=transport))
    monkeypatch.setattr("hid_pilot.__main__.ActionController", Mock(return_value=controller))
    monkeypatch.setattr("hid_pilot.__main__.DuoFarmer", runtime)
    gui.launch()
    assert gui.worker is not None
    assert gui.duo.isEnabled()
    assert gui.duo.live_status.isEnabled()
    assert not gui.duo.capture_button.isEnabled()
    gui.worker.join(timeout=2)
    assert not gui.worker.is_alive()
    app.processEvents()
    prepare.assert_called_once_with(gui.duo.base(), farmer, support)
    runtime.return_value.run.assert_called_once_with()
    runtime.return_value.setup_observation.assert_called_once_with(gui.duo.base() / "farmer")
    runtime.return_value.close_observation.assert_called_once_with()
    controller.start.assert_called_once_with()
    controller.close.assert_called_once_with()
    assert gui.duo.isEnabled()


def test_duo_launch_rejects_missing_second_window(window):
    gui, _, _, _ = window
    gui.tabs.setCurrentWidget(gui.duo)
    gui.duo.captured = None
    gui.launch()
    assert gui.worker is None
    assert "Захватите второе окно" in gui.status.text()
