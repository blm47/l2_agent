from unittest.mock import Mock

import pytest

from hid_pilot.controller import Stopped
from hid_pilot.desktop import ActivationDenied, FocusLost
from test_duo import SUPPORT, switch_controller


def test_session_reset_replaces_alt_tab_after_three_activation_failures(monkeypatch):
    controller, destination, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.activate", Mock(side_effect=ActivationDenied("Отказ")))
    helper = Mock(side_effect=[RuntimeError("Отказ")] * 3 + [None])
    monkeypatch.setattr("hid_pilot.controller.request_activation", helper)
    controller.stopped.wait = Mock(return_value=False)
    monkeypatch.setattr("hid_pilot.controller.win32api.GetAsyncKeyState", lambda key: 0)
    controller.switch_window(SUPPORT)
    transport.reset_session.assert_called_once()
    transport.window_switch.assert_not_called()
    transport.hold.assert_not_called()
    assert helper.call_count == 4
    assert all(not call.kwargs for call in helper.call_args_list)
    assert controller.desktop is destination
    assert controller.pause_generation == 1
    transport.arm.assert_called_once()


def test_failed_session_reset_never_arms_or_sends_alt_tab(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.activate", Mock(side_effect=ActivationDenied("Отказ")))
    controller.stopped.wait = Mock(return_value=False)
    transport.reset_session.side_effect = OSError("COM недоступен")
    with pytest.raises(OSError, match="COM недоступен"):
        controller.switch_window(SUPPORT)
    transport.window_switch.assert_not_called()
    transport.hold.assert_not_called()
    transport.arm.assert_not_called()
    assert controller.stopped.is_set()


def test_source_focus_loss_prevents_session_reset(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    controller.desktop.check.side_effect = FocusLost("Уход из игры")
    with pytest.raises(FocusLost):
        controller._reset_and_activate(SUPPORT, .75)
    transport.reset_session.assert_not_called()


def test_stop_during_reset_prevents_reactivation_and_arm(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    transport.reset_session.side_effect = controller.stopped.set
    activate = Mock()
    monkeypatch.setattr("hid_pilot.controller.activate", activate)
    with pytest.raises(Stopped):
        controller._reset_and_activate(SUPPORT, .75)
    activate.assert_not_called()
    transport.arm.assert_not_called()
    transport.window_switch.assert_not_called()
