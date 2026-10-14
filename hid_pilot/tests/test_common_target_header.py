from unittest.mock import Mock

import numpy as np

from hid_pilot.auto_player import find_player_panels
from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.numbers import parse_numbers
from hid_pilot.target import TargetReader


def test_autosearch_proposes_single_monster_name_above_hp():
    image = np.zeros((200, 800, 3), dtype=np.uint8)
    hp = BarBox(x1=.6, y1=.6, x2=.65, y2=.7)
    reader = Mock()
    reader.detect.return_value = [([[420, 108], [500, 108], [500, 118], [420, 118]], "Gremlin", .99)]
    candidates = find_player_panels(image, reader, False, hp)
    assert len(candidates) == 1
    assert candidates[0].box.y2 < hp.y1
    assert "Gremlin" in candidates[0].evidence


def test_name_only_common_header_reads_monster_hp_from_separate_roi():
    image = np.zeros((200, 800, 3), dtype=np.uint8)
    header = BarBox(x1=.5, y1=.1, x2=.7, y2=.2)
    hp = BarBox(x1=.6, y1=.6, x2=.65, y2=.7)
    profile = BarProfile(width=800, height=200, target_panel=header, numbers={"target_hp": hp})
    reader = Mock()
    reader.detect.return_value = [([[0, 0], [50, 0], [50, 10], [0, 10]], "Gremlin", .99)]
    reader.read.return_value = parse_numbers("75/100", .99)
    target = TargetReader(profile).read(image, 1., reader)
    assert target.name == "Gremlin" and target.kind == "monster" and target.hp == .75
