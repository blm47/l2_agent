from unittest.mock import Mock

import pytest

from firmware.pico.control import KEYS, Control
from l2_agent.action_validator import ALLOWED_KEYS


@pytest.fixture
def board():
    clock = Mock(return_value=10.0)
    control = Control(Mock(), Mock(), clock)
    return control, clock


def test_allowlists_match():
    assert set(KEYS) == set(ALLOWED_KEYS)
    assert not set(KEYS.values()) & set(range(224, 232))


@pytest.mark.parametrize("command", [b"KEY W 100", b"BUTTON left 100"])
def test_input_requires_arm(board, command):
    control, _ = board
    assert b"ERR" in control.command(b"1 " + command)
    assert not control.active


@pytest.mark.parametrize("command", [b"KEY W 100", b"BUTTON right 100"])
def test_deadline_releases_without_host(board, command):
    control, clock = board
    control.command(b"1 ARM")
    assert control.command(b"2 " + command) == b"2 OK SENT\n"
    assert control.active
    clock.return_value = 10.11
    control.tick(True)
    assert not control.active
    control.keyboard.send_report.assert_called_with(bytes(8))
    control.mouse.send_report.assert_called_with(bytes(4))


@pytest.mark.parametrize("connected", [True, False])
def test_disconnect_or_heartbeat_timeout_disarms(board, connected):
    control, clock = board
    control.command(b"1 ARM")
    control.command(b"2 KEY W 1000")
    clock.return_value = 10.36
    control.tick(connected)
    assert not control.armed
    assert not control.active
    assert b"ERR" in control.command(b"3 KEY W 100")


def test_heartbeat_cannot_extend_hold(board):
    control, clock = board
    control.command(b"1 ARM")
    control.command(b"2 KEY W 100")
    clock.return_value = 10.09
    control.command(b"3 HB")
    clock.return_value = 10.11
    control.tick(True)
    assert control.armed
    assert not control.active


@pytest.mark.parametrize(
    "command",
    [
        b"KEY F10 100",
        b"KEY \\ 100",
        b"KEY CTRL 100",
        b"KEY W 1001",
        b"KEY W 0",
        b"KEY W -1",
        b"KEY W 1.5",
        b"BUTTON middle 100",
        b"BOGUS",
        b"",
        b"\xff",
    ],
)
def test_malformed_commands_fail_closed(board, command):
    control, _ = board
    control.command(b"1 ARM")
    assert b"ERR" in control.command(b"2 " + command)
    assert not control.armed


def test_duplicate_command_releases_and_disarms(board):
    control, _ = board
    control.command(b"1 ARM")
    control.command(b"2 KEY W 1000")
    assert b"ERR" in control.command(b"2 KEY W 1000")
    assert not control.active
    assert not control.armed


def test_stop_is_available_without_arm(board):
    control, _ = board
    assert control.command(b"1 STOP") == b"1 OK STOPPED\n"


def test_fragmentation_and_overflow(board):
    control, _ = board
    assert control.feed(b"1 HEL") == []
    assert control.feed(b"LO\n") == [b"1 OK L2_PICO_V1\n"]
    control.command(b"2 ARM")
    control.feed(b"x" * 10000)
    assert not control.armed
    assert len(control.buffer) <= 64
    assert control.feed(b"3 KEY W 100\n") == [b"-1 ERR LINE_TOO_LONG\n"]


def test_second_hold_cannot_replace_first(board):
    control, _ = board
    control.command(b"1 ARM")
    control.command(b"2 KEY W 1000")
    assert b"ERR" in control.command(b"3 BUTTON left 1000")
    assert not control.active


def test_mouse_release_attempted_even_when_keyboard_release_fails(board):
    control, _ = board
    control.keyboard.send_report.side_effect = OSError("USB")
    control.mouse.reset_mock()
    with pytest.raises(OSError):
        control.disarm()
    control.mouse.send_report.assert_called_once_with(bytes(4))
