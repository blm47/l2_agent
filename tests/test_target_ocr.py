import threading
import time
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from l2_agent.bar_parser import perceive_bars
from l2_agent.capture import CapturedFrame
from l2_agent.geometry import ClientRect
from l2_agent.ocr import RapidOCRProvider, TextObservation
from l2_agent.roi import NormalizedBox, RoiProfile
from l2_agent.target_ocr import (
    TargetNameReader,
    select_name,
    target_header,
    target_panel_visible,
    text_fingerprint,
)
from l2_agent.world_state import NameObservation, WorldState


def line(text="Gremlin", score=0.98):
    return TextObservation(
        text=text,
        score=score,
        box=NormalizedBox(x1=0.1, y1=0.1, x2=0.9, y2=0.8),
    )


def frame_and_profile():
    rgb = cv2.cvtColor(
        cv2.imread(str(Path(__file__).parent / "fixtures/target/self.png")), cv2.COLOR_BGR2RGB
    )
    return (
        CapturedFrame(
            sequence=1,
            timestamp=time.monotonic(),
            rect=ClientRect(left=0, top=0, right=410, bottom=115),
            rgb=rgb,
        ),
        RoiProfile(
            frame_width=410,
            frame_height=115,
            bars={"target_hp": NormalizedBox(x1=82 / 410, y1=89 / 115, x2=395 / 410, y2=104 / 115)},
        ),
    )


def test_name_has_own_frame_metadata_and_roundtrips():
    result = select_name([line()], 7, 100)
    assert result.value == "Gremlin"
    state = WorldState(frame_id=8, timestamp=100.2, target_name=result)
    assert WorldState.model_validate_json(state.model_dump_json()).target_name.frame_id == 7


@pytest.mark.parametrize(
    "lines",
    [[], [line(score=0.4)], [line("58/58")], [line("Gremlin"), line("Other")], [line("@@")]],
)
def test_ambiguous_or_weak_text_is_unknown(lines):
    result = select_name(lines, 1, 0)
    assert result.value is None
    assert result.confidence == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"value": "Gremlin", "confidence": 0.9},
        {"value": None, "confidence": 0.9},
        {"value": " ", "confidence": 0.9, "frame_id": 1, "timestamp": 1},
    ],
)
def test_name_rejects_missing_evidence(kwargs):
    with pytest.raises(ValidationError):
        NameObservation(**kwargs)


def test_provider_normalizes_boxes_and_converts_rgb():
    provider = RapidOCRProvider()
    provider._engine = Mock(
        return_value=(
            [([[-2, 2], [18, 2], [18, 8], [-2, 8]], "Gremlin", 0.9)],
            [],
        )
    )
    rgb = np.full((10, 20, 3), (200, 50, 10), np.uint8)
    result = provider.recognize(rgb)
    assert result[0].box.x1 == 0
    assert result[0].box.x2 == 0.9
    assert tuple(provider._engine.call_args.args[0][0, 0]) == (10, 50, 200)


def test_header_requires_target_profile_and_valid_size():
    frame, profile = frame_and_profile()
    assert target_header(frame, profile) is not None
    assert target_header(frame, profile.model_copy(update={"frame_width": 800})) is None
    assert target_header(frame, profile.model_copy(update={"bars": {}})) is None


def test_async_result_is_cleared_on_changed_pixels_and_target_loss():
    frame, profile = frame_and_profile()
    provider = Mock()
    provider.recognize.return_value = [line()]
    reader = TargetNameReader(provider)
    try:
        assert reader.observe(frame, profile, True, (1, 2)).value is None
        reader._thread.join(timeout=2)
        assert reader.observe(frame, profile, True, (1, 2)).value == "Gremlin"
        frame.rgb[:] = 255
        assert reader.observe(frame, profile, True, (1, 2)).value is None
        assert reader.observe(frame, profile, False, (1, 2)).value is None
        assert reader._cached is None
    finally:
        reader.close()


def test_late_result_cannot_cross_window_change():
    frame, profile = frame_and_profile()
    release = threading.Event()
    provider = Mock()
    provider.recognize.side_effect = lambda _: (release.wait(2), [line()])[1]
    reader = TargetNameReader(provider)
    try:
        reader.observe(frame, profile, True, (1, 2))
        reader.observe(frame, profile, True, (3, 4))
        release.set()
        reader._thread.join(timeout=2)
        assert reader.observe(frame, profile, True, (3, 4)).value is None
        assert provider.recognize.call_count == 1
    finally:
        release.set()
        reader.close()


def test_stale_result_expires_even_if_header_unchanged(monkeypatch):
    frame, profile = frame_and_profile()
    provider = Mock()
    provider.recognize.return_value = [line()]
    reader = TargetNameReader(provider)
    try:
        reader.observe(frame, profile, True, 1)
        reader._thread.join(timeout=2)
        assert reader.observe(frame, profile, True, 1).value == "Gremlin"
        now = frame.timestamp + 2
        monkeypatch.setattr("l2_agent.target_ocr.time.monotonic", lambda: now)
        reader._next_submit = now + 10
        newer = frame.model_copy(update={"sequence": 2, "timestamp": now})
        assert reader.observe(newer, profile, True, 1).value is None
    finally:
        reader.close()


def test_self_target_without_hp_still_has_name():
    frame, profile = frame_and_profile()
    assert perceive_bars(frame, profile).target_hp.value is None
    assert target_panel_visible(frame, profile)
    provider = Mock()
    provider.recognize.return_value = [line("LenaBerkova")]
    reader = TargetNameReader(provider)
    try:
        reader.observe(frame, profile, True, 1)
        reader._thread.join(timeout=2)
        assert reader.observe(frame, profile, True, 1).value == "LenaBerkova"
        frame.rgb[15:40, 260:290] = (30, 32, 34)
        assert reader.observe(frame, profile, True, 1).value == "LenaBerkova"
        frame.rgb[:] = 0
        assert reader.observe(frame, profile, True, 1).value is None
    finally:
        reader.close()


def test_fingerprint_ignores_dark_background_but_detects_other_text():
    first = np.zeros((30, 100, 3), np.uint8)
    first[5:20, 20:40] = 220
    second = first.copy()
    second[:, 60:] = (40, 60, 20)
    assert text_fingerprint(first) == text_fingerprint(second)
    second[5:20, 45:50] = 220
    assert text_fingerprint(first) != text_fingerprint(second)


def test_panel_anchor_present_for_mob_and_absent_on_blank_frame():
    frame, profile = frame_and_profile()
    assert target_panel_visible(frame, profile)
    rgb = cv2.cvtColor(
        cv2.imread(str(Path(__file__).parent / "fixtures/target/gremlin.png")), cv2.COLOR_BGR2RGB
    )
    assert target_panel_visible(frame.model_copy(update={"rgb": rgb}), profile)
    assert not target_panel_visible(frame.model_copy(update={"rgb": np.zeros_like(rgb)}), profile)
