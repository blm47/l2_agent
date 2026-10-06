from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.auto_party import find_party


def scene(count):
    image = np.zeros((800, 600, 3), dtype=np.uint8)
    for i in range(count):
        y = 60 + i * 80
        image[y:y + 5, 30:230] = (0, 150, 220)
        image[y + 7:y + 17, 25:230] = (0, 0, 180)
        image[y + 19:y + 29, 20:230] = (180, 0, 0)
    reader = Mock()
    reader.detect.return_value = [([[0, 0], [100, 0], [100, 15], [0, 15]], "Member", .99)]
    return image, reader


@pytest.mark.parametrize("count", [0, 1, 8])
def test_party_region_is_proposed_and_step_uses_next_member(count):
    image, reader = scene(count)
    found = find_party(image, reader)
    assert len(found) == int(count > 0)
    if count:
        assert found[0].profile.cp.y1 == 60 / 800
        assert found[0].profile.row_step == (80 if count > 1 else 69)
        assert "Member" in found[0].evidence


@pytest.mark.parametrize("text,score", [("148/148", .99), ("CP", .99), ("Member", .5)])
def test_resource_panel_or_uncertain_name_is_not_party(text, score):
    image, reader = scene(1)
    reader.detect.return_value[0] = (reader.detect.return_value[0][0], text, score)
    assert find_party(image, reader) == []


def test_partial_bars_use_cp_extent_for_user_review():
    image, reader = scene(1)
    image[67:77, 150:] = 0
    profile = find_party(image, reader)[0].profile
    assert profile.hp.x2 == profile.cp.x2 == 230 / 600
    assert profile.hp.y1 == 67 / 800


def test_first_member_with_low_mp_and_pets_is_not_skipped():
    image = np.zeros((1000, 600, 3), dtype=np.uint8)
    offsets = [0, 80, 160, 240, 340, 420, 500, 600]
    for index, offset in enumerate(offsets):
        y = 60 + offset
        image[y:y + 5, 30:230] = (0, 150, 220)
        image[y + 7:y + 17, 25:220] = (0, 0, 180)
        image[y + 19:y + 29, 20:(60 if index == 0 else 180)] = (180, 0, 0)
        if index in (3, 6):
            image[y + 35:y + 39, 25:230] = (0, 0, 180)
            image[y + 41:y + 45, 20:180] = (180, 0, 0)
    reader = Mock()
    reader.detect.side_effect = [[([], name, .99)] for name in (
        "ninochka", "immolate", "ForgotMeNot", "ISatana", "Alfa", "Valdakar", "indik", "Mephala")]
    found = find_party(image, reader)
    assert len(found) == 1
    assert found[0].evidence.startswith("ninochka:")
    assert found[0].profile.cp.y1 == .06
    assert found[0].profile.row_offsets == offsets
    assert found[0].profile.box("mp", 7).y1 == pytest.approx(.679)


def test_unknown_first_name_does_not_shift_slot_numbers():
    image, reader = scene(8)
    recognized = reader.detect.return_value
    reader.detect.side_effect = [[]] + [recognized] * 7
    found = find_party(image, reader)
    assert len(found) == 1
    assert found[0].profile.cp.y1 == 60 / 800


def test_manual_panel_selection_works_without_ocr():
    from hid_pilot.bars import BarBox

    image, _ = scene(8)
    region = BarBox(x1=0, x2=.45, y1=.02, y2=.9)
    assert find_party(image, None, region)[0].profile.cp.y1 == 60 / 800
    assert find_party(image, None, BarBox(x1=.5, x2=1, y1=0, y2=1)) == []
