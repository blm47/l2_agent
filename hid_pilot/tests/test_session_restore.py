from unittest.mock import Mock

from hid_pilot.__main__ import MainWindow, ScriptNotifications
from hid_pilot.desktop import Target
from hid_pilot.duo import DuoConfig
from hid_pilot.session import match_session_window
from test_duo import make_duo
from test_duo_gui import window


def test_reopening_restores_scripts_and_role_order_after_client_restart(window, monkeypatch):
    first, app, farmer, support = window
    root = first.duo.path.parent
    solo_path = root / "last-solo.pilot"
    solo_path.write_text("# Сохранённый сценарий\nwait 2\n", encoding="utf-8")
    first.path = solo_path
    first.duo.path.write_text(DuoConfig(heal_key="F9").model_dump_json(), encoding="utf-8")
    duo_path = first.duo.path
    first.tabs.setCurrentWidget(first.duo)
    first.save_layout()
    first.close()
    new_farmer = farmer.model_copy(update={"hwnd": 201, "pid": 21})
    new_support = support.model_copy(update={"hwnd": 202, "pid": 22})
    monkeypatch.setattr("hid_pilot.__main__.windows", lambda: [new_support, new_farmer])
    monkeypatch.setattr("hid_pilot.duo_panel.snapshot",
                        lambda hwnd: new_farmer if hwnd == 201 else new_support)
    transport = Mock()
    monkeypatch.setattr("hid_pilot.__main__.PicoTransport", transport)
    restored = MainWindow()
    try:
        app.processEvents()
        assert restored.path == solo_path
        assert restored.editor.toPlainText() == solo_path.read_text("utf-8")
        assert restored.duo.path == duo_path
        assert restored.duo.config().heal_key == "F9"
        assert restored.targets.currentData() == new_farmer
        assert restored.duo.captured == new_support
        assert restored.tabs.currentWidget() is restored.duo
        assert restored.worker is None
        transport.assert_not_called()
    finally:
        restored.close()


def test_ambiguous_same_titles_are_not_assigned_arbitrary_roles():
    saved = Target(hwnd=1, pid=11, title="LU4", rect=(0, 0, 100, 100))
    other = saved.model_copy(update={"hwnd": 2, "pid": 22})
    third = saved.model_copy(update={"hwnd": 3, "pid": 33})
    assert match_session_window([other, third], saved) is None
    assert match_session_window([other, saved], saved) == saved


def test_missing_last_script_does_not_break_startup(window):
    first, _, _, _ = window
    missing = first.duo.path.parent / "removed.pilot"
    first.layout_settings.setValue("session/solo_script", str(missing))
    first.restore_session()
    assert first.path is None
    assert first.worker is None
    assert "Не удалось восстановить сценарий" in first.journal.toPlainText()


def test_duo_statistics_panel_uses_same_target_and_kill_statistics_as_solo(window):
    gui, app, _, _ = window
    duo, _ = make_duo()
    duo.notify = ScriptNotifications(gui.events, duo_mode=True)
    duo.display_data = ({"hp": .8, "mp": .9, "cp": .7},
                        {"name": "Gremlin", "type": "monster", "hp": .5, "level": 7}, [])
    duo.mob_statistics.record_kill("Gremlin")
    duo.publish_statistics()
    app.processEvents()
    text = gui.duo.statistics.toPlainText()
    assert "Gremlin" in text
    assert "80%" in text
    assert duo.mob_statistics.summary(duo.clock()) in text
    assert gui.duo.statistics.isReadOnly()
    gui.duo.set_running(True)
    assert gui.duo.statistics.isEnabled()
