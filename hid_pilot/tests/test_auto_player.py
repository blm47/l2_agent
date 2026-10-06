from unittest.mock import Mock

import numpy as np

from hid_pilot.auto_player import find_player_panels


def line(text, x, y, score=.99):
    return ([[x, y], [x + 100, y], [x + 100, y + 10], [x, y + 10]], text, score)


def test_auto_player_groups_screenshot_layout_and_excludes_own_stats():
    image = np.zeros((800, 1200, 3), dtype=np.uint8)
    reader = Mock()
    reader.detect.return_value = [line("originalNutta", 185, 15), line("Hawkeye", 162, 33),
                                  line("AES", 162, 57), line("HP", 50, 15), line("300 / 300", 70, 15)]
    candidates = find_player_panels(image, reader)
    assert len(candidates) == 1
    assert "originalNutta / Hawkeye / AES" in candidates[0].evidence
    assert candidates[0].box.x1 == 159 / 1200
    assert candidates[0].box.y2 == 70 / 800
    assert reader.detect.call_args.args[0].shape[0] == 240


def test_party_rows_and_resource_mob_panel_are_not_player_candidates():
    reader = Mock()
    reader.detect.return_value = [line("MemberA", 50, 15), line("MemberB", 50, 85),
                                  line("MemberC", 50, 155), line("Mob", 400, 15),
                                  line("500 / 800", 400, 33)]
    assert not find_player_panels(np.zeros((800, 1200, 3), dtype=np.uint8), reader)
