import subprocess
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from l2_agent.network_capture import CaptureConfig, capture_command, capture_connection


def config(**changes):
    return CaptureConfig(**({
        "interface": 1, "client_ip": "192.0.2.10", "client_port": 50000,
        "server_ip": "198.51.100.20", "server_port": 7777,
    } | changes))


@pytest.mark.parametrize("changes", [
    {"interface": 0}, {"server_port": 0}, {"client_port": 65536},
    {"duration": 301}, {"max_kib": 65537}, {"server_ip": "any or tcp"},
])
def test_capture_rejects_invalid_scope(changes):
    with pytest.raises(ValidationError):
        config(**changes)


def test_capture_bounds_and_bidirectional_filter(tmp_path):
    command = capture_command("dumpcap", config(), tmp_path / "capture.pcapng")
    assert "duration:30" in command
    assert "filesize:16384" in command
    assert "-p" in command
    expression = command[command.index("-f") + 1]
    assert "src host 192.0.2.10 and src port 50000" in expression
    assert "src host 198.51.100.20 and src port 7777" in expression


def test_existing_capture_is_not_overwritten(monkeypatch, tmp_path):
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    output = tmp_path / "capture.pcapng"
    output.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        capture_connection(config(), output, "dumpcap")
    assert output.read_bytes() == b"original"
    run.assert_not_called()


def test_capture_failure_propagates_with_timeout(monkeypatch, tmp_path):
    run = Mock(side_effect=subprocess.TimeoutExpired("dumpcap", 45))
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        capture_connection(config(), tmp_path / "capture.pcapng", "dumpcap")
    assert run.call_args.kwargs["timeout"] == 45
    assert run.call_args.kwargs["check"] is True
