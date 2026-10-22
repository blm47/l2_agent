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
    assert board.command(b"1 HELLO") == b"1 OK HID_PILOT_V5\n"
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
