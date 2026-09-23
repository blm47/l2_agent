import os
import time
from unittest.mock import Mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from l2_agent.capture import CapturedFrame, CaptureStatus
from l2_agent.geometry import ClientRect
from l2_agent.gui import MainWindow
from l2_agent.windows import WindowSnapshot


def test_gui_tracks_window_and_handles_disappearance(monkeypatch):
    app = QApplication.instance() or QApplication([])
    snapshot = WindowSnapshot(
        hwnd=42,
        pid=123,
        title="Parsec",
        minimized=False,
        focused=True,
        rect=ClientRect(left=10, top=20, right=810, bottom=620),
    )
    manager = Mock()
    manager.discover.return_value = [snapshot]
    manager.snapshot.return_value = snapshot
    monkeypatch.setattr("l2_agent.gui.ParsecWindowManager", lambda: manager)
    capture = Mock()
    capture.latest.return_value = CaptureStatus()
    monkeypatch.setattr("l2_agent.gui.CaptureWorker", lambda: capture)
    controller = Mock(armed=False, state="STOP", epoch=0)
    hotkey = Mock(available=True, error="")
    monkeypatch.setattr("l2_agent.gui.ActionController", lambda: controller)
    monkeypatch.setattr("l2_agent.gui.HardStopHotkey", lambda callback: hotkey)
    window = MainWindow()
    try:
        window.show()
        app.processEvents()
        assert "800 × 600" in window.status.text()
        assert "Фокус: Parsec" in window.status.text()
        capture.select.assert_called_with((42, 123))
        capture.latest.return_value = CaptureStatus(
            frame=CapturedFrame(
                sequence=1,
                timestamp=time.monotonic(),
                rect=snapshot.rect,
                rgb=np.zeros((600, 800, 3), dtype=np.uint8),
            )
        )
        window.update_preview()
        assert not window.preview.pixmap().isNull()
        capture.latest.return_value = CaptureStatus(message="Preview выключен")
        window.update_preview()
        assert window.preview.text() == "Preview выключен"
        window.preview_enabled.setChecked(False)
        capture.select.assert_called_with(None)
        for button in window.findChildren(QPushButton):
            if button.text() in {"START", "PAUSE", "STOP"}:
                assert button.isEnabled()
        window.start_input()
        controller.arm.assert_called_with((42, 123))
        controller.armed = True
        window.schedule_test()
        assert window.test_timer.isActive()
        controller.epoch = 1
        window.run_test()
        controller.mouse_move_absolute_client.assert_not_called()
        window.stop_input("STOP")
        controller.stop.assert_called_with("STOP")
        window.test_selector.setCurrentText("Клавиша R — 100 мс")
        window.schedule_test()
        window.test_timer.stop()
        window.run_test()
        controller.key_down.assert_called_once_with("R", 100)
        controller.armed = False
        manager.snapshot.side_effect = ValueError("Окно закрыто")
        window.update_status()
        assert "окно недоступно" in window.status.text()
        manager.discover.return_value = []
        window.refresh_windows()
        assert "не найдены" in window.status.text()
    finally:
        window.timer.stop()
        window.close()
        capture.close.assert_called_once()
        controller.close.assert_called_once()
        hotkey.close.assert_called_once()
