import threading
from unittest.mock import Mock

import pytest

from hid_pilot.controller import ActionController, Stopped
from hid_pilot.desktop import FocusLost, Target
from hid_pilot.keyboard_layout import ensure_english_layout


TARGET = Target(hwnd=101, pid=201, title="МА", rect=(0, 0, 800, 600))
SUPPORT = TARGET.model_copy(update={"hwnd": 102, "pid": 202, "title": "Саппорт"})


@pytest.fixture
def layout_api(monkeypatch):
    layout = Mock(return_value=0x04190419)
    post = Mock()
    monkeypatch.setattr("hid_pilot.keyboard_layout.snapshot", lambda hwnd: TARGET)
    monkeypatch.setattr("hid_pilot.keyboard_layout.win32process.GetWindowThreadProcessId",
                        Mock(return_value=(301, TARGET.pid)))
    monkeypatch.setattr("hid_pilot.keyboard_layout.win32api.GetKeyboardLayout", layout)
    monkeypatch.setattr("hid_pilot.keyboard_layout.win32api.GetKeyboardLayoutList",
                        lambda: (0x04190419, 0x04090409))
    monkeypatch.setattr("hid_pilot.keyboard_layout.win32gui.PostMessage", post)
    return layout, post


@pytest.mark.parametrize("english", [0x04090409, 0x08090809])
def test_already_english_sends_no_change_request(layout_api, english):
    layout, post = layout_api
    layout.return_value = english
    ensure_english_layout(TARGET, threading.Event(), Mock())
    layout.assert_called_with(301)
    post.assert_not_called()


def test_russian_changes_to_installed_english_and_waits_for_confirmation(layout_api):
    layout, post = layout_api
    layout.side_effect = [0x04190419, 0x04190419, 0x04090409]
    stopped = Mock()
    ensure_english_layout(TARGET, stopped, Mock())
    post.assert_called_once_with(101, 0x0050, 0, 0x04090409)
    stopped.wait.assert_called_once_with(.01)


def test_rejected_layout_change_blocks_start(layout_api):
    _, post = layout_api
    with pytest.raises(RuntimeError, match="не подтвердило ENG"):
        ensure_english_layout(TARGET, threading.Event(), Mock(), timeout=0)
    post.assert_called_once()


def test_missing_english_fails_without_keyboard_shortcuts(layout_api, monkeypatch):
    _, post = layout_api
    monkeypatch.setattr("hid_pilot.keyboard_layout.win32api.GetKeyboardLayoutList", lambda: (0x04190419,))
    with pytest.raises(RuntimeError, match="не установлена"):
        ensure_english_layout(TARGET, threading.Event(), Mock())
    post.assert_not_called()


def test_reused_window_handle_prevents_layout_request(layout_api, monkeypatch):
    _, post = layout_api
    monkeypatch.setattr("hid_pilot.keyboard_layout.snapshot", lambda hwnd: TARGET.model_copy(update={"pid": 999}))
    with pytest.raises(RuntimeError, match="Окно изменилось"):
        ensure_english_layout(TARGET, threading.Event(), Mock())
    post.assert_not_called()


def test_layout_wait_checks_stop_before_accepting_success(layout_api):
    layout, _ = layout_api
    layout.side_effect = [0x04190419, 0x04090409]
    verify = Mock(side_effect=[None, None, Stopped("STOP")])
    with pytest.raises(Stopped):
        ensure_english_layout(TARGET, threading.Event(), verify)


def test_controller_checks_both_windows_while_disarmed(monkeypatch):
    transport = Mock()
    controller = ActionController(Mock(), transport, lambda: True)
    events = []
    transport.stop.side_effect = lambda: events.append("stop")
    helper = Mock(side_effect=lambda target, stopped, verify: (verify(), events.append(target.hwnd)))
    monkeypatch.setattr("hid_pilot.controller.ensure_english_layout", helper)
    controller.prepare_keyboard((TARGET, SUPPORT))
    assert events == ["stop", 101, 102]
    transport.arm.assert_not_called()
    transport.hold.assert_not_called()


@pytest.mark.parametrize("failure", [RuntimeError("Нет ENG"), FocusLost("Уход из игры"), Stopped("STOP")])
def test_preparation_failure_blocks_future_arm(monkeypatch, failure):
    transport = Mock()
    controller = ActionController(Mock(), transport, lambda: True)
    monkeypatch.setattr("hid_pilot.controller.ensure_english_layout", Mock(side_effect=failure))
    with pytest.raises(type(failure)):
        controller.prepare_keyboard((TARGET, SUPPORT))
    with pytest.raises(Stopped):
        controller.start()
    transport.arm.assert_not_called()
    transport.hold.assert_not_called()


def test_manual_pause_during_preparation_prevents_layout_change(monkeypatch):
    controller = ActionController(Mock(), Mock(), lambda: True)
    controller.manual_paused.set()
    monkeypatch.setattr("hid_pilot.controller.ensure_english_layout",
                        lambda target, stopped, verify: verify())
    with pytest.raises(Stopped, match="ручной паузой"):
        controller.prepare_keyboard((TARGET,))
    controller.transport.arm.assert_not_called()
