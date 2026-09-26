from pathlib import Path

import pytest
from pydantic import ValidationError

from l2_agent.roi import NormalizedBox, RoiProfile, load_profile, save_profile


@pytest.mark.parametrize(
    "coordinates",
    [
        (0.5, 0.1, 0.4, 0.3),
        (0.1, 0.3, 0.4, 0.3),
        (-0.1, 0, 1, 1),
        (0, 0, 1.1, 1),
        (0, 0, float("nan"), 1),
    ],
)
def test_invalid_boxes_rejected(coordinates):
    with pytest.raises(ValidationError):
        NormalizedBox(**dict(zip(("x1", "y1", "x2", "y2"), coordinates)))


def test_pixel_bounds_are_exclusive_and_inside_image():
    full = NormalizedBox(x1=0, y1=0, x2=1, y2=1)
    assert full.pixels(800, 600) == (0, 0, 800, 600)
    small = NormalizedBox(x1=0.1, y1=0.1, x2=0.101, y2=0.102)
    assert small.pixels(100, 100) == (10, 10, 11, 11)
    with pytest.raises(ValueError):
        full.pixels(0, 100)


def test_profile_roundtrip_and_resize_guard(tmp_path):
    path = tmp_path / "config" / "roi.json"
    assert load_profile(path) is None
    profile = RoiProfile(
        frame_width=1920,
        frame_height=1080,
        bars={"hp": NormalizedBox(x1=0.1, y1=0.1, x2=0.3, y2=0.12)},
    )
    save_profile(profile, path)
    assert load_profile(path) == profile
    assert profile.matches(1920, 1080)
    assert not profile.matches(1280, 720)
    assert not path.with_suffix(".json.tmp").exists()


def test_bad_profile_is_not_silently_accepted(tmp_path):
    path = tmp_path / "roi.json"
    path.write_text('{"version": 999}', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_profile(path)


def test_failed_replace_preserves_previous_profile(tmp_path, monkeypatch):
    path = tmp_path / "roi.json"
    profile = RoiProfile(frame_width=800, frame_height=600)
    save_profile(profile, path)
    previous = path.read_bytes()

    def fail_replace(self, target):
        raise OSError("Файл занят")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError):
        save_profile(RoiProfile(frame_width=1920, frame_height=1080), path)
    assert path.read_bytes() == previous
