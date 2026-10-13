import threading
from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.controller import ActionController, Stopped
from hid_pilot.desktop import FocusLost, match_image
from hid_pilot.runner import Runner
from hid_pilot.script import parse_script


def test_stop_blocks_later_input():
    transport = Mock()
    controller = ActionController(Mock(), transport, lambda: True)
    controller.stop()
    with pytest.raises(Stopped):
        controller.key("R", 100)
    transport.hold.assert_not_called()


def test_focus_failure_blocks_hid():
    desktop = Mock()
    desktop.check.side_effect = RuntimeError("Фокус потерян")
    transport = Mock()
    controller = ActionController(desktop, transport, lambda: True)
    with pytest.raises(RuntimeError):
        controller.key("R", 100)
    transport.hold.assert_not_called()


def test_hard_stop_unavailable_blocks_arm():
    transport = Mock()
    controller = ActionController(Mock(), transport, lambda: False)
    with pytest.raises(RuntimeError):
        controller.start()
    transport.arm.assert_not_called()


def test_cursor_drift_cancels_click():
    desktop = Mock()
    desktop.point.return_value = (100, 100)
    desktop.cursor.return_value = (120, 100)
    transport = Mock()
    controller = ActionController(desktop, transport, lambda: True)
    controller.move = Mock()
    with pytest.raises(RuntimeError):
        controller.click("left", .5, .5)
    transport.hold.assert_not_called()


def test_cursor_enters_game_from_start_button_before_click():
    desktop, transport = Mock(), Mock()
    desktop.point.return_value = (50, 50)
    positions = iter([(110, 50), (70, 50), (50, 50), (50, 50)])
    desktop.cursor.side_effect = lambda: next(positions)

    def check_point(point):
        if not all(0 <= value < 100 for value in point):
            raise RuntimeError("Курсор вне клиентской области")

    desktop.check_point.side_effect = check_point
    controller = ActionController(desktop, transport, lambda: True)
    controller.wait = Mock()
    controller.click("right", .5, .5)
    assert transport.method_calls[0][0] == "release"
    assert transport.command.call_count >= 1
    transport.hold.assert_called_once_with("BUTTON", "right", 80)


def test_obscured_destination_blocks_navigation():
    desktop, transport = Mock(), Mock()
    desktop.check_point.side_effect = RuntimeError("Окно перекрыто")
    controller = ActionController(desktop, transport, lambda: True)
    with pytest.raises(RuntimeError, match="перекрыто"):
        controller.click("right", .5, .5)
    transport.command.assert_not_called()
    transport.hold.assert_not_called()


def test_fast_mouse_step_preserves_fine_approach():
    desktop, transport = Mock(), Mock()
    desktop.point.return_value = (100, 100)
    desktop.cursor.side_effect = [(0, 100), (94, 100), (100, 100)]
    controller = ActionController(desktop, transport, lambda: True)
    controller.wait = Mock()
    controller.move(.5, .5)
    assert [call.args[0] for call in transport.command.call_args_list] == ["MOVE 16 0", "MOVE 3 0"]


def test_navigation_sends_three_bounded_verified_steps_between_waits():
    desktop, transport = Mock(), Mock()
    position = [0, 0]
    desktop.point.return_value = (160, 0)
    desktop.cursor.side_effect = lambda: tuple(position)
    moves_since_wait = [0]
    batches = []

    def command(text, expected):
        _, dx, dy = text.split()
        assert abs(int(dx)) <= 16 and abs(int(dy)) <= 16
        position[0] += int(dx)
        position[1] += int(dy)
        moves_since_wait[0] += 1

    def wait(seconds):
        batches.append(moves_since_wait[0])
        moves_since_wait[0] = 0

    transport.command.side_effect = command
    controller = ActionController(desktop, transport, lambda: True)
    controller.wait = wait
    verify = Mock()
    controller.move(.5, .5, verify=verify)
    assert batches and all(count == 3 for count in batches)
    assert verify.call_count > transport.command.call_count
    assert abs(position[0] - 160) <= 2


def test_click_uses_fast_navigation_while_center_return_keeps_curve(monkeypatch):
    desktop, transport = Mock(), Mock()
    desktop.point.return_value = (500, 300)
    desktop.cursor.return_value = (500, 300)
    desktop.target.rect = (0, 0, 1000, 600)
    path = Mock(return_value=[(400, 250), (500, 300)])
    monkeypatch.setattr("hid_pilot.controller.mouse_path", path)
    controller = ActionController(desktop, transport, lambda: True)
    controller.humanize = True
    controller._move_to = Mock()
    controller.wait = Mock()
    controller.click("left", .5, .5)
    path.assert_not_called()
    assert controller._move_to.call_args.args[3:] == (.001, True)
    controller._move_to.reset_mock()
    controller.move(.5, .5)
    path.assert_called_once()
    assert controller._move_to.call_count == 2
    for call in controller._move_to.call_args_list:
        assert .001 <= call.args[3] <= .002
        assert call.args[4] is False


def test_fast_navigation_handles_acceleration_without_waiting_for_applied_moves():
    desktop, transport = Mock(), Mock()
    position = [0, 0]
    desktop.point.return_value = (301, 123)
    desktop.cursor.side_effect = lambda: tuple(position)
    movements = []

    def command(text, expected):
        _, dx, dy = text.split()
        step = int(dx), int(dy)
        assert max(abs(value) for value in step) <= 16
        movements.append(step)
        position[0] += step[0] * 3
        position[1] += step[1] * 3

    transport.command.side_effect = command
    controller = ActionController(desktop, transport, lambda: True)
    controller.humanize = True
    controller.wait = Mock()
    controller.move(.5, .5, fast=True)
    assert len(movements) < 30
    assert abs(position[0] - 301) <= 2 and abs(position[1] - 123) <= 2
    controller.wait.assert_not_called()


def test_stuck_cursor_cancels_without_seconds_of_movement_or_clicks():
    desktop, transport = Mock(), Mock()
    desktop.point.return_value = (500, 300)
    desktop.cursor.return_value = (0, 0)
    controller = ActionController(desktop, transport, lambda: True)
    controller.humanize = True
    controller.wait = Mock()
    with pytest.raises(RuntimeError, match="движение прекращено"):
        controller.click("left", .5, .5)
    assert transport.command.call_count <= 20
    transport.hold.assert_not_called()


def test_navigation_waits_for_delayed_cursor_update_instead_of_queuing_moves():
    desktop, transport = Mock(), Mock()
    position, now = [0, 0], [0.]
    pending = []
    desktop.point.return_value = (301, 123)
    desktop.cursor.side_effect = lambda: tuple(position)

    def command(text, expected):
        assert not pending
        _, dx, dy = text.split()
        pending.append((now[0] + .025, int(dx), int(dy)))

    def wait(seconds):
        now[0] += seconds
        if pending and now[0] >= pending[0][0]:
            _, dx, dy = pending.pop()
            position[0] += dx * 3
            position[1] += dy * 3

    controller = ActionController(desktop, transport, lambda: True)
    controller.active_time = lambda: now[0]
    controller.wait = wait
    transport.command.side_effect = command
    controller.move(.5, .5, fast=True)
    assert max(abs(a - b) for a, b in zip(position, (301, 123))) <= 2
    assert not pending


@pytest.mark.parametrize("duration", [1., 2.5])
def test_return_to_center_finishes_within_chosen_duration(duration):
    desktop, transport = Mock(), Mock()
    position, now = [50, 50], [0.]
    desktop.target.rect = (0, 0, 1000, 600)
    desktop.point.return_value = (500, 300)
    desktop.cursor.side_effect = lambda: tuple(position)

    def command(text, expected):
        _, dx, dy = text.split()
        assert max(abs(int(dx)), abs(int(dy))) <= 16
        position[0] += int(dx) * 3
        position[1] += int(dy) * 3

    controller = ActionController(desktop, transport, lambda: True)
    controller.active_time = lambda: now[0]
    controller.wait = lambda seconds: now.__setitem__(0, now[0] + seconds)
    transport.command.side_effect = command
    controller.move(.5, .5, duration=duration)
    assert now[0] == pytest.approx(duration)
    assert max(abs(a - b) for a, b in zip(position, (500, 300))) <= 2


@pytest.mark.parametrize("interrupt", [False, True])
def test_recovery_view_is_bounded_and_releases_buttons(monkeypatch, interrupt):
    now = [0.]
    monkeypatch.setattr("hid_pilot.controller.time.monotonic", lambda: now[0])
    desktop, transport = Mock(), Mock()
    desktop.cursor.return_value = (100, 100)
    controller = ActionController(desktop, transport, lambda: True)
    controller.move = Mock()

    def wait(seconds):
        now[0] += seconds
        if interrupt and transport.command.called:
            controller.stopped.set()

    controller.wait = wait
    if interrupt:
        with pytest.raises(Stopped):
            controller.recover_view(10.)
    else:
        controller.recover_view(10.)
        assert now[0] < 4
        assert [call.args[1] for call in transport.hold.call_args_list] == [
            "W", "A", "S", "D", "right", "right", "right"]
    assert transport.method_calls[-1][0] == "release"
    assert transport.command.called


@pytest.mark.parametrize("failure", ["focus", "bounds"])
def test_recovery_camera_cancels_on_focus_or_boundary_failure(monkeypatch, failure):
    now = [0.]
    monkeypatch.setattr("hid_pilot.controller.time.monotonic", lambda: now[0])
    desktop, transport = Mock(), Mock()
    desktop.cursor.return_value = (100, 100)
    controller = ActionController(desktop, transport, lambda: True)
    controller.move = Mock()
    controller.wait = lambda seconds: now.__setitem__(0, now[0] + seconds)

    def hold(kind, name, duration):
        if kind == "BUTTON":
            if failure == "focus":
                desktop.check.side_effect = FocusLost("Фокус потерян")
            else:
                desktop.check_point.side_effect = RuntimeError("Граница окна")

    transport.hold.side_effect = hold
    with pytest.raises(RuntimeError):
        controller.recover_view(10.)
    transport.command.assert_not_called()
    assert transport.method_calls[-1][0] == "release"


def test_runner_loops_and_unknown_point(tmp_path):
    controller = Mock()
    runner = Runner(controller, tmp_path, lambda _: None)
    runner.run(parse_script("repeat 3\nkey R 10\nend\nif_found\nclick left\nend"))
    assert controller.key.call_count == 3
    controller.click.assert_not_called()
    with pytest.raises(RuntimeError):
        runner.run(parse_script("click left"))


def test_failed_find_clears_previous_result(tmp_path):
    controller = Mock()
    runner = Runner(controller, tmp_path, lambda _: None)
    runner.found = (.5, .5)
    with pytest.raises(FileNotFoundError):
        runner.run(parse_script('find "missing.png" .9 0'))
    assert runner.found is None


def test_template_match_location_and_rejection():
    rng = np.random.default_rng(12)
    template = rng.integers(1, 255, (8, 10, 3), dtype=np.uint8)
    image = np.zeros((50, 60, 3), dtype=np.uint8)
    image[20:28, 30:40] = template
    assert match_image(image, template, .99) == pytest.approx((34.5 / 59, 23.5 / 49))
    assert match_image(np.zeros_like(image), template, .99) is None


def test_pause_blocks_commands_until_resume():
    transport = Mock()
    controller = ActionController(Mock(), transport, lambda: True)
    controller.pause()
    reached = threading.Event()

    def worker():
        with controller.ready():
            reached.set()

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert not reached.wait(.05)
        controller.resume()
        assert reached.wait(1)
        transport.stop.assert_called_once()
        transport.arm.assert_called_once()
    finally:
        controller.stopped.set()
        thread.join(1)


def test_focus_pause_releases_and_automatically_resumes_same_pending_command():
    desktop, transport = Mock(), Mock()
    focused = threading.Event()
    released, sent = threading.Event(), threading.Event()

    def check():
        if not focused.is_set():
            raise FocusLost("test")

    desktop.check.side_effect = check
    transport.stop.side_effect = released.set
    transport.hold.side_effect = lambda *args: sent.set()
    controller = ActionController(desktop, transport, lambda: True)
    thread = threading.Thread(target=lambda: controller.key("F9", 1))
    thread.start()
    try:
        assert released.wait(1)
        assert controller.paused.is_set()
        assert not controller.stopped.is_set()
        transport.hold.assert_not_called()
        focused.set()
        assert sent.wait(1)
        thread.join(1)
        assert not thread.is_alive()
        transport.arm.assert_called_once()
        transport.hold.assert_called_once_with("KEY", "F9", 1)
    finally:
        controller.stop()
        thread.join(1)


def test_manual_pause_survives_focus_return_and_resume_can_wait_in_background():
    desktop, transport = Mock(), Mock()
    controller = ActionController(desktop, transport, lambda: True)
    desktop.check.side_effect = FocusLost("background")
    controller.pause()
    desktop.check.side_effect = None
    with controller.lock:
        controller.sync_pause()
    assert controller.manual_paused.is_set() and controller.paused.is_set()
    transport.arm.assert_not_called()
    desktop.check.side_effect = FocusLost("background")
    controller.resume()
    assert not controller.manual_paused.is_set() and controller.paused.is_set()
    transport.arm.assert_not_called()
    desktop.check.side_effect = None
    with controller.lock:
        controller.sync_pause()
    assert not controller.paused.is_set()
    transport.arm.assert_called_once()


def test_capture_focus_race_is_retried_without_hid():
    desktop, transport = Mock(), Mock()
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    desktop.capture.side_effect = [FocusLost("race"), frame]
    controller = ActionController(desktop, transport, lambda: True)
    assert controller.capture() is frame
    transport.hold.assert_not_called()


def test_invalid_window_is_not_automatically_resumed():
    desktop, transport = Mock(), Mock()
    controller = ActionController(desktop, transport, lambda: True)
    controller.pause()
    desktop.check.side_effect = RuntimeError("PID changed")
    with pytest.raises(RuntimeError, match="PID"):
        controller.resume()
    transport.arm.assert_not_called()


def test_pause_does_not_consume_active_time(monkeypatch):
    now = [10.]
    monkeypatch.setattr("hid_pilot.controller.time.monotonic", lambda: now[0])
    controller = ActionController(Mock(), Mock(), lambda: True)
    controller.pause()
    now[0] = 100.
    assert controller.active_time() == 10.
    controller.resume()
    now[0] = 102.
    assert controller.active_time() == 12.


def test_hard_stop_cancels_background_wait_without_resume():
    desktop, transport = Mock(), Mock()
    desktop.check.side_effect = FocusLost("background")
    controller = ActionController(desktop, transport, lambda: True)
    controller.pause()
    controller.stop()
    with pytest.raises(Stopped):
        controller.checkpoint()
    transport.arm.assert_not_called()


def test_nested_empty_loops_have_execution_budget(tmp_path):
    runner = Runner(Mock(), tmp_path, lambda _: None)
    runner.steps = 99999
    with pytest.raises(RuntimeError):
        runner.run(parse_script("repeat 1000\nrepeat 1000\nend\nend"))


def test_click_current_outside_window_is_rejected():
    desktop = Mock()
    desktop.check_point.side_effect = RuntimeError("Курсор вне окна")
    transport = Mock()
    controller = ActionController(desktop, transport, lambda: True)
    with pytest.raises(RuntimeError):
        controller.click_current("left")
    transport.hold.assert_not_called()


def test_legacy_firmware_cannot_move_mouse():
    transport = Mock(protocol="L2_PICO_V1")
    controller = ActionController(Mock(), transport, lambda: True)
    with pytest.raises(RuntimeError, match="HID_PILOT_V1"):
        controller.move(.5, .5)
    transport.command.assert_not_called()


def test_target_guard_can_abort_during_mouse_movement_before_click():
    desktop = Mock()
    desktop.point.return_value = (40, 0)
    desktop.cursor.return_value = (0, 0)
    transport = Mock(protocol="HID_PILOT_V2")
    controller = ActionController(desktop, transport, lambda: True)
    controller.wait = Mock()
    verify = Mock(side_effect=[None, RuntimeError("Цель сменилась")])
    with pytest.raises(RuntimeError, match="Цель"):
        controller.click("left", .5, .5, verify=verify)
    assert transport.command.call_count == 1
    transport.hold.assert_not_called()
