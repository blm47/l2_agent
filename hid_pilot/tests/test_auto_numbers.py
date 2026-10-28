from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.auto_numbers import find_numbers
from hid_pilot.numbers import parse_numbers


def detection(text, x, y, score=.99):
    return ([[x, y], [x + 45, y], [x + 45, y + 12], [x, y + 12]], text, score)


def test_labels_cp_exclusion_and_target_proposal():
    image = np.zeros((400, 800, 3), dtype=np.uint8)
    reader = Mock()
    reader.detect.return_value = [detection("CP", 10, 20), detection("64/64", 150, 20),
                                  detection("HP", 10, 40), detection("129/129", 150, 40),
                                  detection("MP", 10, 60), detection("104/109", 150, 60),
                                  detection("0/58", 500, 60)]
    reader.read.side_effect = [parse_numbers(text, .99)
                              for text in ("64/64", "129/129", "104/109", "0/58")]
    results = find_numbers(image, reader)
    assert [result.kind for result in results] == ["cp", "hp", "mp", "target_hp"]
    assert results[-1].numbers.current == 0
    assert reader.detect.call_args.args[0].shape[0] == 120


def test_multiple_unlabelled_targets_remain_unassigned():
    reader = Mock()
    reader.detect.return_value = [detection("HP", 10, 40), detection("129/129", 150, 40),
                                  detection("0/58", 500, 60), detection("10/100", 500, 90)]
    reader.read.side_effect = [parse_numbers(text, .99)
                              for text in ("129/129", "0/58", "10/100")]
    results = find_numbers(np.zeros((400, 800, 3), dtype=np.uint8), reader, False)
    assert [result.kind for result in results] == ["hp", None, None]
    assert reader.detect.call_args.args[0].shape[0] == 400


def test_crop_recheck_disagreement_rejects_candidate():
    reader = Mock()
    reader.detect.return_value = [detection("0/58", 100, 60)]
    reader.read.return_value = parse_numbers("8/58", .99)
    assert find_numbers(np.zeros((200, 400, 3), dtype=np.uint8), reader) == []


def test_number_proposal_has_space_for_larger_values_after_tight_crop_check():
    reader = Mock()
    reader.detect.return_value = [detection("100/100", 100, 20)]
    reader.read.return_value = parse_numbers("100/100", .99)
    result, = find_numbers(np.zeros((200, 400, 3), dtype=np.uint8), reader)
    assert reader.read.call_args.args[0].shape == (19, 52, 3)
    assert result.box.x1 == pytest.approx((97 - 52 * .2) / 400)
    assert result.box.x2 == pytest.approx((149 + 52 * .2) / 400)
    assert result.box.y1 == 17 / 200
    assert result.box.y2 == 36 / 200
