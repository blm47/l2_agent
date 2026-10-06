import os
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from hid_pilot.firmware_manager import PicoVolume
from hid_pilot.firmware_panel import FirmwareDialog


def dialog_harness(monkeypatch, kind="circuitpython"):
    app = QApplication.instance() or QApplication([])
    volume = PicoVolume(root=Path("X:/"), serial=123, kind=kind,
                        board="raspberry_pi_pico" if kind == "circuitpython" else "rp2040")
    monkeypatch.setattr("hid_pilot.firmware_panel.discover_volumes", lambda: [volume])
    monkeypatch.setattr("hid_pilot.firmware_panel.pico_ports", lambda: ["COM77"])
    dialog = FirmwareDialog(lambda: True, "COM77")
    app.processEvents()
    return dialog, volume


def test_opening_menu_never_flashes_and_cancelled_confirmation_does_not_write(monkeypatch):
    flash = Mock()
    install = Mock()
    monkeypatch.setattr("hid_pilot.firmware_panel.flash_runtime", flash)
    monkeypatch.setattr("hid_pilot.firmware_panel.install_scripts", install)
    dialog, _volume = dialog_harness(monkeypatch)
    assert dialog.selected_port() == "COM77"
    dialog.confirm = Mock(return_value=False)
    dialog.install()
    flash.assert_not_called()
    install.assert_not_called()
    dialog.reject()


def test_full_reset_requires_checkbox_confirmation_and_bootsel(monkeypatch):
    dialog, volume = dialog_harness(monkeypatch, "bootloader")
    flash = Mock()
    monkeypatch.setattr("hid_pilot.firmware_panel.flash_runtime", flash)
    dialog.run_job = Mock(side_effect=lambda title, job: job())
    dialog.confirm = Mock(return_value=True)
    dialog.reset_board()
    dialog.run_job.assert_not_called()
    dialog.erase_ack.setChecked(True)
    dialog.reset_board()
    flash.assert_called_once_with(volume, "raspberry_pi_pico", erase=True)
    dialog.reject()


def test_running_script_blocks_firmware_job_and_busy_dialog_cannot_close(monkeypatch):
    dialog, _volume = dialog_harness(monkeypatch)
    dialog.available = lambda: False
    job = Mock()
    dialog.run_job("test", job)
    job.assert_not_called()
    assert not dialog.busy
    dialog.show()
    dialog.busy = True
    dialog.reject()
    assert dialog.isVisible()
    dialog.close()
    assert dialog.isVisible()
    dialog.busy = False
    dialog.reject()


def test_worker_reports_verification_and_returns_confirmed_port(monkeypatch):
    dialog, volume = dialog_harness(monkeypatch)
    verify = Mock(return_value=("COM77", "Verified"))
    monkeypatch.setattr("hid_pilot.firmware_panel.verify_firmware", verify)
    dialog.verify()
    for _ in range(100):
        QTest.qWait(10)
        if not dialog.busy:
            break
    assert not dialog.busy
    assert dialog.verified_port == "COM77"
    assert dialog.status.text() == "Verified"
    verify.assert_called_once_with(volume, "COM77")
    dialog.reject()
