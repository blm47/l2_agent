from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.healer_observer import HealerReader
from hid_pilot.numbers import parse_numbers
from hid_pilot.party import PartyProfile


def test_disabled_chat_does_not_require_region_before_start():
    own, party = profiles()
    own.system_chat_enabled = True
    assert HealerReader(own, party).own is own


def test_healer_background_loop_feeds_same_frame_to_system_chat(monkeypatch):
    from hid_pilot.healer_observer import HealerObserver
    from hid_pilot.target import TargetObservation

    reader = HealerReader(*profiles())
    reader.auto_refresh = Mock()
    reader.identities.update = Mock(return_value=TargetObservation(at=10.))
    observer = HealerObserver(Mock(), reader, .1, lambda: False)
    image = scene()
    observer.identity_image = image
    observer.identity_at = 10.
    observer.chat.read_chat = Mock()
    observer.stop_event = Mock()
    observer.stop_event.is_set.side_effect = [False, True]
    monkeypatch.setattr("hid_pilot.healer_observer.time.monotonic", lambda: 10.)
    observer._identity_loop()
    observer.chat.read_chat.assert_called_once()
    assert observer.chat.read_chat.call_args.args[0] is image
    assert observer.chat.read_chat.call_args.args[1] == 10.


def profiles():
    def box(y1, y2):
        return BarBox(x1=.1, x2=.9, y1=y1 / 800, y2=y2 / 800)

    own = BarProfile(width=200, height=800,
                     bars={"hp": box(700, 706), "mp": box(710, 716), "cp": box(720, 726)})
    party = PartyProfile(width=200, height=800, name=box(0, 20), cp=box(22, 26),
                         hp=box(28, 34), mp=box(36, 42), row_step=80)
    return own, party


def scene():
    image = np.zeros((800, 200, 3), dtype=np.uint8)
    image[22:26, 20:180] = (0, 150, 220)
    image[28:34, 20:100] = (0, 0, 180)
    image[36:42, 20:100] = (180, 0, 0)
    image[0:10, 40:60] = 200
    image[700:706, 20:180] = (0, 0, 180)
    image[710:716, 20:180] = (180, 0, 0)
    image[720:726, 20:180] = (0, 150, 220)
    return image


def test_party_resources_work_without_name_ocr():
    reader = HealerReader(*profiles())
    image = scene()
    first = reader.read(image, 1)
    assert first.roster_known
    assert first.members[0].name is None
    assert first.members[0].target_id == "@slot1"
    image[0:20] = 0
    image[28:34, 60:100] = 0
    second = reader.read(image, 2)
    assert second.roster_known
    assert second.members[0].hp == .25
    assert second.members[0].mp == .5


def test_missing_party_mp_is_unknown_even_with_other_bars():
    reader = HealerReader(*profiles())
    image = scene()
    image[36:42] = 0
    state = reader.read(image, 1)
    assert not state.roster_known
    assert state.members[0].mp is None


def test_own_numbers_cache_requires_unchanged_text(monkeypatch):
    own, party = profiles()
    own.numbers["mp"] = BarBox(x1=.1, x2=.9, y1=.8, y2=.82)
    reader = HealerReader(own, party)
    image = scene()
    image[640:650, 30:40] = 200
    ocr = Mock()
    ocr.read.return_value = parse_numbers("50/100", .99)
    monkeypatch.setattr("hid_pilot.numbers.shared_numbers", lambda: ocr)
    assert reader.read(image, 1).mp == .5
    assert reader.read(image, 1.2).mp == .5
    assert ocr.read.call_count == 1
    image[640:650, 40:45] = 200
    ocr.read.return_value = parse_numbers("30/100", .99)
    assert reader.read(image, 1.4).mp == .3
    assert ocr.read.call_count == 2


