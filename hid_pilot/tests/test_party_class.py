import numpy as np
import pytest
from unittest.mock import Mock

from hid_pilot.bars import BarBox
from hid_pilot.party import PartyMember, PartyProfile
from hid_pilot.party_class import PartyClassDetector, icon_pixels
from hid_pilot.party_statistics import format_statistics


def fixture():
    rng = np.random.default_rng(19)
    icon = rng.integers(0, 255, size=(40, 40, 3), dtype=np.uint8)
    other = rng.integers(0, 255, size=(40, 40, 3), dtype=np.uint8)
    image = np.zeros((800, 400, 3), dtype=np.uint8)
    image[20:60, 300:340] = icon
    image[100:140, 300:340] = other
    box = BarBox(x1=.05, y1=.025, x2=.5, y2=.03)
    profile = PartyProfile(width=400, height=800, row_step=80, name=box, cp=box, hp=box, mp=box,
        class_icon=BarBox(x1=.75, y1=.025, x2=.85, y2=.075),
        class_templates={"Elven Elder": icon_pixels(icon)})
    return profile, image, icon


def test_icon_requires_three_distinct_frames_and_unknown_stays_unknown():
    profile, image, _ = fixture()
    detector = PartyClassDetector(profile)
    members = [PartyMember(slot=1, name="A"), PartyMember(slot=2, name="B")]
    assert detector.apply(image, members, 1.)[0].player_class is None
    assert detector.apply(image, members, 1.)[0].player_class is None
    assert detector.apply(image, members, 1.5)[0].player_class is None
    result = detector.apply(image, members, 2.)
    assert result[0].player_class == "Elven Elder"
    assert result[0].class_confidence >= .94
    assert result[1].player_class is None and result[1].class_icon_checked
    text = format_statistics({}, {}, result, {}, "Наблюдение")
    assert "Elven Elder" in text and "Класс" in text


@pytest.mark.parametrize("reason", ["gap", "name", "icon", "empty"])
def test_temporal_confirmation_resets_on_changed_or_missing_state(reason):
    profile, image, _ = fixture()
    detector = PartyClassDetector(profile)
    members = [PartyMember(slot=1, name="A")]
    detector.apply(image, members, 1.)
    detector.apply(image, members, 1.5)
    at = 5. if reason == "gap" else 2.
    if reason == "name":
        members = [PartyMember(slot=1, name="New")]
    if reason == "icon":
        image[20:60, 300:340] = 0
    if reason == "empty":
        detector.apply(image, [], 1.8)
    assert detector.apply(image, members, at)[0].player_class is None


def test_identical_templates_are_ambiguous_and_never_assign_class():
    profile, image, icon = fixture()
    profile.class_templates["Shilien Elder"] = icon_pixels(icon)
    detector = PartyClassDetector(profile)
    for at in (1., 1.5, 2., 2.5):
        assert detector.apply(image, [PartyMember(slot=1)], at)[0].player_class is None


def test_both_classes_are_detected_and_level_numbers_are_ignored():
    profile, image, icon = fixture()
    profile.class_templates["Shilien Elder"] = icon_pixels(image[100:140, 300:340])
    image[53:60, 300:340] = 255
    assert icon_pixels(image[20:60, 300:340]) == icon_pixels(icon)
    detector = PartyClassDetector(profile)
    for at in (1., 1.5, 2.):
        result = detector.apply(image, [PartyMember(slot=1), PartyMember(slot=2)], at)
    assert [m.player_class for m in result] == ["Elven Elder", "Shilien Elder"]


def test_profile_roundtrip_and_missing_calibration_are_compatible():
    profile, image, _ = fixture()
    assert PartyProfile.model_validate_json(profile.model_dump_json()) == profile
    profile.class_templates.clear()
    member = PartyMember(slot=1)
    assert PartyClassDetector(profile).apply(image, [member], 1.) == [member]
    with pytest.raises(ValueError, match="однотонным"):
        PartyProfile.model_validate(profile.model_dump() | {"class_templates": {"Elven Elder": [[0] * 32] * 32}})


def test_healer_reader_emits_class_without_target_selection():
    from hid_pilot.healer_observer import HealerReader
    from hid_pilot.bars import BarProfile

    profile, image, _ = fixture()
    own = BarProfile(width=400, height=800, bars={key: profile.hp for key in ("hp", "mp", "cp")})
    reader = HealerReader(own, profile)
    reader.identities.apply = Mock(return_value=([PartyMember(slot=1, name="A", hp=1., mp=.1)], {}))
    reader.destroyer.read = Mock(return_value=())
    for at in (1., 1.5, 2.):
        state = reader.read(image, at)
    assert state.members[0].player_class == "Elven Elder"
    assert state.selected_name is None


def test_icon_class_does_not_change_ee_mana_policy():
    from test_farm_ee import policy, mana_reserve_state

    state = mana_reserve_state(own_mp=1., members_mp=(.01, .5))
    state = state.model_copy(update={"members": tuple(
        member.model_copy(update={"player_class": "Elven Elder", "class_confidence": 1., "class_icon_checked": True})
        for member in state.members)})
    assert policy().decide(state, 10.).target == "@slot1"
