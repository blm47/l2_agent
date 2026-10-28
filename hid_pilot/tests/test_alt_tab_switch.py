from unittest.mock import Mock

import pytest

from hid_pilot.controller import Stopped
from hid_pilot.desktop import ActivationDenied, FocusLost
from test_duo import FARMER, SUPPORT, switch_controller


def test_repeated_visits_skip_failed_direct_and_foreground_methods(monkeypatch):
    controller, destination, transport = switch_controller(monkeypatch)
    source = controller.desktop
    direct = Mock(side_effect=ActivationDenied("Отказ"))
    helper = Mock(side_effect=[RuntimeError("foreground отказ"), None, None])
    monkeypatch.setattr("hid_pilot.controller.activate", direct)
    monkeypatch.setattr("hid_pilot.controller.request_activation", helper)
    monkeypatch.setattr("hid_pilot.controller.win32api.GetAsyncKeyState", lambda key: 0)
    controller.switch_window(SUPPORT)
    # Следующий визит по тому же направлению использует уже успешный метод.
    controller.desktop = source
    controller.switch_window(SUPPORT)
    assert direct.call_count == 1
    assert helper.call_count == 3
    assert [call.kwargs for call in helper.call_args_list] == [
        {}, {"strategy": "attached_focus"}, {"strategy": "attached_focus"}]
    transport.reset_session.assert_not_called()
    assert controller.pause_generation == 0
    assert controller.desktop is destination


def test_successful_helper_is_reused_without_failed_direct_request(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    source = controller.desktop
    direct = Mock(side_effect=ActivationDenied("Отказ"))
    helper = Mock()
    monkeypatch.setattr("hid_pilot.controller.activate", direct)
    monkeypatch.setattr("hid_pilot.controller.request_activation", helper)
    controller.switch_window(SUPPORT)
    controller.desktop = source
    controller.switch_window(SUPPORT)
    assert direct.call_count == 1
    assert helper.call_count == 2
    transport.reset_session.assert_not_called()


def test_activation_memory_does_not_cross_pid_changes(monkeypatch):
    controller, _, _ = switch_controller(monkeypatch)
    controller.activation_strategies[(FARMER.hwnd, FARMER.pid, SUPPORT.hwnd, SUPPORT.pid)] = "attached_focus"
    replacement = SUPPORT.model_copy(update={"pid": SUPPORT.pid + 1})
    monkeypatch.setattr("hid_pilot.controller.snapshot", lambda hwnd: replacement)
    direct = Mock()
    helper = Mock()
    monkeypatch.setattr("hid_pilot.controller.activate", direct)
    monkeypatch.setattr("hid_pilot.controller.request_activation", helper)
    controller.switch_window(replacement)
    direct.assert_called_once_with(replacement)
    helper.assert_not_called()


def test_focus_recovery_preserves_com_after_one_activation_failure(monkeypatch):
    controller, destination, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.activate", Mock(side_effect=ActivationDenied("Отказ")))
    helper = Mock(side_effect=[RuntimeError("Отказ")] + [None])
    monkeypatch.setattr("hid_pilot.controller.request_activation", helper)
    controller.stopped.wait = Mock(return_value=False)
    monkeypatch.setattr("hid_pilot.controller.win32api.GetAsyncKeyState", lambda key: 0)
    controller.switch_window(SUPPORT)
    transport.reset_session.assert_not_called()
    transport.window_switch.assert_not_called()
    transport.hold.assert_not_called()
    assert helper.call_count == 2
    assert all(not call.kwargs for call in helper.call_args_list[:1])
    assert helper.call_args.kwargs == {"strategy": "attached_focus"}
    assert controller.desktop is destination
    assert controller.pause_generation == 0
    transport.arm.assert_called_once()


def test_actual_transport_failure_never_arms_or_attempts_focus_recovery(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.activate", Mock(side_effect=ActivationDenied("Отказ")))
    controller.stopped.wait = Mock(return_value=False)
    transport.stop.side_effect = OSError("COM недоступен")
    with pytest.raises(OSError, match="COM недоступен"):
        controller.switch_window(SUPPORT)
    transport.window_switch.assert_not_called()
    transport.hold.assert_not_called()
    transport.arm.assert_not_called()
    assert controller.stopped.is_set()


def test_source_focus_loss_prevents_focus_recovery(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    controller.desktop.check.side_effect = FocusLost("Уход из игры")
    with pytest.raises(FocusLost):
        controller._recover_focus(SUPPORT, .75)
    transport.reset_session.assert_not_called()


def test_stop_during_release_prevents_reactivation_and_arm(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    transport.release.side_effect = controller.stopped.set
    activate = Mock()
    monkeypatch.setattr("hid_pilot.controller.activate", activate)
    with pytest.raises(Stopped):
        controller._recover_focus(SUPPORT, .75)
    activate.assert_not_called()
    transport.arm.assert_not_called()
    transport.window_switch.assert_not_called()


def alt_tab_controller(monkeypatch):
    controller, destination, transport = switch_controller(monkeypatch)
    transport.supports_alt_tab = True
    monkeypatch.setattr("hid_pilot.controller.activate", Mock(side_effect=ActivationDenied("Отказ")))
    monkeypatch.setattr("hid_pilot.controller.request_activation", Mock(side_effect=RuntimeError("Отказ")))
    monkeypatch.setattr("hid_pilot.controller.win32api.GetAsyncKeyState", lambda key: 0)
    controller.stopped.wait = Mock(return_value=False)
    now = [0.]

    def clock():
        now[0] += .02
        return now[0]

    monkeypatch.setattr("hid_pilot.controller.time.monotonic", clock)
    return controller, destination, transport


def test_last_resort_alt_tab_releases_and_waits_before_arm(monkeypatch):
    controller, _, transport = alt_tab_controller(monkeypatch)
    events = []
    transport.window_switch.side_effect = lambda: events.append("switch")
    transport.release.side_effect = lambda: events.append("release")
    transport.arm.side_effect = lambda: events.append("arm")
    controller.stopped.wait.side_effect = lambda seconds: events.append(seconds) or False
    controller.switch_window(SUPPORT)
    transport.reset_session.assert_not_called()
    transport.window_switch.assert_called_once()
    transport.hold.assert_not_called()
    switch = events.index("switch")
    assert events[switch:] == ["switch", .08, "release", "release", .2, "release", "arm"]


@pytest.mark.parametrize("alt_key", [0x12, 0xA4, 0xA5])
def test_alt_tab_stuck_modifier_blocks_arm_and_farm(monkeypatch, alt_key):
    controller, _, transport = alt_tab_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.win32api.GetAsyncKeyState",
                        lambda key: 0x8000 if transport.window_switch.called and key == alt_key else 0)
    with pytest.raises(RuntimeError, match="ALT остаётся зажатым"):
        controller.switch_window(SUPPORT)
    transport.arm.assert_not_called()
    transport.hold.assert_not_called()
    assert controller.stopped.is_set()


def test_late_alt_after_settle_delay_blocks_arm(monkeypatch):
    controller, _, transport = alt_tab_controller(monkeypatch)
    delayed = [False]

    def wait(seconds):
        if seconds == .2:
            delayed[0] = True
        return False

    controller.stopped.wait.side_effect = wait
    monkeypatch.setattr("hid_pilot.controller.win32api.GetAsyncKeyState",
                        lambda key: 0x8000 if delayed[0] else 0)
    with pytest.raises(RuntimeError, match="ALT остаётся зажатым"):
        controller.switch_window(SUPPORT)
    transport.arm.assert_not_called()
    transport.hold.assert_not_called()


def test_switch_error_still_releases_keys_and_blocks_arm(monkeypatch):
    controller, _, transport = alt_tab_controller(monkeypatch)
    events = []

    def failed_switch():
        events.append("switch")
        raise OSError("SWITCH timeout")

    transport.window_switch.side_effect = failed_switch
    transport.release.side_effect = lambda: events.append("release")
    with pytest.raises(OSError, match="SWITCH timeout"):
        controller.switch_window(SUPPORT)
    assert events[-2:] == ["switch", "release"]
    transport.arm.assert_not_called()


def test_release_failure_after_alt_tab_blocks_arm_and_farm(monkeypatch):
    controller, _, transport = alt_tab_controller(monkeypatch)

    def release():
        if transport.window_switch.called:
            raise OSError("RELEASE timeout")

    transport.release.side_effect = release
    with pytest.raises(OSError, match="RELEASE timeout"):
        controller.switch_window(SUPPORT)
    transport.arm.assert_not_called()
    transport.hold.assert_not_called()
    assert controller.stopped.is_set()


def test_alt_tab_wrong_window_never_arms(monkeypatch):
    controller, destination, transport = alt_tab_controller(monkeypatch)
    destination.check.side_effect = FocusLost("Другое окно")
    with pytest.raises(RuntimeError, match="ALT\\+TAB не выбрал"):
        controller.switch_window(SUPPORT)
    transport.arm.assert_not_called()
    transport.hold.assert_not_called()


def test_stop_during_alt_tab_still_releases_without_arm(monkeypatch):
    controller, _, transport = alt_tab_controller(monkeypatch)
    transport.window_switch.side_effect = controller.stopped.set
    controller.stopped.wait.side_effect = lambda seconds: controller.stopped.is_set()
    with pytest.raises(Stopped):
        controller.switch_window(SUPPORT)
    transport.release.assert_called()
    transport.arm.assert_not_called()
    transport.hold.assert_not_called()
