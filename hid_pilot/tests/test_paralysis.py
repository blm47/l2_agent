import threading
import time
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.paralysis import ParalysisDetector, ParalysisProfile
from hid_pilot.party import PartyProfile
from hid_pilot.train_observer import TrainObserver, TrainReader


def scene(tmp_path):
    width, height = 600, 800

    def box(y1, y2):
        return BarBox(x1=20 / width, x2=220 / width, y1=y1 / height, y2=y2 / height)

    party = PartyProfile(width=width, height=height, row_step=80,
                         name=box(0, 20), cp=box(22, 26), hp=box(28, 34), mp=box(36, 42))
    own = BarProfile(width=width, height=height,
                     bars={"hp": box(700, 706), "mp": box(710, 716), "cp": box(720, 726)})
    reference = Path(__file__).parents[1] / "extensions" / "assets" / "paralysis.bmp"
    profile = ParalysisProfile(width=width, height=height, template=str(reference))
    path = tmp_path / "paralysis.json"
    path.write_text(profile.model_dump_json(), encoding="utf-8")
    detector = ParalysisDetector(path, party)
    image = np.zeros((height, width, 3), dtype=np.uint8)
    for row in range(8):
        y = row * 80
        image[y + 22:y + 26, 20:220] = (0, 150, 220)
        image[y + 28:y + 34, 20:220] = (0, 0, 180)
        image[y + 36:y + 42, 20:220] = (180, 0, 0)
    image[700:706, 20:220] = (0, 0, 180)
    image[710:716, 20:220] = (180, 0, 0)
    image[720:726, 20:220] = (0, 150, 220)
    return image, own, party, detector


def insert_icon(image, detector, slot, size=22):
    icon = cv2.resize(detector.template, (size, round(detector.template.shape[0] * size / detector.template.shape[1])),
                      interpolation=cv2.INTER_AREA)
    y = (slot - 1) * 80 + 1
    image[y:y + icon.shape[0], 240:240 + icon.shape[1]] = icon


@pytest.mark.parametrize("size", [20, 22, 24, 28])
def test_paralysis_reference_detects_correct_rows_at_multiple_sizes(tmp_path, size):
    image, _own, _party, detector = scene(tmp_path)
    insert_icon(image, detector, 2, size)
    insert_icon(image, detector, 7, size)
    assert detector.read(image, tuple(range(1, 9))) == (2, 7)


def test_other_icons_and_icons_outside_party_band_do_not_match(tmp_path):
    image, _own, _party, detector = scene(tmp_path)
    image[1:24, 240:264] = (0, 150, 220)
    insert_icon(image, detector, 7)
    assert detector.read(image, (1, 2, 3)) == ()


def test_reference_resolution_does_not_restrict_current_party_window(tmp_path):
    image, _own, party, original = scene(tmp_path)
    path = tmp_path / "paralysis.json"
    reference = original.profile.model_copy(update={"width": 3840, "height": 2160})
    path.write_text(reference.model_dump_json(), encoding="utf-8")
    detector = ParalysisDetector(path, party)
    assert (detector.profile.width, detector.profile.height) == (party.width, party.height)
    insert_icon(image, detector, 3)
    assert detector.read(image, (1, 2, 3)) == (3,)


def test_high_resolution_icon_is_scaled_to_actual_debuff_size(tmp_path):
    image, _own, party, original = scene(tmp_path)
    enlarged = cv2.resize(original.template, (276, 268), interpolation=cv2.INTER_NEAREST)
    path = tmp_path / "large.png"
    assert cv2.imwrite(str(path), enlarged)
    profile = original.profile.model_copy(update={"width": 3840, "height": 2160, "template": path.name})
    config = tmp_path / "large.json"
    config.write_text(profile.model_dump_json(), encoding="utf-8")
    detector = ParalysisDetector(config, party)
    insert_icon(image, original, 6, 22)
    assert detector.read(image, (1, 6)) == (6,)


def test_dark_icon_corners_can_contain_game_background(tmp_path):
    image, _own, _party, detector = scene(tmp_path)
    insert_icon(image, detector, 7)
    icon = image[481:502, 240:262]
    mask = np.max(icon, axis=2) <= 30
    icon[mask] = (80, 110, 90)
    assert detector.read(image, (7,)) == (7,)


def test_reader_samples_party_without_calling_or_waiting_for_ocr(tmp_path, monkeypatch):
    image, own, party, detector = scene(tmp_path)
    ocr = Mock()
    ocr.read.side_effect = AssertionError("OCR не должен задерживать полосы")
    monkeypatch.setattr("hid_pilot.numbers.shared_numbers", lambda: ocr)
    own.numbers = own.bars.copy()
    insert_icon(image, detector, 7)
    reader = TrainReader(own, party, detector)
    first = reader.read(image, 1.)
    assert (first.hp, first.mp, first.cp) == (1., 1., 1.)
    assert first.paralyzed_slots == (7,)
    image[7 * 80 + 28:7 * 80 + 34, 60:220] = 0
    second = reader.read(image, 1.1)
    assert second.members[-1].hp == .2
    assert first.members[-1].hp == 1.
    ocr.read.assert_not_called()


def test_missing_own_bars_does_not_reuse_stale_ocr(tmp_path, monkeypatch):
    image, own, party, detector = scene(tmp_path)
    own.numbers = own.bars.copy()
    own.bars = {}
    reader = TrainReader(own, party, detector)
    reader.own_values = {"hp": 1., "mp": 1., "cp": 1.}
    reader.own_at = 1.
    monkeypatch.setattr("hid_pilot.train_observer.time.monotonic", lambda: 2.)
    assert reader.read(image, 2.).hp is None
    monkeypatch.setattr("hid_pilot.train_observer.time.monotonic", lambda: 1.3)
    assert reader.read(image, 1.3).hp == 1.


def test_resized_frame_is_rejected(tmp_path):
    image, _own, _party, detector = scene(tmp_path)
    with pytest.raises(RuntimeError, match="Размер"):
        detector.read(image[:700], (1,))


def test_fast_observer_keeps_sampling_while_own_ocr_is_busy(tmp_path, monkeypatch):
    image, own, party, detector = scene(tmp_path)
    reader = TrainReader(own, party, detector)
    reader.identities.update = Mock()
    desktop = Mock()
    desktop.capture.return_value = image
    ocr_busy = threading.Event()
    unblock_ocr = threading.Event()

    def prepare():
        ocr_busy.set()
        unblock_ocr.wait(2)

    ocr = Mock()
    ocr.prepare.side_effect = prepare
    ocr.lock = threading.Lock()
    monkeypatch.setattr("hid_pilot.healer_observer.shared_numbers", lambda: ocr)
    observer = TrainObserver(desktop, reader, lambda: .1, lambda: False)
    observer.start()
    try:
        assert ocr_busy.wait(1)
        limit = time.monotonic() + 1
        while len(observer.snapshots()) < 4 and time.monotonic() < limit:
            time.sleep(.02)
        samples = observer.snapshots()
        assert len(samples) >= 4
        assert samples[3].at - samples[0].at < .5
        assert not unblock_ocr.is_set()
    finally:
        observer.stop_event.set()
        unblock_ocr.set()
        observer.close()
