from unittest.mock import Mock
from pathlib import Path
import cv2

import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.target import TargetReader


def test_real_skull_template_distinguishes_living_portrait():
    root = Path(__file__).parent / "fixtures"
    dead = cv2.imread(str(root / "target-portrait-dead.png"))
    alive = cv2.imread(str(root / "target-portrait-alive.png"))
    h, w = dead.shape[:2]
    config = BarProfile(width=w, height=h)
    config.calibrate_dead_marker(dead, BarBox(x1=0, y1=0, x2=1, y2=1))
    assert config.dead_marker_visible(dead)
    assert not config.dead_marker_visible(alive)
    assert type(config.dead_marker_visible(dead)) is bool
    assert type(config.dead_marker_visible(alive)) is bool


@pytest.mark.parametrize("hp,expected", [(np.float64(0), True), (np.float64(.5), False)])
def test_death_comparison_returns_serializable_python_bool(hp, expected):
    from hid_pilot.target import TargetObservation

    dead = TargetReader.death(hp, False)
    assert dead is expected
    observation = TargetObservation(at=0).model_copy(update={"dead": dead})
    assert observation.model_dump(mode="json")["dead"] is expected


def row(text, y, score=.99):
    return ([[120, y], [220, y], [220, y + 10], [120, y + 10]], text, score)


def profile():
    return BarProfile(width=423, height=126, target_panel=BarBox(x1=0, y1=0, x2=1, y2=1))


@pytest.mark.parametrize("name", ["Gremlin", "Monster With Arbitrary Name", "Моб 12"])
def test_one_name_and_hp_reads_monster_without_name_whitelist(name):
    ocr = Mock()
    ocr.detect.return_value = [row("0 / 58", 78), row(name, 5)]
    target = TargetReader(profile()).read(np.zeros((126, 423, 3), dtype=np.uint8), 1., ocr)
    assert target.kind == "monster"
    assert target.name == name
    assert target.hp == 0 and target.dead is True
    assert target.player_class is None and target.clan is None


def test_fragmented_zero_hp_is_joined_on_one_row():
    ocr = Mock()
    tail = ([[261, 71], [288, 71], [288, 91], [261, 91]], "58", .99)
    head = ([[218, 71], [260, 71], [260, 93], [218, 93]], "0/", .95)
    ocr.detect.return_value = [tail, row("Gremlin", 5), head]
    target = TargetReader(profile()).read(np.zeros((126, 423, 3), dtype=np.uint8), 1., ocr)
    assert target.kind == "monster" and target.name == "Gremlin"
    assert target.hp == 0 and target.dead is True


def test_player_keeps_name_class_and_optional_clan_even_with_hp():
    ocr = Mock()
    ocr.detect.return_value = [row("Leader", 5), row("Hawkeye", 25), row("Clan", 45), row("50 / 100", 80)]
    target = TargetReader(profile()).read(np.zeros((126, 423, 3), dtype=np.uint8), 1., ocr)
    assert (target.kind, target.name, target.player_class, target.clan) == ("player", "Leader", "Hawkeye", "Clan")
    assert target.hp == .5 and target.dead is False


def test_known_npc_is_not_mistaken_for_monster_or_player():
    config = profile()
    config.target_kind_names = {"Shopkeeper": "npc"}
    ocr = Mock()
    ocr.detect.return_value = [row("Shopkeeper", 5), row("Trader", 25), row("58 / 58", 80)]
    target = TargetReader(config).read(np.zeros((126, 423, 3), dtype=np.uint8), 1., ocr)
    assert target.kind == "npc"
    assert target.player_class is None


def test_name_without_hp_is_npc_only_when_hp_strip_region_is_configured():
    ocr = Mock()
    ocr.detect.return_value = [row("Any NPC Name", 5)]
    image = np.zeros((126, 423, 3), dtype=np.uint8)
    config = profile()
    assert TargetReader(config).read(image, 1., ocr).kind == "unknown"
    config.target_hp_presence_box = BarBox(x1=.5, x2=.75, y1=.55, y2=.75)
    assert TargetReader(config).read(image, 2., ocr).kind == "npc"


def test_missing_hp_numbers_with_visible_strip_never_means_npc():
    config = profile()
    config.target_hp_presence_box = BarBox(x1=200 / 423, x2=320 / 423, y1=70 / 126, y2=90 / 126)
    image = np.zeros((126, 423, 3), dtype=np.uint8)
    image[74:84, 219:231] = 240
    ocr = Mock()
    ocr.detect.return_value = [row("Gremlin", 5)]
    target = TargetReader(config).read(image, 1., ocr)
    assert target.kind == "monster" and target.hp is None
    assert target.dead is None


def test_hp_strip_outside_name_roi_invalidates_cached_npc_classification():
    config = profile()
    config.target_panel = BarBox(x1=0, x2=1, y1=0, y2=.3)
    config.target_hp_presence_box = BarBox(x1=200 / 423, x2=320 / 423, y1=70 / 126, y2=90 / 126)
    image = np.zeros((126, 423, 3), dtype=np.uint8)
    ocr = Mock()
    ocr.detect.return_value = [row("Any Name", 5)]
    reader = TargetReader(config)
    assert reader.read(image, 1., ocr).kind == "npc"
    image[70:90, 200:320] = (0, 0, 180)
    target = reader.read(image, 2., ocr)
    assert target.kind == "monster" and target.hp == 1


def dead_image():
    image = np.zeros((126, 423, 3), dtype=np.uint8)
    image[10:58, 10:58] = (0, 0, 180)
    image[24:32, 20:29] = 0
    image[24:32, 39:48] = 0
    image[40:46, 26:43] = 0
    return image


def test_calibrated_red_skull_confirms_death_even_with_positive_ocr_hp():
    config = profile()
    image = dead_image()
    config.calibrate_dead_marker(image, BarBox(x1=10 / 423, y1=10 / 126, x2=58 / 423, y2=58 / 126))
    ocr = Mock()
    ocr.detect.return_value = [row("Gremlin", 5)]
    target = TargetReader(config).read(image, 1., ocr)
    assert target.death_marker and target.dead is True
    assert target.kind == "monster" and target.hp is None
    ocr.detect.return_value = [row("Gremlin", 5), row("10 / 58", 80)]
    target = TargetReader(config).read(image, 2., ocr)
    assert target.death_marker and target.dead is True
    assert not config.dead_marker_visible(np.zeros_like(image))


def test_colored_name_change_invalidates_ocr_cache():
    image = np.zeros((126, 423, 3), dtype=np.uint8)
    ocr = Mock()
    ocr.detect.return_value = [row("First", 5), row("58 / 58", 80)]
    reader = TargetReader(profile())
    assert reader.read(image, 1., ocr).name == "First"
    reader.read(image, 1.5, ocr)
    assert ocr.detect.call_count == 1
    image[2:12, 120:145] = (0, 180, 0)
    ocr.detect.return_value = [row("Second", 5), row("58 / 58", 80)]
    assert reader.read(image, 2., ocr).name == "Second"
    assert ocr.detect.call_count == 2


def test_changed_classifier_configuration_invalidates_cache():
    config = profile()
    image = np.zeros((126, 423, 3), dtype=np.uint8)
    ocr = Mock()
    ocr.detect.return_value = [row("Shopkeeper", 5), row("58 / 58", 80)]
    reader = TargetReader(config)
    assert reader.read(image, 1., ocr).kind == "monster"
    config.target_kind_names["Shopkeeper"] = "npc"
    assert reader.read(image, 2., ocr).kind == "npc"


def test_missing_player_class_never_turns_previous_player_into_monster():
    config = profile()
    image = np.zeros((126, 423, 3), dtype=np.uint8)
    reader = TargetReader(config)
    ocr = Mock()
    ocr.detect.return_value = [row("Leader", 5), row("Hawkeye", 25), row("50 / 100", 80)]
    assert reader.read(image, 1., ocr).kind == "player"
    for index in range(2, 4):
        image[5:10, index * 10:index * 10 + 5] = 180
        ocr.detect.return_value = [row("Leader", 5), row(f"{50 - index} / 100", 80)]
        assert reader.read(image, float(index), ocr).kind == "unknown"


@pytest.mark.parametrize("rows", [[], [row("Gremlin", 5, .8), row("0 / 58", 80)],
                                   [row("Gremlin", 5), row("0 / 0", 80)],
                                   [row("Gremlin", 5), row("0 / 58", 80, .8)]])
def test_uncertain_ocr_does_not_invent_monster_or_death(rows):
    ocr = Mock()
    ocr.detect.return_value = rows
    target = TargetReader(profile()).read(np.zeros((126, 423, 3), dtype=np.uint8), 1., ocr)
    assert target.kind == "unknown"
    if target.name:
        assert target.hp is None and target.dead is None

