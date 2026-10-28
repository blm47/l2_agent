import threading
from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.farm_observer import FarmIdentityCache
from hid_pilot.monster_catalogue import MonsterCatalogue, MonsterNameConsensus
from hid_pilot.target import TargetObservation


def observation(at, name="Gremlin", **changes):
    return TargetObservation(at=at, name=name, kind="monster", level=1,
                             confidence=.95).model_copy(update=changes)


def test_new_name_saved_only_after_three_independent_matching_readings(tmp_path):
    path = tmp_path / "monsters.sqlite3"
    catalogue = MonsterCatalogue(path)
    consensus = MonsterNameConsensus(catalogue)
    for index in range(2):
        result = consensus.observe(observation(index * .5), index * .5)
        assert result.name is None
        assert result.raw_name == "Gremlin"
        assert catalogue.lookup("Gremlin") is None
    result = consensus.observe(observation(1.), 1.)
    assert result.name == "Gremlin" and result.name_confirmations == 3
    assert MonsterCatalogue(path).lookup("gremlin") == "Gremlin"


def test_known_name_requires_two_readings_and_keeps_canonical_spelling(tmp_path):
    catalogue = MonsterCatalogue(tmp_path / "monsters.sqlite3")
    catalogue.confirm("Cloudy Beast", 22)
    consensus = MonsterNameConsensus(catalogue)
    assert consensus.observe(observation(0, " cloudy   beast "), 0).name is None
    assert consensus.observe(observation(.5, "CLOUDY BEAST"), .5).name == "Cloudy Beast"


def test_alternating_names_do_not_accumulate_votes(tmp_path):
    catalogue = MonsterCatalogue(tmp_path / "monsters.sqlite3")
    consensus = MonsterNameConsensus(catalogue)
    for index in range(12):
        assert consensus.observe(observation(index * .5, "Gremlin" if index % 2 else "Gremiin"),
                                 index * .5).name is None
    assert not catalogue.load()


def test_new_target_does_not_compete_with_previous_monsters_votes(tmp_path):
    catalogue = MonsterCatalogue(tmp_path / "monsters.sqlite3")
    consensus = MonsterNameConsensus(catalogue)
    for index in range(8):
        consensus.observe(observation(index * .5, "Kanil Succubus"), index * .5)
    consensus.reset_target()
    for index in range(8, 11):
        result = consensus.observe(observation(index * .5, "Rowin Undine"), index * .5)
    assert result.name == "Rowin Undine"
    assert result.name_confirmations == 3


def test_replayed_timestamp_and_stale_reading_are_not_confirmations(tmp_path):
    catalogue = MonsterCatalogue(tmp_path / "monsters.sqlite3")
    consensus = MonsterNameConsensus(catalogue)
    consensus.observe(observation(0), 0)
    for _ in range(10):
        consensus.observe(observation(0), 0)
    assert consensus.confirmations == 1
    assert consensus.observe(observation(.5), 4).name is None
    assert consensus.confirmations == 0
    assert not catalogue.load()


@pytest.mark.parametrize("changes", [{"kind": "player"}, {"kind": "npc"}, {"confidence": .8}, {"name": None}])
def test_non_monster_or_uncertain_name_never_enters_database(tmp_path, changes):
    catalogue = MonsterCatalogue(tmp_path / "monsters.sqlite3")
    consensus = MonsterNameConsensus(catalogue)
    for index in range(8):
        consensus.observe(observation(index * .5, **changes), index * .5)
    assert not catalogue.load()


def test_long_gap_starts_confirmation_again(tmp_path):
    consensus = MonsterNameConsensus(MonsterCatalogue(tmp_path / "monsters.sqlite3"))
    consensus.observe(observation(0), 0)
    consensus.observe(observation(.5), .5)
    assert consensus.observe(observation(35), 35).name_confirmations == 1


def test_single_bad_reading_does_not_erase_matching_name_evidence(tmp_path):
    catalogue = MonsterCatalogue(tmp_path / "monsters.sqlite3")
    consensus = MonsterNameConsensus(catalogue)
    readings = ["Gremlin", "Gremlin", "Gremiin", "Gremlin", "Gremlin", "Gremlin", "Gremlin"]
    for index, name in enumerate(readings):
        result = consensus.observe(observation(index, name), index)
    assert result.name == "Gremlin"
    assert catalogue.lookup("Gremlin") == "Gremlin"


def test_unknown_ocr_frame_does_not_erase_valid_votes(tmp_path):
    consensus = MonsterNameConsensus(MonsterCatalogue(tmp_path / "monsters.sqlite3"))
    consensus.observe(observation(0), 0)
    consensus.observe(observation(1, confidence=.5), 1)
    assert consensus.observe(observation(2), 2).name_confirmations == 2


def test_known_name_does_not_guess_a_similar_new_name(tmp_path):
    catalogue = MonsterCatalogue(tmp_path / "monsters.sqlite3")
    catalogue.confirm("Gremlin", 1)
    consensus = MonsterNameConsensus(catalogue)
    for index in range(2):
        assert consensus.observe(observation(index * .5, "Gremiin"), index * .5).name is None
    assert catalogue.lookup("Gremiin") is None


def test_confirmation_is_written_only_once_per_matching_streak(tmp_path):
    catalogue = MonsterCatalogue(tmp_path / "monsters.sqlite3")
    catalogue.confirm = Mock(wraps=catalogue.confirm)
    consensus = MonsterNameConsensus(catalogue)
    for index in range(10):
        consensus.observe(observation(index * .5), index * .5)
    catalogue.confirm.assert_called_once_with("Gremlin", 1)


def test_background_confirmation_runs_real_ocr_three_times_on_unchanged_pixels(monkeypatch, tmp_path):
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    numbers = Mock()
    points = [[0, 0], [50, 0], [50, 10], [0, 10]]
    hp_points = [[0, 20], [50, 20], [50, 30], [0, 30]]
    numbers.detect.return_value = [(points, "Gremlin", .99), (hp_points, "100 / 100", .99)]
    monkeypatch.setattr("hid_pilot.farm_observer.background_numbers", lambda: numbers)
    catalogue = MonsterCatalogue(tmp_path / "monsters.sqlite3")
    profile = BarProfile(width=200, height=100, target_panel=BarBox(x1=0, y1=0, x2=1, y2=1))
    cache = FarmIdentityCache(profile, None, catalogue)
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    tick = [0]
    now = [0.]
    monkeypatch.setattr("hid_pilot.farm_observer.time.monotonic", lambda: now[0])

    def schedule(seconds):
        if tick[0] == 3:
            return True
        now[0] = tick[0] * .5
        cache.pending = (image.copy(), now[0], cache.signature(image), 0)
        tick[0] += 1
        return False

    cache.stop_event = Mock()
    cache.stop_event.wait.side_effect = schedule
    cache._run()
    assert numbers.detect.call_count == 3
    assert catalogue.lookup("Gremlin") == "Gremlin"
    assert cache.result[2].name_confirmations == 3
