import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from l2_agent.bar_parser import parse_bar, perceive_bars
from l2_agent.capture import CapturedFrame
from l2_agent.geometry import ClientRect
from l2_agent.roi import NormalizedBox, RoiProfile

COLORS = {"hp": (190, 30, 45), "mp": (40, 90, 210), "cp": (220, 180, 20)}


def crop(kind="hp", fraction=0.6):
    rgb = np.full((12, 200, 3), 20, dtype=np.uint8)
    rgb[:, : round(200 * fraction)] = COLORS[kind]
    return rgb


@pytest.mark.parametrize("kind", ["hp", "mp", "cp"])
@pytest.mark.parametrize("fraction", [0.05, 0.25, 0.5, 0.75, 1.0])
def test_fill_fraction(kind, fraction):
    observation = parse_bar(crop(kind, fraction), kind)
    assert observation.value == pytest.approx(fraction, abs=0.01)
    assert 0 < observation.confidence < 1


def test_target_hp_uses_red_without_inventing_identity():
    assert parse_bar(crop(), "target_hp").value == pytest.approx(0.6)


def test_text_gaps_do_not_shorten_bar():
    rgb = crop(fraction=0.8)
    rgb[2:10, 65:70] = 255
    rgb[2:10, 74:79] = 255
    assert parse_bar(rgb, "hp").value == pytest.approx(0.8)


@pytest.mark.parametrize("rgb", [np.zeros((12, 200, 3), np.uint8), crop("mp")])
def test_missing_color_is_unknown_not_zero(rgb):
    result = parse_bar(rgb, "hp")
    assert result.value is None
    assert result.confidence == 0
    assert result.reason


def test_disjoint_colored_objects_are_not_a_bar():
    rgb = crop()
    rgb[:, 40:80] = 0
    assert parse_bar(rgb, "hp").value is None


def test_bar_start_shift_is_rejected():
    rgb = crop()
    rgb[:, :40] = 0
    assert parse_bar(rgb, "hp").value is None


def test_thin_line_is_not_enough_evidence():
    rgb = crop()
    rgb[1:] = 0
    assert parse_bar(rgb, "hp").value is None


def fixture():
    rgb = crop()
    frame = CapturedFrame(
        sequence=12,
        timestamp=100,
        rect=ClientRect(left=0, top=0, right=200, bottom=12),
        rgb=rgb,
    )
    profile = RoiProfile(
        frame_width=200,
        frame_height=12,
        bars={"hp": NormalizedBox(x1=0, y1=0, x2=1, y2=1)},
    )
    return frame, profile


def test_frame_metadata_and_missing_rois():
    frame, profile = fixture()
    state = perceive_bars(frame, profile, now=100.1)
    assert state.frame_id == 12
    assert state.timestamp == 100
    assert state.player.hp.value == pytest.approx(0.6)
    assert state.player.mp.value is None
    assert state.target_hp.value is None


@pytest.mark.parametrize("now", [99, 101.1])
def test_stale_or_future_frame_is_unknown(now):
    frame, profile = fixture()
    assert perceive_bars(frame, profile, now=now).player.hp.value is None


def test_resize_and_missing_profile_are_unknown():
    frame, profile = fixture()
    profile = profile.model_copy(update={"frame_width": 400})
    assert perceive_bars(frame, profile, now=100).player.hp.value is None
    assert perceive_bars(frame, None, now=100).player.hp.value is None


def test_absent_bar_does_not_reuse_previous_value():
    frame, profile = fixture()
    assert perceive_bars(frame, profile, now=100).player.hp.value is not None
    frame.rgb[:] = 0
    assert perceive_bars(frame, profile, now=100).player.hp.value is None


FIXTURES = Path(__file__).parent / "fixtures" / "bars"
SAMPLES = json.loads((FIXTURES / "labels.json").read_text(encoding="utf-8"))["samples"]


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample["kind"])
def test_labeled_lu4_crops(sample):
    rgb = cv2.cvtColor(cv2.imread(str(FIXTURES / sample["file"])), cv2.COLOR_BGR2RGB)
    result = parse_bar(rgb, sample["kind"])
    assert result.value == pytest.approx(sample["value"], abs=0.02)


def test_occluded_real_bar_is_unknown():
    rgb = cv2.cvtColor(cv2.imread(str(FIXTURES / "hp.png")), cv2.COLOR_BGR2RGB)
    rgb[:, 80:180] = 128
    assert parse_bar(rgb, "hp").value is None
