import hashlib
import json
import struct
from pathlib import Path
from unittest.mock import Mock

import pytest

from hid_pilot.firmware_manager import (
    FIRMWARE_FILES,
    RP2040_FAMILY,
    backup_files,
    flash_runtime,
    identify_volume,
    install_scripts,
    pico_ports,
    validate_uf2,
    verify_firmware,
)
from hid_pilot.paths import resource_path


@pytest.fixture
def board(tmp_path, monkeypatch):
    root = tmp_path / "pico"
    root.mkdir()
    (root / "boot_out.txt").write_text(
        "Adafruit CircuitPython 10.3.1; Raspberry Pi Pico with rp2040\n"
        "Board ID:raspberry_pi_pico\nUID:0123456789ABCDEF\n", "utf-8")
    serial = [123]
    monkeypatch.setattr("hid_pilot.firmware_manager.volume_info", lambda path: ("CIRCUITPY", serial[0]))
    return identify_volume(root), serial


def uf2(family=RP2040_FAMILY):
    header = struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0x2000,
                         0x10000000, 256, 0, 1, family)
    return header + bytes(476) + struct.pack("<I", 0x0AB16F30)


def test_install_backs_up_before_replacing_and_verifies_files(board, tmp_path):
    volume, _serial = board
    for name in FIRMWARE_FILES:
        (volume.root / name).write_bytes(b"previous")
    settings = volume.root / "settings.toml"
    settings.write_bytes(b"custom=true")
    notify = Mock()
    backup = install_scripts(volume, notify, tmp_path / "backups")
    for name in FIRMWARE_FILES:
        assert (backup / "files" / name).read_bytes() == b"previous"
        assert (volume.root / name).read_bytes() == resource_path(f"firmware/{name}").read_bytes()
    assert settings.read_bytes() == b"custom=true"
    assert not list(volume.root.glob(".hidpilot*"))
    manifest = json.loads((backup / "manifest.json").read_text("utf-8"))
    assert manifest["files"]["control.py"] == hashlib.sha256(b"previous").hexdigest()
    assert notify.call_count == 2


def test_replaced_volume_prevents_writes_after_backup(board, tmp_path):
    volume, serial = board
    (volume.root / "code.py").write_bytes(b"previous")
    with pytest.raises(ValueError, match="заменена"):
        install_scripts(volume, lambda text: serial.__setitem__(0, 999), tmp_path / "backups")
    assert (volume.root / "code.py").read_bytes() == b"previous"
    assert not (volume.root / "control.py").exists()


def test_failed_install_retains_backup_and_removes_staged_files(board, tmp_path, monkeypatch):
    volume, _serial = board
    (volume.root / "code.py").write_bytes(b"previous")
    replace = Path.replace

    def fail_code(path, target):
        if path.name == ".hidpilot-code.py.new":
            raise OSError("Simulated USB failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_code)
    with pytest.raises(OSError, match="USB failure"):
        install_scripts(volume, Mock(), tmp_path / "backups")
    backups = list((tmp_path / "backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "files" / "code.py").read_bytes() == b"previous"
    assert not list(volume.root.glob(".hidpilot*"))


def test_backup_rejects_destination_on_board_and_large_storage(board, tmp_path):
    volume, _serial = board
    with pytest.raises(ValueError, match="компьютере"):
        backup_files(volume, volume.root / "backup")
    (volume.root / "huge.bin").write_bytes(bytes(4_000_001))
    with pytest.raises(ValueError, match="лимит"):
        install_scripts(volume, Mock(), tmp_path / "backups")
    assert not (volume.root / "control.py").exists()


@pytest.mark.parametrize("label,info", [
    ("OTHER", ""), ("RPI-RP2", "Board-ID: RPI-RP2\nModel: RP2350"),
    ("CIRCUITPY", "Board ID:raspberry_pi_pico2\n"),
])
def test_unrelated_or_unsupported_drives_are_rejected(label, info, tmp_path, monkeypatch):
    monkeypatch.setattr("hid_pilot.firmware_manager.volume_info", lambda _: (label, 1))
    (tmp_path / "INFO_UF2.TXT").write_text(info, "utf-8")
    (tmp_path / "boot_out.txt").write_text(info, "utf-8")
    with pytest.raises(ValueError):
        identify_volume(tmp_path)


def test_uf2_checksum_family_and_block_validation(tmp_path, monkeypatch):
    from hid_pilot.firmware_manager import vendor_asset

    path = tmp_path / "raspberry_pi_pico.uf2"
    data = uf2()
    path.write_bytes(data)
    (tmp_path / "manifest.json").write_text(json.dumps({"assets": {
        path.name: {"sha256": hashlib.sha256(data).hexdigest()}}}), "utf-8")
    monkeypatch.setattr("hid_pilot.firmware_manager.resource_path", lambda _: tmp_path)
    assert vendor_asset(path.name) == path
    path.write_bytes(data[:-1] + b"x")
    with pytest.raises(ValueError, match="Checksum"):
        vendor_asset(path.name)
    path.write_bytes(uf2(0x12345678))
    with pytest.raises(ValueError, match="RP2040"):
        validate_uf2(path)
    path.write_bytes(data + data)
    with pytest.raises(ValueError, match="набор блоков"):
        validate_uf2(path)
    path.write_bytes(data + uf2(0xE48BFF57))
    validate_uf2(path, multi_family=True)
    with pytest.raises(ValueError):
        validate_uf2(path)


def test_bootsel_write_only_targets_selected_volume(tmp_path, monkeypatch):
    root = tmp_path / "bootloader"
    root.mkdir()
    (root / "INFO_UF2.TXT").write_text("Board-ID: RPI-RP2\nModel: Raspberry Pi RP2040", "utf-8")
    monkeypatch.setattr("hid_pilot.firmware_manager.volume_info", lambda _: ("RPI-RP2", 456))
    volume = identify_volume(root)
    source = tmp_path / "firmware.uf2"
    source.write_bytes(uf2())
    asset = Mock(return_value=source)
    monkeypatch.setattr("hid_pilot.firmware_manager.vendor_asset", asset)
    flash_runtime(volume, "raspberry_pi_pico", erase=True)
    asset.assert_called_once_with("flash_nuke.uf2")
    assert (root / "HIDPILOT.UF2").read_bytes() == uf2()


def test_verify_checks_hashes_and_protocol_without_arm_or_input(board, monkeypatch):
    volume, _serial = board
    for name in FIRMWARE_FILES:
        (volume.root / name).write_bytes(resource_path(f"firmware/{name}").read_bytes())
    transport = Mock(protocol="HID_PILOT_V3")
    transport.command.return_value = "DISARMED"
    factory = Mock(return_value=transport)
    monkeypatch.setattr("hid_pilot.firmware_manager.PicoTransport", factory)
    monkeypatch.setattr("hid_pilot.firmware_manager.pico_ports", lambda uid: ["COM77"])
    port, report = verify_firmware(volume, "COM77")
    assert port == "COM77" and "SHA256" in report
    transport.command.assert_called_once_with("STATUS", "DISARMED")
    transport.arm.assert_not_called()
    transport.hold.assert_not_called()
    transport.close.assert_called_once()
    factory.reset_mock()
    (volume.root / "code.py").write_bytes(b"changed")
    with pytest.raises(ValueError, match="отличается"):
        verify_firmware(volume, "COM77")
    factory.assert_not_called()


def test_verify_rejects_port_belonging_to_another_pico(board, monkeypatch):
    volume, _serial = board
    for name in FIRMWARE_FILES:
        (volume.root / name).write_bytes(resource_path(f"firmware/{name}").read_bytes())
    factory = Mock()
    monkeypatch.setattr("hid_pilot.firmware_manager.PicoTransport", factory)
    monkeypatch.setattr("hid_pilot.firmware_manager.pico_ports", lambda uid: ["COM77"])
    with pytest.raises(ValueError, match="UID"):
        verify_firmware(volume, "COM88")
    factory.assert_not_called()


def test_port_discovery_matches_usb_identity(monkeypatch):
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [
        Mock(device="COM1", vid=None, serial_number="A"),
        Mock(device="COM2", vid=0x239A, serial_number="A"),
        Mock(device="COM3", vid=0x239A, serial_number="B"),
    ])
    assert pico_ports("A") == ["COM2"]
    assert pico_ports() == ["COM2", "COM3"]
