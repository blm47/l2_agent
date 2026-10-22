import json
import subprocess
import threading
from unittest.mock import Mock

import pytest

from hid_pilot.desktop import Target
from hid_pilot.window_activation import _activate_native, request_activation
from hid_pilot.window_activation import helper_main


SOURCE = Target(hwnd=1, pid=11, title="Фармер", rect=(0, 0, 100, 100))
TARGET = Target(hwnd=2, pid=22, title="Бафер", rect=(100, 0, 200, 100))


def payload():
    return json.loads(json.dumps({"source": SOURCE.model_dump(), "target": TARGET.model_dump()}))


def native_api(monkeypatch):
    state = {"foreground": 1, "pid": 22}
    user, kernel = Mock(), Mock()
    user.IsWindow.return_value = True
    user.IsWindowVisible.return_value = True
    user.IsIconic.return_value = False
    user.GetForegroundWindow.side_effect = lambda: state["foreground"]

    def identity(hwnd, pid):
        pid._obj.value = 11 if hwnd == 1 else state["pid"]
        return hwnd * 100

    def rect(hwnd, result):
        result._obj.right, result._obj.bottom = 100, 100
        return True

    def origin(hwnd, result):
        result._obj.x = 0 if hwnd == 1 else 100
        return True

    def foreground(hwnd):
        if user.SetForegroundWindow.call_count == 1:
            return False
        state["foreground"] = hwnd
        return True

    user.GetWindowThreadProcessId.side_effect = identity
    user.GetClientRect.side_effect = rect
    user.ClientToScreen.side_effect = origin
    user.SetForegroundWindow.side_effect = foreground
    user.AttachThreadInput.return_value = True
    kernel.GetCurrentThreadId.return_value = 300
    monkeypatch.setattr("hid_pilot.window_activation.ctypes.WinDLL",
                        lambda name, **kwargs: user if name == "user32" else kernel)
    return user, state


def test_helper_attaches_and_always_detaches_input_queue(monkeypatch):
    user, state = native_api(monkeypatch)
    result = _activate_native(payload())
    assert result["focused"]
    assert result["method"] == "attached_queue"
    assert state["foreground"] == 2
    assert [call.args for call in user.AttachThreadInput.call_args_list] == [(300, 100, True), (300, 100, False)]


def test_task_switch_strategy_uses_different_native_api(monkeypatch):
    user, state = native_api(monkeypatch)
    user.SwitchToThisWindow.side_effect = lambda hwnd, mode: state.update(foreground=hwnd)
    data = payload()
    data["strategy"] = "task_switch"
    result = _activate_native(data)
    assert result == {"focused": True, "method": "task_switch"}
    user.SwitchToThisWindow.assert_called_once_with(2, True)
    user.SetForegroundWindow.assert_not_called()
    user.AttachThreadInput.assert_not_called()


def test_task_switch_cannot_claim_success_without_foreground(monkeypatch):
    user, _ = native_api(monkeypatch)
    monkeypatch.setattr("hid_pilot.window_activation.time.monotonic", Mock(side_effect=[0., 1.]))
    data = payload()
    data["strategy"] = "task_switch"
    result = _activate_native(data)
    assert not result["focused"]
    assert "error" in result


def test_task_switch_aborts_if_user_changed_foreground(monkeypatch):
    user, state = native_api(monkeypatch)
    state["foreground"] = 3
    data = payload()
    data["strategy"] = "task_switch"
    with pytest.raises(RuntimeError, match="Пользователь сменил"):
        _activate_native(data)
    user.SwitchToThisWindow.assert_not_called()


def test_helper_rejects_changed_source_without_focus_request(monkeypatch):
    user, state = native_api(monkeypatch)
    state["foreground"] = 3
    with pytest.raises(RuntimeError, match="Пользователь сменил"):
        _activate_native(payload())
    user.SetForegroundWindow.assert_not_called()
    user.AttachThreadInput.assert_not_called()


def test_helper_rejects_reused_target_handle(monkeypatch):
    user, state = native_api(monkeypatch)
    state["pid"] = 33
    with pytest.raises(RuntimeError, match="сменило процесс"):
        _activate_native(payload())
    user.SetForegroundWindow.assert_not_called()


def test_helper_detaches_on_error_after_attach(monkeypatch):
    user, _ = native_api(monkeypatch)
    user.SetForegroundWindow.side_effect = [False, OSError("Win32 failure")]
    with pytest.raises(OSError):
        _activate_native(payload())
    assert user.AttachThreadInput.call_args.args == (300, 100, False)


def test_successful_child_is_hidden_and_sends_no_keyboard_events(monkeypatch):
    process = Mock(returncode=0)
    process.poll.return_value = 0
    process.communicate.return_value = (b'{"focused": true, "method": "attached_queue"}', b'')
    launch = Mock(return_value=process)
    monkeypatch.setattr("hid_pilot.window_activation.subprocess.Popen", launch)
    request_activation(SOURCE, TARGET, threading.Event(), lambda: True, .75)
    assert "--activate-window" in launch.call_args.args[0]
    assert launch.call_args.kwargs["creationflags"] != 0
    process.kill.assert_not_called()


@pytest.mark.parametrize("cancel", [True, False])
def test_hung_child_is_killed_on_stop_or_timeout(monkeypatch, cancel):
    process = Mock(returncode=None)
    process.poll.side_effect = lambda: process.returncode
    process.kill.side_effect = lambda: setattr(process, "returncode", -1)
    process.communicate.return_value = (b'', b'')
    monkeypatch.setattr("hid_pilot.window_activation.subprocess.Popen", Mock(return_value=process))
    stopped = threading.Event()
    if cancel:
        stopped.set()
    with pytest.raises(RuntimeError, match="отменено|не завершила"):
        request_activation(SOURCE, TARGET, stopped, lambda: True, 0)
    process.kill.assert_called_once_with()


def test_rejected_child_does_not_report_success(monkeypatch):
    process = Mock(returncode=1)
    process.poll.return_value = 1
    process.communicate.return_value = (b'{"focused": false, "error": "source changed"}', b'')
    monkeypatch.setattr("hid_pilot.window_activation.subprocess.Popen", Mock(return_value=process))
    with pytest.raises(RuntimeError, match="source changed"):
        request_activation(SOURCE, TARGET, threading.Event(), lambda: True, .75)


def test_consoleless_helper_can_report_verified_focus_by_exit_code(monkeypatch):
    process = Mock(returncode=0)
    process.poll.return_value = 0
    process.communicate.return_value = (b'', b'')
    monkeypatch.setattr("hid_pilot.window_activation.subprocess.Popen", Mock(return_value=process))
    request_activation(SOURCE, TARGET, threading.Event(), lambda: True, .75)


def test_successful_exit_with_delayed_pipe_can_be_verified_by_controller(monkeypatch):
    process = Mock(returncode=0)
    process.poll.return_value = 0
    process.communicate.side_effect = subprocess.TimeoutExpired("helper", .1)
    monkeypatch.setattr("hid_pilot.window_activation.subprocess.Popen", Mock(return_value=process))
    request_activation(SOURCE, TARGET, threading.Event(), lambda: True, .75)
    process.kill.assert_not_called()


def test_failed_exit_pipe_timeout_becomes_retryable_runtime_error(monkeypatch):
    process = Mock(returncode=1)
    process.poll.return_value = 1
    process.communicate.side_effect = subprocess.TimeoutExpired("helper", .1)
    monkeypatch.setattr("hid_pilot.window_activation.subprocess.Popen", Mock(return_value=process))
    with pytest.raises(RuntimeError, match="завершился с ошибкой"):
        request_activation(SOURCE, TARGET, threading.Event(), lambda: True, .75)


def test_cleanup_pipe_timeout_does_not_mask_stop(monkeypatch):
    process = Mock(returncode=None)
    process.poll.side_effect = lambda: process.returncode
    process.kill.side_effect = lambda: setattr(process, "returncode", -1)
    process.communicate.side_effect = subprocess.TimeoutExpired("helper", .5)
    monkeypatch.setattr("hid_pilot.window_activation.subprocess.Popen", Mock(return_value=process))
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(RuntimeError, match="отменено"):
        request_activation(SOURCE, TARGET, stopped, lambda: True, .75)
    process.kill.assert_called_once()


def test_delayed_success_reply_never_arms_unfocused_destination(monkeypatch):
    from hid_pilot.desktop import ActivationDenied, FocusLost
    from test_duo import SUPPORT, switch_controller

    controller, destination, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.activate", Mock(side_effect=ActivationDenied("Отказ")))
    monkeypatch.setattr("hid_pilot.controller.request_activation", request_activation)
    process = Mock(returncode=0)
    process.poll.return_value = 0
    process.communicate.side_effect = subprocess.TimeoutExpired("helper", .1)
    monkeypatch.setattr("hid_pilot.window_activation.subprocess.Popen", Mock(return_value=process))
    now = [0.]

    def monotonic():
        now[0] += .2
        return now[0]

    monkeypatch.setattr("hid_pilot.controller.time.monotonic", monotonic)
    controller.stopped.wait = Mock(return_value=False)
    destination.check.side_effect = FocusLost("Окно не активно")
    with pytest.raises(RuntimeError, match="подтвердить фокус"):
        controller.switch_window(SUPPORT)
    transport.arm.assert_not_called()
    transport.hold.assert_not_called()


def test_windowed_helper_does_not_require_stdout(monkeypatch):
    monkeypatch.setattr("hid_pilot.window_activation._activate_native", Mock(return_value={"focused": True}))
    monkeypatch.setattr("hid_pilot.window_activation.sys.stdout", None)
    assert helper_main(["--activate-window", json.dumps(payload())]) == 0


def test_frozen_dispatch_reuses_executable_without_starting_gui(monkeypatch):
    process = Mock(returncode=0)
    process.poll.return_value = 0
    process.communicate.return_value = (b'{"focused": true}', b'')
    launch = Mock(return_value=process)
    monkeypatch.setattr("hid_pilot.window_activation.subprocess.Popen", launch)
    monkeypatch.setattr("hid_pilot.window_activation.sys.frozen", True, raising=False)
    request_activation(SOURCE, TARGET, threading.Event(), lambda: True, .75)
    assert launch.call_args.args[0][1] == "--activate-window"
