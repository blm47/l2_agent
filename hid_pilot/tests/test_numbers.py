from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.numbers import NumbersReader, parse_numbers


@pytest.mark.parametrize("text,fraction", [("129 / 129", 1), ("0 / 58", 0),
                                         ("5/100", .05), ("1 000 / 2 000", .5)])
def test_numbers_read_exact_ratio_and_zero(text, fraction):
    assert parse_numbers(text, .99).fraction == fraction


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
