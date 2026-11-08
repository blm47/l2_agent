import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    "pilot_board", Path(__file__).parents[1] / "firmware" / "control.py"
)
board_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board_module)


def test_chat_chord_usb_report_and_watchdog_release():
    keyboard, mouse = Mock(), Mock()
    now = [0.]
    board = board_module.Control(keyboard, mouse, lambda: now[0])
    board.command(b"1 ARM")
    assert board.command(b"2 CHORD CTRL+A 80") == b"2 OK SENT\n"
    keyboard.send_report.assert_called_with(bytes((1, 0, 4, 0, 0, 0, 0, 0)))
    now[0] = .08
    board.tick(True)
    keyboard.send_report.assert_called_with(bytes(8))
    assert board.command(b"3 KEY W 3000") == b"3 OK SENT\n"
    now[0] = .36
    board.tick(True)
    assert not board.armed
    keyboard.send_report.assert_called_with(bytes(8))


def test_switch_alt_tab_only_disarmed_and_releases_on_deadline():
    keyboard = Mock()
    now = [0.]
    board = board_module.Control(keyboard, Mock(), lambda: now[0])
    assert board.command(b"1 SWITCH ALT+TAB 80") == b"1 OK SENT\n"
    keyboard.send_report.assert_called_with(bytes((4, 0, 43, 0, 0, 0, 0, 0)))
    assert not board.armed
    now[0] = .08
    board.tick(True)
    keyboard.send_report.assert_called_with(bytes(8))
    board.command(b"2 ARM")
    assert board.command(b"3 SWITCH ALT+TAB 80") == b"3 ERR REJECTED\n"
    assert not board.armed


def test_switch_rejects_unbounded_duration_and_other_chords():
    board = board_module.Control(Mock(), Mock(), lambda: 0.)
    assert b"ERR" in board.command(b"1 SWITCH ALT+TAB 1000")
    assert b"ERR" in board.command(b"2 SWITCH WIN+R 80")


@pytest.mark.parametrize("chord", [b"ALT+TAB", b"CTRL+ESC", b"WIN+R", b"SHIFT+ENTER"])
def test_firmware_rejects_os_chords(chord):
    board = board_module.Control(Mock(), Mock(), lambda: 0.)
    board.command(b"1 ARM")
    assert b"ERR" in board.command(b"2 CHORD " + chord + b" 80")
    assert not board.armed


def test_hid_move_and_heartbeat_timeout():
    keyboard, mouse, clock = Mock(), Mock(), Mock(return_value=1.0)
    board = board_module.Control(keyboard, mouse, clock)
    assert board.command(b"1 HELLO") == b"1 OK HID_PILOT_V10\n"
    assert board.command(b"2 MOVE 1 1") == b"2 ERR REJECTED\n"
    board.command(b"3 ARM")
    assert board.command(b"4 MOVE -8 7") == b"4 OK MOVED\n"
    mouse.send_report.assert_called_with(bytes((0, 248, 7, 0)))
    clock.return_value = 1.36
    board.tick(True)
    assert not board.armed
    mouse.send_report.assert_called_with(bytes(4))


@pytest.mark.parametrize("destination", [(200, 200), (-200, -200)])
def test_controller_navigation_is_accepted_by_firmware(destination):
    from hid_pilot.controller import ActionController

    position = [0, 0]
    mouse = Mock()

    def report(data):
        for axis in (0, 1):
            value = data[axis + 1]
            position[axis] += value if value < 128 else value - 256

    mouse.send_report.side_effect = report
    board = board_module.Control(Mock(), mouse, lambda: 1.)
    sequence = [0]

    def command(text, expected):
        sequence[0] += 1
        result = board.command(f"{sequence[0]} {text}".encode())
        assert result == f"{sequence[0]} OK {expected}\n".encode()

    transport = Mock(protocol="HID_PILOT_V4")
    transport.command.side_effect = command
    transport.release.side_effect = lambda: command("RELEASE", "RELEASED")
    command("ARM", "ARMED")
    desktop = Mock()
    desktop.point.return_value = destination
    desktop.cursor.side_effect = lambda: tuple(position)
    controller = ActionController(desktop, transport, lambda: True)
    controller.wait = Mock()
    controller.move(.5, .5)
    assert max(abs(a - b) for a, b in zip(position, destination)) <= 2
    assert board.armed


@pytest.mark.parametrize("command", [b"MOVE 17 0", b"MOVE 0 -17", b"KEY ALT+TAB 10",
                                     b"KEY R 3001", b"BUTTON middle 10"])
def test_invalid_input_disarms(command):
    board = board_module.Control(Mock(), Mock(), lambda: 1.0)
    board.command(b"1 ARM")
    assert b"ERR" in board.command(b"2 " + command)
    assert not board.armed


def test_move_rejected_during_hold_and_deadline_releases():
    clock = Mock(return_value=1.0)
    board = board_module.Control(Mock(), Mock(), clock)
    board.command(b"1 ARM")
    board.command(b"2 KEY R 100")
    assert b"ERR" in board.command(b"3 MOVE 1 0")
    board.command(b"4 ARM")
    board.command(b"5 BUTTON left 100")
    clock.return_value = 1.11
    board.tick(True)
    assert not board.active


def test_camera_move_preserves_right_button_and_does_not_extend_deadline():
    mouse = Mock()
    now = [1.]
    board = board_module.Control(Mock(), mouse, lambda: now[0])
    board.command(b"1 ARM")
    board.command(b"2 BUTTON right 300")
    deadline = board.deadline
    assert board.command(b"3 MOVE -6 0") == b"3 OK MOVED\n"
    mouse.send_report.assert_called_with(bytes((2, 250, 0, 0)))
    now[0] = 1.2
    assert board.command(b"4 MOVE 6 0") == b"4 OK MOVED\n"
    assert board.deadline == deadline
    now[0] = 1.301
    board.tick(True)
    assert not board.active and board.mouse_buttons == 0
    mouse.send_report.assert_called_with(bytes(4))


def test_fast_camera_step_is_allowed_only_with_right_button_and_remains_bounded():
    board = board_module.Control(Mock(), Mock(), lambda: 0.)
    board.command(b"1 ARM")
    assert b"ERR" in board.command(b"2 MOVE 120 0")
    board.command(b"3 ARM")
    board.command(b"4 BUTTON right 300")
    assert board.command(b"5 MOVE -120 0") == b"5 OK MOVED\n"
    assert b"ERR" in board.command(b"6 MOVE -121 0")
    assert not board.armed and not board.active


@pytest.mark.parametrize("amount", [-4, -1, 1, 4])
def test_wheel_report_and_no_hold(amount):
    mouse = Mock()
    board = board_module.Control(Mock(), mouse, lambda: 0.)
    board.command(b"1 ARM")
    assert board.command(f"2 WHEEL {amount}".encode()) == b"2 OK SCROLLED\n"
    mouse.send_report.assert_called_with(bytes((0, 0, 0, amount & 255)))
    assert not board.active


@pytest.mark.parametrize("command", [b"WHEEL 0", b"WHEEL 5", b"WHEEL -5"])
def test_wheel_invalid_amount_disarms(command):
    board = board_module.Control(Mock(), Mock(), lambda: 0.)
    board.command(b"1 ARM")
    assert b"ERR" in board.command(b"2 " + command)
    assert not board.armed


@pytest.mark.parametrize("ending", ["STOP", "RELEASE", "disconnect", "heartbeat"])
def test_camera_hold_is_released_by_every_stop_path(ending):
    now = [0.]
    mouse = Mock()
    board = board_module.Control(Mock(), mouse, lambda: now[0])
    board.command(b"1 ARM")
    board.command(b"2 BUTTON right 1000")
    board.command(b"3 MOVE 6 0")
    if ending in {"STOP", "RELEASE"}:
        board.command(f"4 {ending}".encode())
    else:
        now[0] = .36
        board.tick(ending != "disconnect")
    assert not board.active and board.mouse_buttons == 0
    mouse.send_report.assert_called_with(bytes(4))


def test_real_api_camera_commands_are_accepted_by_firmware(monkeypatch):
    import time

    from hid_pilot.api_controller import ApiActionController
    from hid_pilot.controller import ActionController

    mouse = Mock()
    board = board_module.Control(Mock(), mouse, time.monotonic)
    sequence = [0]

    def command(text, expected):
        sequence[0] += 1
        reply = board.command(f"{sequence[0]} {text}".encode())
        assert reply == f"{sequence[0]} OK {expected}\n".encode()

    transport = Mock(supports_camera_drag=True, supports_fast_camera=True)
    transport.command.side_effect = command
    transport.release.side_effect = lambda: command("RELEASE", "RELEASED")
    transport.hold.side_effect = lambda kind, name, ms: command(f"{kind} {name} {ms}", "SENT")
    desktop = Mock()
    desktop.target.rect = (0, 0, 1000, 800)
    desktop.cursor.return_value = (500, 400)
    controller = ApiActionController(desktop, transport, lambda: True)
    monkeypatch.setattr(ActionController, "move", lambda *args, **kw: None)
    monkeypatch.setattr("hid_pilot.controller.win32api.GetAsyncKeyState", lambda _: 0)
    command("ARM", "ARMED")
    controller.camera_turn("left", lambda: None)
    reports = [call.args[0] for call in mouse.send_report.call_args_list]
    assert reports.count(bytes((2, 226, 0, 0))) == 1
    assert reports[-1] == bytes(4)
    assert not board.active and board.armed


def test_strong_camera_is_one_command_with_exact_displacement():
    mouse = Mock()
    board = board_module.Control(Mock(), mouse, lambda: 0.)
    board.command(b"1 ARM")
    board.command(b"2 BUTTON right 300")
    mouse.send_report.reset_mock()
    assert board.command(b"3 CAMERA -300 0") == b"3 OK MOVING\n"
    for _ in range(13):
        board.tick(True)
    assert board.command(b"4 CAMERA_STATUS") == b"4 OK DONE\n"
    reports = [call.args[0] for call in mouse.send_report.call_args_list]
    assert len(reports) == 13
    assert max(abs(int.from_bytes(report[1:2], "little", signed=True)) for report in reports) == 24
    assert sum(int.from_bytes(report[1:2], "little", signed=True) for report in reports) == -300
    assert all(report[0] == 2 and report[2:] == bytes(2) for report in reports)
    assert board.deadline == .3


@pytest.mark.parametrize("command", [b"CAMERA -901 0", b"CAMERA 1 1", b"CAMERA 0 0"])
def test_strong_camera_rejects_invalid_displacement(command):
    board = board_module.Control(Mock(), Mock(), lambda: 0.)
    board.command(b"1 ARM")
    board.command(b"2 BUTTON right 300")
    assert b"ERR" in board.command(b"3 " + command)
    assert not board.armed


def test_slow_usb_camera_accepts_heartbeat_until_completed():
    now = [0.]
    mouse = Mock()

    def slow_report(report):
        now[0] += .032

    mouse.send_report.side_effect = slow_report
    board = board_module.Control(Mock(), mouse, lambda: now[0])
    board.command(b"1 ARM")
    board.command(b"2 BUTTON right 1000")
    assert board.command(b"3 CAMERA -300 0") == b"3 OK MOVING\n"
    sequence = 3
    for _ in range(20):
        sequence += 1
        assert board.command(f"{sequence} HB".encode()) == f"{sequence} OK ALIVE\n".encode()
        sequence += 1
        reply = board.command(f"{sequence} CAMERA_STATUS".encode())
        if b"DONE" in reply:
            break
    else:
        pytest.fail("Рывок не завершён")
    assert now[0] > .3
    assert board.armed and board.mouse_buttons == 2 and board.camera_done


@pytest.mark.parametrize("ending", ["STOP", "RELEASE", "heartbeat", "disconnect", "deadline"])
def test_pending_camera_stops_without_remaining_reports(ending):
    now = [0.]
    mouse = Mock()
    board = board_module.Control(Mock(), mouse, lambda: now[0])
    board.command(b"1 ARM")
    board.command(b"2 BUTTON right 1000")
    board.command(b"3 CAMERA -300 0")
    if ending in {"STOP", "RELEASE"}:
        board.command(f"4 {ending}".encode())
    else:
        now[0] = 1.01 if ending == "deadline" else .36
        if ending == "deadline":
            board.heartbeat = now[0]
        board.tick(ending != "disconnect")
    sent = mouse.send_report.call_count
    board.tick(True)
    assert mouse.send_report.call_count == sent
    assert board.camera_delta is None and board.mouse_buttons == 0


def test_allowlist_matches_parser():
    from hid_pilot.keyboard import KEYS as usages
    from hid_pilot.script import KEYS
    assert set(board_module.KEYS) == KEYS
    assert board_module.KEYS == usages


@pytest.mark.parametrize("key,usage", [(key, usage) for key, usage in board_module.KEYS.items()
                                      if usage not in (226, 230)])
def test_every_keyboard_key_report_and_release(key, usage):
    keyboard, clock = Mock(), Mock(return_value=1.)
    board = board_module.Control(keyboard, Mock(), clock)
    board.command(b"1 ARM")
    assert board.command(f"2 KEY {key} 100".encode()) == b"2 OK SENT\n"
    modifier = 1 << (usage - 224) if usage >= 224 else 0
    keyboard.send_report.assert_called_with(bytes((modifier, 0, 0 if modifier else usage, 0, 0, 0, 0, 0)))
    clock.return_value = 1.11
    board.tick(True)
    keyboard.send_report.assert_called_with(bytes(8))


@pytest.mark.parametrize("key", ["ALT", "LALT", "RALT"])
def test_alt_cannot_be_held_by_game_key_command(key):
    keyboard = Mock()
    board = board_module.Control(keyboard, Mock(), lambda: 0.)
    board.command(b"1 ARM")
    keyboard.send_report.reset_mock()
    assert b"ERR" in board.command(f"2 KEY {key} 100".encode())
    keyboard.send_report.assert_called_once_with(bytes(8))


@pytest.mark.parametrize("action", [b"KEY F6 100", b"CHORD SHIFT+A 80", b"BUTTON left 80"])
def test_modifier_release_report_precedes_every_press(action):
    keyboard, mouse = Mock(), Mock()
    events = []
    keyboard.send_report.side_effect = lambda report: events.append(("keyboard", report))
    mouse.send_report.side_effect = lambda report: events.append(("mouse", report))
    board = board_module.Control(keyboard, mouse, lambda: 0.)
    board.command(b"1 ARM")
    events.clear()
    assert board.command(b"2 " + action) == b"2 OK SENT\n"
    assert events[0] == ("keyboard", bytes(8))
    assert len(events) == 2


@pytest.mark.parametrize("key", ["\\", "BACKSLASH", "NONUSBACKSLASH", "49", "100"])
def test_hard_stop_cannot_be_sent_even_by_direct_serial_command(key):
    keyboard = Mock()
    board = board_module.Control(keyboard, Mock(), lambda: 1.)
    board.command(b"1 ARM")
    keyboard.send_report.reset_mock()
    assert b"ERR" in board.command(f"2 KEY {key} 100".encode())
    keyboard.send_report.assert_called_once_with(bytes(8))
    assert not board.armed
    assert not {49, 50, 100} & set(board_module.KEYS.values())


def test_equals_key_uses_hid_usage_46():
    keyboard = Mock()
    board = board_module.Control(keyboard, Mock(), lambda: 1.)
    board.command(b"1 ARM")
    assert board.command(b"2 KEY = 100") == b"2 OK SENT\n"
    keyboard.send_report.assert_called_with(bytes((0, 0, 46, 0, 0, 0, 0, 0)))


@pytest.mark.parametrize("key,usage", [("-", 45), ("NUM7", 95)])
def test_healer_keys_have_distinct_hid_usages(key, usage):
    keyboard = Mock()
    board = board_module.Control(keyboard, Mock(), lambda: 1.)
    board.command(b"1 ARM")
    assert board.command(f"2 KEY {key} 100".encode()) == b"2 OK SENT\n"
    keyboard.send_report.assert_called_with(bytes((0, 0, usage, 0, 0, 0, 0, 0)))
