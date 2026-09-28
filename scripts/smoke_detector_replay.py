import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2
from PySide6.QtWidgets import QApplication

from l2_agent.capture import CapturedFrame, CaptureStatus
from l2_agent.detector import DEFAULT_DETECTOR_PATH
from l2_agent.geometry import ClientRect
from l2_agent.gui import MainWindow
from l2_agent.logging_setup import setup_logging
from l2_agent.windows import WindowSnapshot
from smoke_preview import read_memory


def main() -> int:
    logger = setup_logging()
    images = [cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB) for p in sorted(
        (ROOT / "datasets/detector/export-taxonomy-v2-20260927-final/images/val").glob("*.png")
    )]
    if not images:
        raise ValueError("Нет кадров для replay")
    height, width = images[0].shape[:2]
    rect = ClientRect(left=0, top=0, right=width, bottom=height)
    snapshot = WindowSnapshot(hwnd=42, pid=123, title="Replay", focused=True, minimized=False, rect=rect)
    manager = Mock()
    manager.discover.return_value = [snapshot]
    manager.snapshot.return_value = snapshot
    capture = Mock()
    capture.latest.return_value = CaptureStatus()
    controller = Mock(armed=False, epoch=0, state="STOP", backend_name="Replay без ввода")
    hotkey = Mock(available=False, error="Replay без ввода")
    app = QApplication.instance() or QApplication([])
    output = ROOT / "artifacts/detector-live"
    output.mkdir(parents=True, exist_ok=True)
    with patch("l2_agent.gui.GameWindowManager", return_value=manager), \
         patch("l2_agent.gui.CaptureWorker", return_value=capture), \
         patch("l2_agent.gui.ActionController", return_value=controller), \
         patch("l2_agent.gui.HardStopHotkey", return_value=hotkey):
        window = MainWindow(detector_path=DEFAULT_DETECTOR_PATH)
        window.auto_bars.setChecked(False)
        window.show()
        start = previous = time.monotonic()
        gaps, inference, sources, memory = [], [], set(), []
        sequence = 0
        try:
            while time.monotonic() - start < 20:
                now = time.monotonic()
                sequence += 1
                capture.latest.return_value = CaptureStatus(
                    frame=CapturedFrame(sequence=sequence, timestamp=now, rect=rect,
                                        rgb=images[int(now - start) % len(images)]), fps=30,
                    message="Replay сохранённых кадров",
                )
                app.processEvents()
                gaps.append(time.monotonic() - previous)
                previous = time.monotonic()
                state = window.world_state
                if state and state.detected_objects.ready:
                    observed = state.detected_objects
                    if observed.frame_id not in sources:
                        sources.add(observed.frame_id)
                        inference.append(observed.inference_ms)
                    if len(sources) == 10:
                        window.grab().save(str(output / "gui-replay.png"))
                memory.append(read_memory()["WorkingSetSize"] / 2**20)
                time.sleep(.01)
            report = {"mode": "saved-frame replay; mocked capture and input", "seconds": time.monotonic() - start,
                      "distinct_detector_results": len(sources), "inference_ms": inference,
                      "max_event_loop_gap_ms": max(gaps) * 1000, "max_rss_mb": max(memory)}
            (output / "gui-replay.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            logger.info("Replay проверен: результатов=%s, max gap=%.0f мс", len(sources), max(gaps) * 1000)
            return 0 if len(sources) >= 10 else 1
        finally:
            window.close()


if __name__ == "__main__":
    raise SystemExit(main())
