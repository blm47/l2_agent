import os
import time
from unittest.mock import Mock

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from l2_agent.bar_detection import BarProposal, DetectionResult
from l2_agent.capture import CapturedFrame, CaptureStatus
from l2_agent.geometry import ClientRect
from l2_agent.gui import KEY_TESTS, MOUSE_TESTS, MainWindow
from l2_agent.roi import NormalizedBox, RoiProfile, load_profile
from l2_agent.windows import WindowSnapshot
from l2_agent.world_state import NameObservation


@pytest.mark.parametrize("pico_port", [None, "COM99"])
def test_gui_tracks_window_and_handles_disappearance(monkeypatch, tmp_path, pico_port):
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
    monkeypatch.setattr("l2_agent.gui.GameWindowManager", lambda: manager)
    capture = Mock()
    capture.latest.return_value = CaptureStatus()
    monkeypatch.setattr("l2_agent.gui.CaptureWorker", lambda: capture)
    bar_worker = Mock()
    bar_worker.take_result.return_value = None
    bar_worker.submit.return_value = False
    monkeypatch.setattr("l2_agent.gui.BarDetectionWorker", lambda: bar_worker)
    controller = Mock(armed=False, state="STOP", epoch=0)
    hotkey = Mock(available=True, error="")
    controller_factory = Mock(return_value=controller)
    pico_factory = Mock()
    monkeypatch.setattr("l2_agent.gui.ActionController", controller_factory)
    monkeypatch.setattr("l2_agent.gui.PicoTransport", pico_factory)
    monkeypatch.setattr("l2_agent.gui.HardStopHotkey", lambda callback: hotkey)
    window = MainWindow(roi_path=tmp_path / "roi.json", pico_port=pico_port)
    controller_factory.assert_called_once_with(
        pico=pico_factory.return_value if pico_port else None
    )
    if pico_port:
        pico_factory.assert_called_once_with(pico_port)
    else:
        pico_factory.assert_not_called()
    try:
        window.show()
        app.processEvents()
        assert "800 × 600" in window.status.text()
        assert "Фокус: Lineage 2 / LU4 / Parsec" in window.status.text()
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
        assert window.world_state.frame_id == 1
        assert window.world_state.player.hp.value is None
        window.dataset_path = tmp_path / "dataset"
        window.schedule_dataset_sample()
        assert window.dataset_timer.isActive()
        window.save_dataset_sample()
        assert len(list(window.dataset_path.glob("*/sample.json"))) == 1
        window.schedule_dataset_sample()
        manager.snapshot.return_value = snapshot.model_copy(update={"focused": False})
        window.save_dataset_sample()
        assert "не сохранён" in window.dataset_status.text()
        assert len(list(window.dataset_path.glob("*/sample.json"))) == 1
        manager.snapshot.return_value = snapshot
        profile = RoiProfile(
            frame_width=800,
            frame_height=600,
            bars={"hp": NormalizedBox(x1=0.1, y1=0.1, x2=0.3, y2=0.12)},
        )
        frame = capture.latest.return_value.frame
        frame.rgb[60:72, 80:240] = (190, 30, 45)
        window.roi_profile = profile
        window.update_preview()
        assert window.world_state.player.hp.value == pytest.approx(1.0)
        assert "HP: ≈100%" in window.world_status.text()
        assert "confidence=" in window.world_status.toolTip()
        old_timestamp = frame.timestamp
        capture.latest.return_value = CaptureStatus(
            frame=frame.model_copy(update={"timestamp": time.monotonic() - 2})
        )
        window.update_preview()
        assert window.world_state.player.hp.value is None
        capture.latest.return_value = CaptureStatus(
            frame=frame.model_copy(update={"timestamp": old_timestamp})
        )
        editor = Mock()
        editor.exec.return_value = 1
        editor.profile.return_value = profile
        monkeypatch.setattr("l2_agent.gui.RoiEditor", lambda *_args: editor)
        window.configure_rois()
        controller.stop.assert_called_with("PAUSE — настройка ROI")
        assert load_profile(tmp_path / "roi.json") == profile
        assert window.preview.boxes == profile.bars
        editor.deleteLater.assert_called_once()
        window.retry_auto_bars()
        proposal = BarProposal(
            profile=profile,
            evidence=["Найдены полосы"],
            score=0.9,
            target_options=[NormalizedBox(x1=0.5, y1=0.1, x2=0.8, y2=0.12)],
        )
        bar_worker.take_result.return_value = (
            window._auto_generation,
            capture.latest.return_value.frame,
            DetectionResult(proposals=[proposal], message="Подтвердите полосы"),
        )
        controller.armed = True
        calls_before = bar_worker.take_result.call_count
        window.update_auto_bars(capture.latest.return_value.frame)
        assert bar_worker.take_result.call_count == calls_before
        controller.armed = False
        window.update_preview()
        bar_worker.take_result.return_value = None
        assert window.confirm_bars_button.isEnabled()
        assert window.world_state.player.hp.value is None
        assert "target_hp" not in load_profile(tmp_path / "roi.json").bars
        window.target_bar_selector.setCurrentIndex(1)
        assert "target_hp" in window.preview.boxes
        window.confirm_auto_bars()
        assert "target_hp" in load_profile(tmp_path / "roi.json").bars
        assert not window._proposals
        with monkeypatch.context() as name_patch:
            name_patch.setattr(
                window.target_reader,
                "observe",
                Mock(
                    return_value=NameObservation(
                        value="Gremlin",
                        confidence=0.95,
                        frame_id=1,
                        timestamp=time.monotonic(),
                    )
                ),
            )
            window.update_preview()
            assert window.world_state.target_name.value == "Gremlin"
            assert "Gremlin" in window.world_status.text()
            assert "confidence=0.95" in window.world_status.toolTip()
        window.retry_auto_bars()
        bar_worker.take_result.return_value = (
            window._auto_generation - 1,
            capture.latest.return_value.frame,
            DetectionResult(proposals=[proposal], message="Старый результат"),
        )
        window.update_preview()
        bar_worker.take_result.return_value = None
        assert not window._proposals
        window.auto_bars.setChecked(False)
        capture.latest.return_value = CaptureStatus(message="Preview выключен")
        window.update_preview()
        assert window.preview.text() == "Preview выключен"
        assert window.world_state is None
        assert not window.preview.bar_text
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
        for label, (key, duration) in KEY_TESTS.items():
            controller.key_down.reset_mock()
            window.test_selector.setCurrentText(label)
            window.schedule_test()
            window.test_timer.stop()
            window.run_test()
            controller.key_down.assert_called_once_with(key, duration)
        for label, (button, duration) in MOUSE_TESTS.items():
            controller.mouse_button_down.reset_mock()
            window.test_selector.setCurrentText(label)
            window.schedule_test()
            window.test_timer.stop()
            window.run_test()
            controller.mouse_button_down.assert_called_once_with(button, duration)
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
        bar_worker.close.assert_called_once()
        controller.close.assert_called_once()
        hotkey.close.assert_called_once()
