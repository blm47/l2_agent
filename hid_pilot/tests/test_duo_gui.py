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
    # Малые HWND совпадают со счётчиком окон Qt offscreen в совместном прогоне.
    farmer = Target(hwnd=100001, pid=11, title="LU4 МА", rect=(0, 0, 100, 100))
    support = Target(hwnd=100002, pid=22, title="LU4 саппорт", rect=(100, 0, 200, 100))
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
    assert gui.tabs.tabText(gui.tabs.indexOf(gui.duo)) == "Дуо: МА + саппорт"
    assert gui.duo.roles.tabText(0) == "Окно 1 — МА"
    assert gui.duo.roles.tabText(1) == "Окно 2 — саппорт"
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


@pytest.mark.parametrize("layout_failure", [False, True])
def test_duo_checks_both_layouts_before_start(window, monkeypatch, layout_failure):
    gui, _, farmer, support = window
    controller = Mock()
    if layout_failure:
        controller.prepare_keyboard.side_effect = RuntimeError("Нет ENG")
    monkeypatch.setattr("hid_pilot.__main__.ActionController", Mock(return_value=controller))
    monkeypatch.setattr("hid_pilot.__main__.Desktop", Mock())
    monkeypatch.setattr("hid_pilot.__main__.PicoTransport", Mock())
    monkeypatch.setattr("hid_pilot.__main__.prepare_duo", Mock())
    monkeypatch.setattr("hid_pilot.__main__.load_duo_party", Mock())
    monkeypatch.setattr("hid_pilot.__main__.snapshot", lambda hwnd: farmer if hwnd == farmer.hwnd else support)
    monkeypatch.setattr("hid_pilot.__main__.activate", lambda target: target)
    scenario = Mock()
    monkeypatch.setattr("hid_pilot.__main__.DuoFarmer", Mock(return_value=scenario))
    gui.tabs.setCurrentWidget(gui.duo)
    gui.port.setText("COM7")
    gui.cancel = Mock()
    gui.cancel.wait.return_value = False
    gui.cancel.is_set.return_value = False
    gui.launch()
    gui.worker.join(timeout=2)
    assert not gui.worker.is_alive()
    controller.prepare_keyboard.assert_called_once_with((farmer, support))
    if layout_failure:
        controller.start.assert_not_called()
        scenario.run.assert_not_called()
    else:
        methods = [call[0] for call in controller.method_calls]
        assert methods.index("prepare_keyboard") < methods.index("start")
        scenario.run.assert_called_once()


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
    assert "40%" in gui.duo.live_status.text()
    assert "80%" in gui.duo.live_status.text()
    for panel in gui.duo.panels.values():
        assert panel.status.isHidden()
        assert panel.enabled.isHidden()
    assert gui.duo.roles.isEnabled()
    assert gui.duo.roles.isHidden()
    assert gui.duo.statistics.isEnabled()
    assert gui.duo.statistics.isReadOnly()
    assert gui.duo.settings_tabs.isEnabled()
    assert gui.duo.isEnabled()
    assert not gui.duo.capture_button.isEnabled()
    gui.duo.set_running(False)
    assert gui.duo.capture_button.isEnabled()
    assert not gui.duo.roles.isHidden()


def test_duo_setup_does_not_take_space_from_monitoring(window):
    from PySide6.QtWidgets import QSizePolicy

    gui, app, _, _ = window
    panel = gui.duo
    gui.resize(1100, 850)
    gui.tabs.setCurrentWidget(panel)
    gui.show()
    app.processEvents()
    assert panel.roles.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
    assert panel.roles.height() < 220
    idle_height = panel.details.height()
    panel.set_running(True)
    app.processEvents()
    assert panel.roles.isHidden()
    assert panel.details.height() > idle_height
    assert panel.statistics.verticalScrollBar().isEnabled()
    panel.set_running(False)


def test_detailed_ma_statistics_are_not_overwritten_by_short_updates(window):
    gui, _, _, _ = window
    from hid_pilot.party_statistics import format_statistics

    gui.duo.set_running(True)
    text = format_statistics({"hp": .8, "mp": .6}, {"type": "monster", "name": "Gremlin", "hp": .5},
                             [], {}, "Обычная атака") + "\nУбито: 12"
    gui.events.duo_statistics.emit({"text": text})
    gui.events.duo_observation.emit({"hp": .8, "mp": .6, "target_hp": .5, "support_hp": .4})
    assert gui.duo.statistics.toPlainText() == text
    gui.duo.set_running(False)
    assert gui.duo.statistics.toPlainText() == text
    assert gui.duo.panels["farmer"].status.isHidden()


def test_manual_observation_uses_single_statistics_panel(window):
    gui, _, _, _ = window
    panel = gui.duo.panels["farmer"]
    gui.duo.roles.setCurrentWidget(panel)
    panel.updated.emit("СВОИ СТАТЫ\nHP 80%")
    assert gui.duo.statistics.toPlainText() == "СВОИ СТАТЫ\nHP 80%"
    assert panel.status.isHidden()


def test_background_party_result_cannot_replace_running_script_statistics(window):
    import time

    gui, _, _, _ = window
    panel = gui.duo.panels["farmer"]
    panel.enabled.setChecked(True)
    panel.running = lambda: True
    panel.status.setText("Статистика работающего сценария")
    panel.receive({"generation": panel.generation, "target": panel.target(),
                   "path": panel.profile_path(), "started": time.monotonic(), "text": "Старый фоновой кадр"})
    assert panel.status.text() == "Статистика работающего сценария"


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
