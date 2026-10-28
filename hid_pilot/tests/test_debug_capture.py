import json
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.debug_capture import encode_frame, main, save_frame
from hid_pilot.desktop import FocusLost, Target


def profile():
    box = BarBox(x1=.1, y1=.1, x2=.5, y2=.3)
    return BarProfile(width=200, height=100, bars={"target_hp": box},
                      own_level=box, target_level=box, target_panel=box)


def test_capture_preserves_level_and_target_crops_losslessly():
    image = np.random.default_rng(1).integers(0, 256, (100, 200, 3), dtype=np.uint8)
    config = profile()
    result = encode_frame(image, config)
    assert set(result) == {"window.jpg", "bar_target_hp.png", "own_level.png",
                           "target_level.png", "target_panel.png"}
    crop = cv2.imdecode(np.frombuffer(result["own_level.png"], np.uint8), cv2.IMREAD_COLOR)
    assert np.array_equal(crop, config.crop(image, config.own_level))


def test_storage_limit_does_not_leave_partial_frame(tmp_path):
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    assert save_frame(tmp_path, 0, image, profile(), 1) == 0
    assert list(tmp_path.iterdir()) == []
    assert save_frame(tmp_path, 0, image, profile(), 100_000) > 0
    assert (tmp_path / "000000" / "target_level.png").exists()


def test_wrong_profile_size_is_rejected():
    with pytest.raises(ValueError, match="Размер окна"):
        encode_frame(np.zeros((200, 200, 3), dtype=np.uint8), profile())


@pytest.mark.parametrize("lost_focus", [False, True])
def test_capture_cli_records_timestamps_and_profile(monkeypatch, tmp_path, lost_focus):
    target = Target(hwnd=1, pid=2, title="LU4 - Test", rect=(0, 0, 200, 100))
    now = [0.]
    monkeypatch.setattr("hid_pilot.debug_capture.windows", lambda: [target])
    monkeypatch.setattr("hid_pilot.debug_capture.setup_logging", Mock())
    monkeypatch.setattr("hid_pilot.debug_capture.time.monotonic", lambda: now[0])
    monkeypatch.setattr("hid_pilot.debug_capture.time.sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    desktop = Mock()
    desktop.capture.return_value = np.zeros((100, 200, 3), dtype=np.uint8)
    if lost_focus:
        desktop.capture.side_effect = [FocusLost("Саппорт активен")] + [desktop.capture.return_value] * 4
    monkeypatch.setattr("hid_pilot.debug_capture.Desktop", Mock(return_value=desktop))
    monkeypatch.setattr("sys.argv", ["debug_capture", "--title", "Test", "--output", str(tmp_path),
                                    "--seconds", "1", "--fps", "5"])
    assert main() == 0
    directory = next(tmp_path.iterdir())
    rows = [json.loads(line) for line in (directory / "frames.jsonl").read_text("utf-8").splitlines()]
    assert len(rows) == (4 if lost_focus else 5)
    assert rows[0]["captured_at"]
    desktop.capture.assert_called()
    desktop.activate.assert_not_called()
