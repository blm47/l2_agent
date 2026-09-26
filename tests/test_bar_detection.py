import time
from unittest.mock import Mock

import numpy as np
import pytest

from l2_agent.bar_detection import (
    AutoBarDetector,
    DetectionResult,
    TextObservation,
    build_proposals,
    colored_candidates,
)
from l2_agent.bar_worker import BarDetectionWorker
from l2_agent.capture import CapturedFrame
from l2_agent.geometry import ClientRect
from l2_agent.roi import NormalizedBox


def panel() -> np.ndarray:
    rgb = np.zeros((500, 800, 3), dtype=np.uint8)
    rgb[30:40, 50:250] = (230, 200, 20)
    rgb[50:60, 50:190] = (210, 30, 30)
    rgb[70:80, 50:250] = (30, 70, 210)
    return rgb


def test_finds_player_panel_without_manual_regions():
    candidates = colored_candidates(panel())
    result = build_proposals(candidates, [], 800, 500)
    assert len(result.proposals) == 1
    proposal = result.proposals[0]
    assert set(proposal.profile.bars) == {"hp", "mp", "cp"}
    assert proposal.needs_confirmation
    assert proposal.profile.bars["hp"].x2 == pytest.approx(250 / 800)
    assert proposal.profile.bars["hp"].pixels(800, 500) == (50, 50, 250, 60)


def test_labels_from_ocr_raise_evidence_but_do_not_prove_full_bar_width():
    candidates = colored_candidates(panel())
    before = build_proposals(candidates, [], 800, 500).proposals[0]
    texts = [
        TextObservation(text=item.kind.upper() + " 100/100", score=0.96, box=item.box)
        for item in candidates
    ]
    after = build_proposals(candidates, texts, 800, 500).proposals[0]
    assert after.score > before.score
    assert after.needs_confirmation
    assert any("OCR прочитал HP" in value for value in after.evidence)


def test_label_elsewhere_does_not_confirm_region():
    texts = [
        TextObservation(text="HP", score=0.99, box=NormalizedBox(x1=0.8, y1=0.8, x2=0.9, y2=0.9))
    ]
    proposal = build_proposals(colored_candidates(panel()), texts, 800, 500).proposals[0]
    assert not any("OCR" in value for value in proposal.evidence)


def test_full_fraction_preserves_individual_bar_width():
    candidates = colored_candidates(panel())
    hp = next(item for item in candidates if item.kind == "hp")
    text = TextObservation(text="HP 100/100", score=0.98, box=hp.box)
    result = build_proposals(candidates, [text], 800, 500)
    assert result.proposals[0].profile.bars["hp"].x2 == hp.box.x2
    assert result.proposals[0].needs_confirmation


@pytest.mark.parametrize("text,score", [("HP 70/100", 0.98), ("HP 100/100", 0.5), ("0/0", 0.99)])
def test_partial_or_uncertain_fraction_does_not_prove_full_width(text, score):
    candidates = colored_candidates(panel())
    hp = next(item for item in candidates if item.kind == "hp")
    observation = TextObservation(text=text, score=score, box=hp.box)
    proposal = build_proposals(candidates, [observation], 800, 500).proposals[0]
    assert proposal.profile.bars["hp"].x2 == pytest.approx(250 / 800)


def test_red_world_object_is_not_player_panel():
    rgb = np.zeros((500, 800, 3), dtype=np.uint8)
    rgb[100:115, 100:400] = (220, 30, 30)
    assert not build_proposals(colored_candidates(rgb), [], 800, 500).proposals


def test_empty_image_does_not_load_model():
    detector = AutoBarDetector()
    assert not detector.detect(np.zeros((100, 100, 3), dtype=np.uint8)).proposals
    assert detector._ocr is None


def test_model_observations_are_used_without_network():
    detector = AutoBarDetector()
    detector._ocr = Mock()
    detector._ocr.recognize.return_value = [
        TextObservation(
            text="HP 10/10",
            score=0.95,
            box=NormalizedBox(x1=100 / 580, y1=80 / 180, x2=200 / 580, y2=100 / 180),
        )
    ]
    result = detector.detect(panel())
    assert result.proposals
    assert any("OCR прочитал HP" in line for line in result.proposals[0].evidence)
    assert detector._ocr.recognize.call_args.args[0].shape[:2] == (180, 580)


def test_extra_red_bar_is_only_a_target_hypothesis():
    rgb = panel()
    rgb[200:210, 400:600] = (210, 30, 30)
    result = build_proposals(colored_candidates(rgb), [], 800, 500)
    assert len(result.proposals) == 1
    assert "target_hp" not in result.proposals[0].profile.bars
    assert result.proposals[0].target_options[0].pixels(800, 500) == (400, 200, 600, 210)


def test_worker_publishes_once_and_keeps_generation(monkeypatch):
    detector = Mock()
    detector.detect.return_value = DetectionResult(message="Нет полос")
    monkeypatch.setattr("l2_agent.bar_worker.AutoBarDetector", lambda: detector)
    worker = BarDetectionWorker()
    frame = CapturedFrame(
        sequence=1,
        timestamp=time.monotonic(),
        rect=ClientRect(left=0, top=0, right=800, bottom=500),
        rgb=panel(),
    )
    try:
        assert worker.submit(7, frame)
        result = None
        deadline = time.monotonic() + 2
        while result is None and time.monotonic() < deadline:
            result = worker.take_result()
            time.sleep(0.01)
        assert result is not None and result[0] == 7 and result[1] is frame
        assert worker.take_result() is None
    finally:
        worker.close()
    assert not worker.submit(8, frame)
