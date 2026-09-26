import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from l2_agent.pico import PicoTransport


@pytest.fixture
def transport(monkeypatch):
    port = Mock()
    port.write.side_effect = lambda data: len(data)
    port.read_until.return_value = b"1 OK L2_PICO_V1\n"
    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=Mock(return_value=port)))
    monkeypatch.setattr("l2_agent.pico.time.sleep", lambda _: None)
    return PicoTransport("COM99"), port


@pytest.mark.parametrize("reply", [b"", b"1 OK ARMED\n", b"2 ERR REJECTED\n"])
def test_timeout_or_wrong_ack_closes_connection_without_retry(transport, reply):
    pico, port = transport
    port.read_until.return_value = reply
    with pytest.raises(OSError):
        pico.arm()
    assert pico.failed
    port.close.assert_called_once()
    count = port.write.call_count
    with pytest.raises(OSError):
        pico.hold("KEY", "W", 100)
    assert port.write.call_count == count


def test_partial_write_fails_closed(transport):
    pico, port = transport
    port.write.side_effect = lambda _: 1
    with pytest.raises(OSError):
        pico.arm()
    assert pico.failed


def test_hold_requires_matching_ack(transport):
    pico, port = transport
    port.read_until.return_value = b"2 OK SENT\n"
    pico.hold("KEY", "W", 100)
    port.write.assert_called_with(b"2 KEY W 100\n")
