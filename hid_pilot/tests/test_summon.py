import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.party_statistics import format_statistics
from hid_pilot.summon import calibrate_summon, read_summon


def summon_scene(hp=1., mp=1.):
    image = np.full((200, 400, 3), 20, np.uint8)
    icon = np.random.default_rng(42).integers(0, 256, (32, 32, 1), dtype=np.uint8)
    image[110:142, 10:42] = icon
    image[120:128, 60:60 + round(200 * hp)] = (60, 55, 155)
    image[130:138, 60:60 + round(200 * mp)] = (195, 110, 55)
    boxes = {"hp": BarBox(x1=.15, y1=.6, x2=.65, y2=.64),
             "mp": BarBox(x1=.15, y1=.65, x2=.65, y2=.69),
             "icon": BarBox(x1=.025, y1=.55, x2=.105, y2=.71)}
    return image, boxes


def summon_profile():
    image, boxes = summon_scene()
    return BarProfile(width=400, height=200, summon=calibrate_summon(image, **boxes))


@pytest.mark.parametrize("hp,mp", [(1., 1.), (.4, .75), (0., 1.), (1., 0.), (0., 0.)])
def test_reads_current_summon_bars_including_visible_empty_bars(hp, mp):
    image, _ = summon_scene(hp, mp)
    assert read_summon(image, summon_profile()) == {"summon_hp": hp, "summon_mp": mp}


@pytest.mark.parametrize("missing", ["panel", "icon", "wrong_icon", "size", "profile"])
def test_missing_or_occluded_panel_has_unknown_resources_not_old_health(missing):
    image, _ = summon_scene(.4, .75)
    profile = summon_profile()
    assert read_summon(image, profile)["summon_hp"] == .4
    if missing == "panel":
        image[100:150] = 20
    elif missing == "icon":
        image[110:142, 10:42] = 20
    elif missing == "wrong_icon":
        image[110:142, 10:42] = np.random.default_rng(7).integers(0, 256, (32, 32, 3), dtype=np.uint8)
    elif missing == "size":
        image = image[:-10]
    else:
        profile.summon = None
    assert read_summon(image, profile) == {"summon_hp": None, "summon_mp": None}


def test_unreadable_red_fragments_do_not_become_zero_hp():
    image, _ = summon_scene(0., .5)
    image[120:128, 150:155] = (60, 55, 155)
    assert read_summon(image, summon_profile()) == {"summon_hp": None, "summon_mp": .5}


def test_summon_profile_roundtrips_and_old_profiles_do_not_enable_it():
    profile = summon_profile()
    assert BarProfile.model_validate_json(profile.model_dump_json()) == profile
    assert BarProfile(width=400, height=200).summon is None
    image, boxes = summon_scene()
    image[110:142, 10:42] = 20
    with pytest.raises(ValueError, match="эталон"):
        calibrate_summon(image, **boxes)
    image, boxes = summon_scene()
    boxes["hp"] = boxes["icon"]
    with pytest.raises(ValueError, match="полосу"):
        calibrate_summon(image, **boxes)


def test_statistics_show_summon_separately_without_shifting_party_slots():
    text = format_statistics({"summon_hp": .4, "summon_mp": None}, {}, [], {}, "Бой")
    assert "САМОН: HP 40% · MP ?" in text
    assert "ПАТИ — участников: 0" in text
