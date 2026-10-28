from unittest.mock import Mock
from pathlib import Path

import cv2
import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.numbers import NumbersReader, parse_numbers


@pytest.mark.parametrize("text,fraction", [("129 / 129", 1), ("0 / 58", 0),
                                         ("5/100", .05), ("1 000 / 2 000", .5)])
def test_numbers_read_exact_ratio_and_zero(text, fraction):
    assert parse_numbers(text, .99).fraction == fraction


@pytest.mark.parametrize("score,accepted", [(.8499, False), (.85, True), (.88997, True)])
def test_numeric_confidence_threshold_is_85_percent(score, accepted):
    assert (parse_numbers("0/4166", score) is not None) is accepted


def test_level_cache_skips_ocr_for_unchanged_digits_and_reads_changes():
    reader = NumbersReader()
    reader.detect = Mock(side_effect=[[(None, "32", .99)], [(None, "33", .99)]])
    image = np.zeros((20, 40, 3), dtype=np.uint8)
    image[4:15, 10:25] = 200
    assert reader.read_level(image) == 32
    assert reader.read_level(image.copy()) == 32
    reader.detect.assert_called_once()
    image[5:10, 10:13] = 0
    assert reader.read_level(image) == 33
    assert reader.detect.call_count == 2


def test_tight_name_crop_uses_single_recognition_without_detection():
    reader = NumbersReader()
    reader.engine = Mock(return_value=([["Sentinel Of Water", .976]], None))
    image = np.zeros((20, 200, 3), dtype=np.uint8)
    image[5:15, 5:150] = 200
    assert reader.read_text(image) == ("Sentinel Of Water", .976)
    reader.engine.assert_called_once()
    assert reader.engine.call_args.kwargs == {"use_det": False, "use_cls": False}
    assert reader.engine.call_args.args[0].shape == (54, 483, 3)


@pytest.mark.parametrize("text,score", [("0/0", .99), ("101/100", .99), ("O/58", .99),
                                      ("HP 5/100", .99), ("5/100", .7), ("/100", .99),
                                      ("-5/100", .99), ("5/100/200", .99)])
def test_ambiguous_numbers_rejected(text, score):
    assert parse_numbers(text, score) is None


def test_conflicting_preprocessing_is_unknown():
    reader = NumbersReader()
    reader.engine = Mock(side_effect=[([["0/58", .99]], None), ([["8/58", .99]], None)])
    image = np.full((20, 80, 3), 200, dtype=np.uint8)
    assert reader.read(image) is None


def test_zero_hp_is_confirmed_by_independent_ocr_instead_of_cache():
    reader = NumbersReader()
    reader.engine = Mock(side_effect=[([["0/100", .99]], None), ([["0/100", .99]], None),
                                      ([["20/100", .99]], None), ([["20/100", .99]], None)])
    image = np.full((20, 80, 3), 200, dtype=np.uint8)
    assert reader.read(image).fraction == 0
    assert reader.read(image).fraction == .2
    assert reader.engine.call_count == 4


def test_ocr_zero_precedes_color_and_unknown_can_fall_back(monkeypatch):
    reader = Mock()
    reader.read.return_value = parse_numbers("0/58", .99)
    monkeypatch.setattr("hid_pilot.numbers.shared_numbers", lambda: reader)
    profile = BarProfile(width=200, height=100,
                         numbers={"target_hp": BarBox(x1=0, y1=0, x2=.5, y2=.2)},
                         bars={"target_hp": BarBox(x1=0, y1=.5, x2=1, y2=.6)})
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[50:60, :100] = (0, 0, 180)
    assert profile.read(image, "target_hp") == 0
    reader.read.return_value = None
    assert profile.read(image, "target_hp") == .5
    assert profile.read_detail(image, "target_hp") == (.5, "полоса (OCR не распознан)")


def test_corpse_hp_excludes_clipped_world_name_from_user_screenshot():
    image = cv2.imread(str(Path(__file__).parent / "fixtures" / "target-zero-with-world-text.png"))
    assert image.shape == (22, 109, 3)
    reader = NumbersReader()

    def recognize(crop, **kwargs):
        # В OCR остаётся строка HP с исходной обводкой, без нижнего имени моба.
        assert crop.shape == (48, 327, 3)
        return [["0 / 1659", .99]], None

    reader.engine = Mock(side_effect=recognize)
    assert reader.read(image).fraction == 0.
    assert reader.read(image).fraction == 0.
    assert reader.engine.call_count == 4


def test_two_complete_numeric_rows_must_agree():
    image = np.zeros((30, 80, 3), dtype=np.uint8)
    image[3:11, 10:60] = 200
    image[17:25, 10:60] = 200
    reader = NumbersReader()
    reader.engine = Mock(side_effect=[([[value, .99]], None)
                                      for value in ("0/100", "0/100", "80/100", "80/100")])
    assert reader.read(image) is None


def test_only_clipped_rows_do_not_produce_zero_hp():
    image = np.zeros((20, 80, 3), dtype=np.uint8)
    image[:6, 10:60] = 200
    image[-6:, 10:60] = 200
    reader = NumbersReader()
    reader.engine = Mock()
    assert reader.read(image) is None
    reader.engine.assert_not_called()


@pytest.mark.parametrize("filename,expected", [
    ("mp-442-with-world-text.png", (442, 442)),
    ("target-zero-with-world-text.png", (0, 1659)),
])
def test_real_ocr_preserves_outlined_digits_next_to_clipped_world_text(filename, expected):
    pytest.importorskip("rapidocr_onnxruntime")
    image = cv2.imread(str(Path(__file__).parent / "fixtures" / filename))
    reader = NumbersReader()
    result = reader.read(image)
    assert result is not None
    assert (result.current, result.maximum) == expected
