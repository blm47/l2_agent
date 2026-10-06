from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.bars import BarBox
from hid_pilot.party import PartyProfile, party_text, read_party


def profile():
    def box(y1, y2):
        return BarBox(x1=.1, x2=.9, y1=y1 / 800, y2=y2 / 800)

    return PartyProfile(width=200, height=800, name=box(0, 20), cp=box(22, 26),
                        hp=box(28, 34), mp=box(36, 42), row_step=80)


def scene(count):
    image = np.zeros((800, 200, 3), dtype=np.uint8)
    texts = []
    for slot in range(count):
        y = slot * 80
        packed = slot * 20
        texts.append(([[0, packed], [80, packed], [80, packed + 18], [0, packed + 18]],
                      f"Member{slot}", .99))
        image[y + 22:y + 26, 20:180] = (0, 150, 220)
        image[y + 28:y + 34, 20:100] = (0, 0, 180)
        image[y + 36:y + 42, 20:60] = (180, 0, 0)
    return image, Mock(detect=Mock(return_value=texts))


@pytest.mark.parametrize("count", [0, 1, 8])
def test_party_counts_names_and_fractional_bars(count):
    image, reader = scene(count)
    members = read_party(image, profile(), reader)
    assert len(members) == count
    for slot, member in enumerate(members):
        assert member.slot == slot + 1
        assert member.name == f"Member{slot}"
        assert (member.cp, member.hp, member.mp) == (1., .5, .25)
    assert reader.detect.call_count == 1
    assert reader.detect.call_args.args[0].shape[1] == 160


def test_party_does_not_keep_departed_members_or_invent_zero():
    image, reader = scene(1)
    image[28:42] = 0
    members = read_party(image, profile(), reader)
    assert members[0].hp is None and members[0].mp is None
    assert "HP ?" in party_text(members)
    empty, reader = scene(0)
    assert read_party(empty, profile(), reader) == []


def test_party_unknown_name_with_visible_bars():
    image, reader = scene(1)
    reader.detect.return_value = []
    assert read_party(image, profile(), reader)[0].name is None


@pytest.mark.parametrize("name", ["Chylli", None])
def test_party_text_places_resources_before_name(name):
    image, reader = scene(1)
    member = read_party(image, profile(), reader)[0].model_copy(update={"name": name})
    assert party_text([member]).splitlines()[1] == (
        f"1. CP 100% · HP 50% · MP 25% · {name or 'имя неизвестно'}"
    )


def test_party_rejects_resized_frame_and_invalid_spacing():
    image, reader = scene(1)
    with pytest.raises(RuntimeError, match="Размер"):
        read_party(image[:400], profile(), reader)
    with pytest.raises(ValueError, match="Шаг"):
        PartyProfile.model_validate(profile().model_dump() | {"row_step": 20})
    with pytest.raises(ValueError, match="границы"):
        PartyProfile.model_validate(profile().model_dump() | {"row_step": 120})


def test_party_reads_offsets_after_pets_and_roundtrips_profile():
    image, reader = scene(8)
    shifted = np.zeros_like(image)
    offsets = [0, 80, 160, 240, 340, 420, 500, 600]
    for slot, offset in enumerate(offsets):
        shifted[offset:offset + 60] = image[slot * 80:slot * 80 + 60]
    adjusted = PartyProfile.model_validate(profile().model_dump() | {"row_offsets": offsets})
    restored = PartyProfile.model_validate_json(adjusted.model_dump_json())
    members = read_party(shifted, restored, reader)
    assert [m.name for m in members] == [f"Member{i}" for i in range(8)]
    assert all((m.cp, m.hp, m.mp) == (1., .5, .25) for m in members)


@pytest.mark.parametrize("offsets", [
    [0, 80], [1, 80, 160, 240, 320, 400, 480, 560],
    [0, 80, 160, 150, 320, 400, 480, 560], [0, 80, 160, 240, 320, 400, 480, 800],
])
def test_party_rejects_invalid_offsets(offsets):
    with pytest.raises(ValueError):
        PartyProfile.model_validate(profile().model_dump() | {"row_offsets": offsets})
