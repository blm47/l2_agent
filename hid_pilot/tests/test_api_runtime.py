import threading
import time
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.api_controller import ApiActionController
from hid_pilot.api_runtime import AlignmentRequest, ApiDuoFarmer, CameraRequest, PilotApiRuntime
from hid_pilot.controller import ActionController
from hid_pilot.desktop import Target
from hid_pilot.duo import DuoFarmer, parse_duo_settings

FARMER = Target(hwnd=1, pid=11, title="MA", rect=(0, 0, 1000, 800))
SUPPORT = Target(hwnd=2, pid=22, title="Support", rect=(1000, 0, 2000, 800))


def controller():
    desktop = Mock()
    desktop.target = FARMER
    desktop.cursor.return_value = (500, 400)
    result = ApiActionController(desktop, Mock(), lambda: True)
    result.transport.supports_camera_drag = True
    result.transport.supports_fast_camera = True
    result.transport.supports_strong_camera = True
    result.transport.command.side_effect = lambda command, expected: (
        "DONE" if command == "CAMERA_STATUS" else None)
    result._begin_hold = Mock()
    return result


def runtime(tmp_path):
    result = PilotApiRuntime(tmp_path, tmp_path, FARMER, SUPPORT, "FAKE", lambda: True)
    result.session_id = "a" * 32
    result.target_epoch = 1
    result.attack_at = time.monotonic()
    result.observation({"target_kind": "monster", "target_hp": 1, "critical": False})
    result.last_frame = {"frame_id": 1, "target_epoch": 1, "timestamp": time.monotonic()}
    result.controller = controller()
    return result


def request(**kwargs):
    return CameraRequest.model_validate({"session_id": "a" * 32, "frame_id": 1,
                                         "target_epoch": 1, "direction": "left"} | kwargs)


def test_three_forks_preserve_all_farm_settings():
    root = Path(__file__).resolve().parents[2]
    forks = list((root / "agent_scripts/duo").glob("*.duo.pilot"))
    assert len(forks) == 3
    for fork in forks:
        text = fork.read_text(encoding="utf-8")
        assert text.startswith("# agent-input-policy: clicks-only\n")
        original = root / "hid_pilot/extensions/scripts" / fork.name
        actual = parse_duo_settings(text)
        expected = parse_duo_settings(original.read_text(encoding="utf-8"))
        if fork.name == "farmer-healer_SINGL.duo.pilot":
            # Форк сбора сохраняет прежний ограниченный бюджет сессии.
            assert (actual.cycles, actual.max_hours, actual.max_seconds) == (1000, 3., 10800.)
        else:
            assert (actual.cycles, actual.max_hours, actual.max_seconds) == (
                expected.cycles, expected.max_hours, expected.max_seconds)
        assert actual.model_dump(exclude={"cycles", "max_hours", "max_seconds"}) == expected.model_dump(
            exclude={"cycles", "max_hours", "max_seconds"})
    for name in ("combat_key", "fight", "acquire", "run", "search_cycle", "visit"):
        assert getattr(ApiDuoFarmer, name) is getattr(DuoFarmer, name)


def test_farm_move_is_blocked_but_coordinate_click_can_navigate(monkeypatch):
    item = controller()
    move = Mock()
    monkeypatch.setattr(ActionController, "move", move)
    monkeypatch.setattr(ActionController, "click", lambda self, button, x, y, **kw: self.move(x, y))
    with pytest.raises(ValueError):
        item.move(.5, .5)
    item.click("left", .5, .5)
    move.assert_called_once()
    with pytest.raises(ValueError):
        item.move(.5, .5)


def test_camera_always_releases_right_button_on_error(monkeypatch):
    item = controller()
    monkeypatch.setattr(ActionController, "move", lambda *args, **kw: None)
    item.transport.command.side_effect = lambda command, expected: (
        None if command == "HB" else (_ for _ in ()).throw(OSError("Нет связи")))
    with pytest.raises(OSError):
        item.camera_turn("left", lambda: None)
    assert item.transport.release.call_count == 2
    item._begin_hold.assert_called_once_with("BUTTON", "right", 300)


def test_legacy_firmware_rejects_camera_before_any_input():
    item = controller()
    item.transport.supports_fast_camera = False
    with pytest.raises(ValueError, match="V7"):
        item.camera_turn("left", lambda: None)
    item.transport.command.assert_not_called()
    item.transport.release.assert_not_called()
    item._begin_hold.assert_not_called()


def test_fine_camera_turn_scales_hid_movement(monkeypatch):
    item = controller()
    monkeypatch.setattr(ActionController, "move", lambda *args, **kw: None)
    item.camera_turn("left", lambda: None, scale=.25)
    assert [call.args for call in item.transport.command.call_args_list] == [
        ("HB", "ALIVE"), ("MOVE -8 0", "MOVED")]
    item.transport.release.assert_called()


@pytest.mark.parametrize("scale", [0, .01, 2, float("nan"), float("inf")])
def test_invalid_camera_scale_sends_no_input(scale):
    item = controller()
    with pytest.raises(ValueError):
        item.camera_turn("left", lambda: None, scale=scale)
    item.transport.command.assert_not_called()


def test_camera_zoom_uses_shared_controller_and_checks_capability(monkeypatch):
    item = controller()
    item.transport.supports_camera_zoom = False
    with pytest.raises(ValueError, match="V8"):
        item.camera_zoom(1)
    item.transport.command.assert_not_called()
    item.transport.supports_camera_zoom = True
    monkeypatch.setattr(ActionController, "move", lambda *args, **kw: None)
    item.camera_zoom(-1)
    item.transport.command.assert_called_once_with("WHEEL -1", "SCROLLED")


def test_camera_does_not_interrupt_held_key(monkeypatch):
    item = controller()
    down, release_key, camera = threading.Event(), threading.Event(), threading.Event()
    events = []

    def hold(self, kind, key, duration):
        down.set()
        assert release_key.wait(2)
        events.append("up")

    monkeypatch.setattr(ActionController, "_hold", hold)
    monkeypatch.setattr(ActionController, "move", lambda *args, **kw: None)
    item._begin_hold.side_effect = lambda *args: (events.append("camera"), camera.set())
    key_thread = threading.Thread(target=lambda: item.key("W", 100))
    turn_thread = threading.Thread(target=lambda: item.camera_turn("left", lambda: None))
    key_thread.start()
    assert down.wait(1)
    turn_thread.start()
    assert not camera.wait(.03)
    release_key.set()
    key_thread.join(2)
    turn_thread.join(2)
    assert events == ["up", "camera"]


@pytest.mark.parametrize("change", ["stale", "epoch", "critical", "player", "support", "no_attack"])
def test_server_rejects_unsafe_camera_before_input(tmp_path, change):
    api = runtime(tmp_path)
    api.controller.camera_turn = Mock()
    if change == "stale":
        api.last_frame["timestamp"] = 0
    elif change == "epoch":
        api.target_epoch = 2
    elif change == "critical":
        api.observed["critical"] = True
    elif change == "player":
        api.observed["target_kind"] = "player"
    elif change == "support":
        api.controller.desktop.target = SUPPORT
    else:
        api.attack_at = None
    with pytest.raises(ValueError):
        api.camera(request())
    api.controller.camera_turn.assert_not_called()


def test_camera_replays_and_disconnect_lease_are_blocked(tmp_path):
    api = runtime(tmp_path)
    api.controller.camera_turn = Mock(side_effect=lambda direction, verify, **kwargs: verify())
    api.camera(request())
    with pytest.raises(ValueError, match="повтор"):
        api.camera(request())
    api.phase = "running"
    api.heartbeat_at = time.monotonic() - 6
    api.check_lease()
    assert api.cancel.is_set() and api.controller.stopped.is_set()
    api.controller.transport.stop.assert_called_once()


def test_camera_allows_unknown_identity_after_farm_attack_but_preserves_hp_checks(tmp_path):
    api = runtime(tmp_path)
    api.observed["target_kind"] = "unknown"
    api.controller.camera_turn = Mock(side_effect=lambda direction, verify, **kwargs: verify())
    api.camera(request())
    api.controller.camera_turn.assert_called_once()
    api.observed["target_hp"] = None
    with pytest.raises(ValueError, match="HP"):
        api.camera(request())


def test_camera_waits_briefly_for_existing_farm_operation(tmp_path):
    api = runtime(tmp_path)
    holding = threading.Event()

    def farm_operation():
        with api.controller.operations:
            holding.set()
            time.sleep(.06)

    worker = threading.Thread(target=farm_operation)
    worker.start()
    assert holding.wait(1)
    api.controller.camera_turn = Mock(side_effect=lambda direction, verify, **kwargs: verify())
    try:
        api.camera(request())
    finally:
        worker.join(1)
    api.controller.camera_turn.assert_called_once()


def test_alignment_has_no_target_requirement_and_runs_at_reduced_speed(tmp_path):
    api = runtime(tmp_path)
    api.attack_at = None
    api.observed["target_hp"] = None
    api.controller.camera_turn = Mock(side_effect=lambda op, verify, **kw: verify())
    value = AlignmentRequest(session_id=api.session_id, frame_id=1, target_epoch=1,
                             operation="up", fine=True)
    api.align_camera(value)
    assert api.controller.camera_turn.call_args.kwargs == {"scale": .5}
    with pytest.raises(ValueError, match="повтор"):
        api.align_camera(value)


@pytest.mark.parametrize("change", ["support", "critical", "stale", "pause", "stop", "epoch"])
def test_alignment_blocks_unsafe_frames_before_input(tmp_path, change):
    api = runtime(tmp_path)
    api.controller.camera_turn = Mock()
    if change == "support":
        api.controller.desktop.target = SUPPORT
    elif change == "critical":
        api.observed["critical"] = True
    elif change == "stale":
        api.last_frame["timestamp"] = 0
    elif change == "pause":
        api.controller.manual_paused.set()
    elif change == "stop":
        api.controller.stopped.set()
    else:
        api.target_epoch = 2
    value = AlignmentRequest(session_id=api.session_id, frame_id=1, target_epoch=1, operation="left")
    with pytest.raises(ValueError):
        api.align_camera(value)
    api.controller.camera_turn.assert_not_called()


def test_capture_discards_epoch_changed_during_capture(tmp_path):
    api = runtime(tmp_path)

    def capture():
        api.selection()
        return np.zeros((800, 1000, 3), dtype=np.uint8)

    api.controller.desktop.capture.side_effect = capture
    with pytest.raises(ValueError, match="изменилось"):
        api.frame()


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("offparty", [False, True])
def test_api_lifecycle_prepares_before_arm_and_closes_after_engine_failure(tmp_path, monkeypatch, failure, offparty):
    import hid_pilot.api_runtime as module

    scripts = Path(__file__).resolve().parents[2] / "agent_scripts/duo"
    hardware = Mock()
    hardware.desktop.target = FARMER
    hardware.paused = threading.Event()
    hardware.manual_paused = threading.Event()
    hardware.stopped = threading.Event()
    events = []
    transport = Mock()
    factory = Mock(side_effect=lambda port: (events.append("transport"), transport)[1])
    engine = Mock()
    if failure:
        engine.run.side_effect = ValueError("Ошибка наблюдения")
    monkeypatch.setattr(module, "snapshot", lambda hwnd: FARMER if hwnd == 1 else SUPPORT)
    monkeypatch.setattr(PilotApiRuntime, "_activate_farmer", lambda self, target: target)
    monkeypatch.setattr(module, "prepare_duo", lambda *args: events.append("profiles"))
    party_loader = Mock(return_value=None)
    monkeypatch.setattr(module, "load_duo_party", party_loader)
    parser = module.parse_duo_settings
    monkeypatch.setattr(module, "parse_duo_settings",
                        lambda text: parser(text).model_copy(update={"offparty_support": offparty}))
    monkeypatch.setattr(module, "ApiDuoFarmer", Mock(return_value=engine))
    api = PilotApiRuntime(scripts, tmp_path, FARMER, SUPPORT, "FAKE", lambda: True,
                          transport_factory=factory, controller_factory=lambda *args: hardware)
    api.start("farmer-healer.duo")
    api.worker.join(2)
    assert not api.worker.is_alive()
    assert events == ["profiles", "transport"]
    hardware.prepare_keyboard.assert_called_once_with((FARMER, SUPPORT))
    hardware.start.assert_called_once()
    hardware.close.assert_called_once()
    engine.close_observation.assert_called_once()
    assert api.phase == ("failed" if failure else "stopped")
    assert party_loader.call_count == 1


def test_prepare_failure_never_opens_com_port(tmp_path, monkeypatch):
    import hid_pilot.api_runtime as module

    scripts = Path(__file__).resolve().parents[2] / "agent_scripts/duo"
    factory = Mock()
    monkeypatch.setattr(module, "snapshot", lambda hwnd: FARMER if hwnd == 1 else SUPPORT)
    monkeypatch.setattr(module, "prepare_duo", Mock(side_effect=ValueError("Нет ROI")))
    api = PilotApiRuntime(scripts, tmp_path, FARMER, SUPPORT, "FAKE", lambda: True,
                          transport_factory=factory)
    api.start("farmer-healer.duo")
    api.worker.join(2)
    factory.assert_not_called()
    assert api.phase == "failed"


@pytest.mark.parametrize("cancelled", [False, True])
def test_initial_activation_uses_bounded_helper_before_connecting_pico(tmp_path, monkeypatch, cancelled):
    import hid_pilot.api_runtime as module
    from hid_pilot.controller import Stopped
    from hid_pilot.desktop import ActivationDenied

    api = runtime(tmp_path)
    source = Target(hwnd=3, pid=33, title="Terminal", rect=(0, 0, 900, 700))
    monkeypatch.setattr(module.win32gui, "GetForegroundWindow", lambda: source.hwnd)
    monkeypatch.setattr(module, "activate", Mock(side_effect=ActivationDenied("Отказ Windows")))
    monkeypatch.setattr(module, "snapshot", lambda hwnd: source if hwnd == 3 else FARMER)
    desktop = Mock()
    monkeypatch.setattr(module, "Desktop", Mock(return_value=desktop))
    helper = Mock(side_effect=lambda *args: api.cancel.set() if cancelled else None)
    monkeypatch.setattr(module, "request_activation", helper)
    if cancelled:
        with pytest.raises(Stopped):
            api._activate_farmer(FARMER)
    else:
        assert api._activate_farmer(FARMER) == FARMER
    helper.assert_called_once_with(source, FARMER, api.cancel, api.hard_stop_ready, .75)
    desktop.check.assert_called_once()


def test_failed_activation_helper_never_opens_pico(tmp_path, monkeypatch):
    import hid_pilot.api_runtime as module
    from hid_pilot.desktop import ActivationDenied

    scripts = Path(__file__).resolve().parents[2] / "agent_scripts/duo"
    factory = Mock()
    monkeypatch.setattr(module, "snapshot", lambda hwnd: FARMER if hwnd == 1 else SUPPORT)
    monkeypatch.setattr(module, "prepare_duo", lambda *args: None)
    monkeypatch.setattr(module, "load_duo_party", lambda *args: None)
    monkeypatch.setattr(module.win32gui, "GetForegroundWindow", lambda: SUPPORT.hwnd)
    monkeypatch.setattr(module, "activate", Mock(side_effect=ActivationDenied("Отказ Windows")))
    monkeypatch.setattr(module, "request_activation", Mock(side_effect=RuntimeError("Helper timeout")))
    api = PilotApiRuntime(scripts, tmp_path, FARMER, SUPPORT, "FAKE", lambda: True,
                          transport_factory=factory)
    api.start("farmer-healer.duo")
    api.worker.join(2)
    factory.assert_not_called()
    assert api.phase == "failed" and api.error == "Helper timeout"


def test_initial_activation_waits_for_delayed_foreground_without_helper(tmp_path, monkeypatch):
    import hid_pilot.api_runtime as module
    from hid_pilot.desktop import FocusLost

    api = runtime(tmp_path)
    monkeypatch.setattr(module.win32gui, "GetForegroundWindow", lambda: SUPPORT.hwnd)
    monkeypatch.setattr(module, "activate", lambda target: target)
    desktop = Mock()
    desktop.check.side_effect = [FocusLost("Переход foreground"), None]
    monkeypatch.setattr(module, "Desktop", Mock(return_value=desktop))
    helper = Mock()
    monkeypatch.setattr(module, "request_activation", helper)
    assert api._activate_farmer(FARMER) == FARMER
    assert desktop.check.call_count == 2
    helper.assert_not_called()


@pytest.mark.parametrize("success_on", ["attached_focus", "task_switch"])
def test_initial_activation_tries_existing_fallback_strategies(tmp_path, monkeypatch, success_on):
    import hid_pilot.api_runtime as module
    from hid_pilot.desktop import ActivationDenied

    api = runtime(tmp_path)
    monkeypatch.setattr(module.win32gui, "GetForegroundWindow", lambda: SUPPORT.hwnd)
    monkeypatch.setattr(module, "activate", Mock(side_effect=ActivationDenied("Отказ Windows")))
    monkeypatch.setattr(module, "snapshot", lambda hwnd: SUPPORT if hwnd == SUPPORT.hwnd else FARMER)
    monkeypatch.setattr(module, "Desktop", Mock())
    attempts = []

    def helper(*args, strategy="foreground"):
        attempts.append(strategy)
        if strategy != success_on:
            raise RuntimeError("Windows не подтвердила foreground")

    monkeypatch.setattr(module, "request_activation", helper)
    assert api._activate_farmer(FARMER) == FARMER
    expected = ["foreground", "attached_focus"]
    if success_on == "task_switch":
        expected.append("task_switch")
    assert attempts == expected


@pytest.mark.parametrize("stop", [False, True])
def test_initial_activation_never_retries_after_stop_or_user_focus_change(tmp_path, monkeypatch, stop):
    import hid_pilot.api_runtime as module
    from hid_pilot.controller import Stopped
    from hid_pilot.desktop import ActivationDenied

    api = runtime(tmp_path)
    foreground = [SUPPORT.hwnd]
    monkeypatch.setattr(module.win32gui, "GetForegroundWindow", lambda: foreground[0])
    monkeypatch.setattr(module, "activate", Mock(side_effect=ActivationDenied("Отказ Windows")))
    monkeypatch.setattr(module, "snapshot", lambda hwnd: SUPPORT)

    def failed(*args):
        if stop:
            api.cancel.set()
        else:
            foreground[0] = 999
        raise RuntimeError("Отказ foreground")

    helper = Mock(side_effect=failed)
    monkeypatch.setattr(module, "request_activation", helper)
    with pytest.raises(Stopped if stop else RuntimeError):
        api._activate_farmer(FARMER)
    assert helper.call_count == 1


def test_search_camera_turn_uses_reduced_angle(monkeypatch):
    item = controller()
    monkeypatch.setattr(ActionController, "move", lambda *args, **kw: None)
    item.camera_turn("left", lambda: None, search=True)
    assert [call.args for call in item.transport.command.call_args_list] == [
        ("HB", "ALIVE"), ("CAMERA -300 0", "MOVING"),
        ("CAMERA_STATUS", ("MOVING", "DONE"))]
    item._begin_hold.assert_called_once_with("BUTTON", "right", 1000)
    assert item.transport.release.call_count == 2


def test_camera_delay_after_button_down_defers_without_sending_rejected_command(monkeypatch):
    item = controller()
    monkeypatch.setattr(ActionController, "move", lambda *args, **kw: None)
    now = [time.monotonic()]
    monkeypatch.setattr("hid_pilot.api_controller.time.monotonic", lambda: now[0])

    def delayed_wait(seconds):
        now[0] += .31
        return False

    monkeypatch.setattr(item.stopped, "wait", delayed_wait)
    with pytest.raises(ValueError, match="бюджет старта"):
        item.camera_turn("left", lambda: None, search=True)
    item.transport.command.assert_called_once_with("HB", "ALIVE")
    assert item.transport.release.call_count == 2


def test_slow_camera_validation_finishes_before_right_button_hold(monkeypatch):
    item = controller()
    monkeypatch.setattr(ActionController, "move", lambda *args, **kw: None)
    events = []
    item._begin_hold.side_effect = lambda *args: events.append("hold")
    item.transport.command.side_effect = lambda command, expected: (
        events.append(command) or ("DONE" if command == "CAMERA_STATUS" else None))
    item.camera_turn("left", lambda: events.append("validate"), search=True)
    hold = events.index("hold")
    movement = events.index("CAMERA -300 0")
    assert "validate" in events[:hold]
    assert "validate" not in events[hold:movement]


def test_camera_does_not_reposition_cursor_when_room_is_available(monkeypatch):
    item = controller()
    move = Mock(side_effect=AssertionError("Лишнее перемещение курсора"))
    monkeypatch.setattr(ActionController, "move", move)
    item.camera_turn("left", lambda: None, search=True)
    move.assert_not_called()
    assert item.transport.command.call_args_list[-2].args == ("CAMERA -300 0", "MOVING")


@pytest.mark.parametrize("point", [(0, 0), (1, 400), (999, 799), (100, 400)])
def test_camera_rotates_from_edges_without_moving_cursor(monkeypatch, point):
    item = controller()
    item.desktop.cursor.return_value = point
    move = Mock()
    monkeypatch.setattr(ActionController, "move", move)
    item.camera_turn("left", lambda: None, search=True)
    move.assert_not_called()
    item._begin_hold.assert_called_once_with("BUTTON", "right", 1000)
    assert item.transport.command.call_args_list[-2].args == ("CAMERA -300 0", "MOVING")
    assert all(call.args == (point,) for call in item.desktop.check_point.call_args_list)


def test_camera_rejects_cursor_outside_game_before_right_button(monkeypatch):
    item = controller()
    item.desktop.check_point.side_effect = RuntimeError("Курсор вне клиентской области")
    move = Mock()
    monkeypatch.setattr(ActionController, "move", move)
    with pytest.raises(RuntimeError, match="вне клиентской области"):
        item.camera_turn("left", lambda: None, search=True)
    move.assert_not_called()
    item._begin_hold.assert_not_called()
    item.transport.command.assert_not_called()
