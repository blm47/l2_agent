import threading
from unittest.mock import Mock

import pytest

from hid_pilot.transport import PicoTransport


def command_transport(chunks):
    transport = PicoTransport.__new__(PicoTransport)
    transport.sequence = 3
    transport.failed = False
    transport.lock = threading.RLock()
    transport.serial = Mock()
    transport.serial.write.side_effect = len
    transport.serial.read_until.side_effect = chunks
    return transport


@pytest.mark.parametrize("chunks", [[b"4 OK ALIVE", b"\n"], [b"4 OK A", b"LI", b"VE\n"],
                                    [b"", b"4 OK ALIVE\n"]])
def test_fragmented_cdc_answer_is_accumulated_without_resending(chunks):
    transport = command_transport(chunks)
    assert transport.command("HB", "ALIVE") == "ALIVE"
    transport.serial.write.assert_called_once_with(b"4 HB\n")
    transport.serial.close.assert_not_called()


@pytest.mark.parametrize("reply", [b"4 ERR REJECTED\n", b"3 OK ALIVE\n", b"5 OK ALIVE\n"])
def test_rejected_or_wrong_sequence_stops_without_resending(reply):
    transport = command_transport([reply])
    with pytest.raises(OSError):
        transport.command("HB", "ALIVE")
    assert transport.failed
    transport.serial.write.assert_called_once_with(b"4 HB\n")
    transport.serial.close.assert_called_once()


def test_partial_answer_has_bounded_total_timeout(monkeypatch):
    now = [0.]
    monkeypatch.setattr("hid_pilot.transport.time.monotonic", lambda: now[0])
    transport = command_transport([])

    def read(*args):
        now[0] += .1
        return b"4 OK A" if now[0] == .1 else b""

    transport.serial.read_until.side_effect = read
    with pytest.raises(OSError):
        transport.command("HB", "ALIVE")
    assert now[0] <= .31
    transport.serial.write.assert_called_once()


@pytest.mark.parametrize("protocol", ["L2_PICO_V1", "HID_PILOT_V1", "HID_PILOT_V2", "HID_PILOT_V3", "HID_PILOT_V4"])
def test_supported_handshake_and_no_arm(monkeypatch, protocol):
    serial = Mock()
    serial.write.side_effect = len
    serial.read_until.return_value = f"1 OK {protocol}\n".encode()
    monkeypatch.setattr("serial.Serial", lambda *a, **k: serial)
    monkeypatch.setattr("hid_pilot.transport.time.sleep", lambda _: None)
    transport = PicoTransport("COM99")
    assert transport.protocol == protocol
    serial.write.assert_called_once_with(b"1 HELLO\n")


def test_unknown_firmware_rejected_before_arm(monkeypatch):
    serial = Mock()
    serial.write.side_effect = len
    serial.read_until.return_value = b"1 OK UNKNOWN\n"
    monkeypatch.setattr("serial.Serial", lambda *a, **k: serial)
    monkeypatch.setattr("hid_pilot.transport.time.sleep", lambda _: None)
    with pytest.raises(OSError):
        PicoTransport("COM99")
    serial.write.assert_called_once_with(b"1 HELLO\n")
    serial.close.assert_called()


@pytest.mark.parametrize("protocol", ["L2_PICO_V1", "HID_PILOT_V1"])
@pytest.mark.parametrize("key", ["-", "NUM7"])
def test_old_firmware_rejects_new_keys_before_command(protocol, key):
    transport = PicoTransport.__new__(PicoTransport)
    transport.protocol = protocol
    transport.command = Mock()
    with pytest.raises(ValueError, match="HID_PILOT_V2"):
        transport.hold("KEY", key, 100)
    transport.command.assert_not_called()


@pytest.mark.parametrize("protocol", ["L2_PICO_V1", "HID_PILOT_V1", "HID_PILOT_V2"])
def test_expanded_keyboard_requires_v3(protocol):
    transport = PicoTransport.__new__(PicoTransport)
    transport.protocol = protocol
    transport.command = Mock()
    for key in ("B", "F12", "RSHIFT", "NUMENTER"):
        with pytest.raises(ValueError, match="HID_PILOT_V3"):
            transport.hold("KEY", key, 100)
    transport.command.assert_not_called()


def test_v3_keyboard_and_hard_stop_rejection():
    transport = PicoTransport.__new__(PicoTransport)
    transport.protocol = "HID_PILOT_V3"
    transport.command = Mock()
    transport.hold("KEY", "RSHIFT", 100)
    transport.command.assert_called_once_with("KEY RSHIFT 100", "SENT")
    transport.command.reset_mock()
    with pytest.raises(ValueError):
        transport.hold("KEY", "\\", 100)
    transport.command.assert_not_called()
