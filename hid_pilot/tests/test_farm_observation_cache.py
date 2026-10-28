import threading
import time
from unittest.mock import Mock

import numpy as np
from test_melee_farm import harness

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.farm import FarmConfig, Farmer
from hid_pilot.farm_observer import FarmIdentityCache
from hid_pilot.numbers import NumbersReader
from hid_pilot.target import TargetObservation
import pytest


@pytest.mark.parametrize("hp,marker,dead", [(None, True, True), (None, False, None),
                                          (.5, True, True), (0., False, True)])
def test_fresh_death_marker_is_read_outside_identity_cache(monkeypatch, hp, marker, dead):
    box = BarBox(x1=0, y1=0, x2=1, y2=.2)
    config = BarProfile(width=200, height=100,
                        bars={name: box for name in ("hp", "mp", "target_hp")})
    controller = Mock()
    controller.pause_generation = 0
    controller.capture.return_value = np.zeros((100, 200, 3), dtype=np.uint8)
    monkeypatch.setattr(BarProfile, "read_detail", lambda self, image, name:
                        (hp if name == "target_hp" else .8, "полоса"))
    monkeypatch.setattr(BarProfile, "dead_marker_visible", lambda self, image: np.bool_(marker))
    farmer = Farmer(controller, config, FarmConfig(rest="="), Mock(), lambda: 0.)
    farmer.identity_cache = Mock()
    farmer.identity_cache.snapshot.return_value = (TargetObservation(at=0), 52, [])
    assert farmer.observe() == (.8, .8, hp)
    assert farmer.last_target.dead is dead
    assert farmer.last_target.death_marker is marker
    assert farmer.last_target.model_dump(mode="json")["death_marker"] is marker


def test_number_cache_requires_same_live_text_and_expires(monkeypatch):
    now = [0.]
    monkeypatch.setattr("hid_pilot.numbers.time.monotonic", lambda: now[0])
    reader = NumbersReader()
    reader.engine = Mock(return_value=([["50/100", .99]], None))
    image = np.full((10, 40, 3), 200, dtype=np.uint8)
    assert reader.read(image).fraction == .5
    assert reader.engine.call_count == 2
    now[0] = 1.9
    assert reader.read(image).fraction == .5
    assert reader.engine.call_count == 2
    image[:, :2] = 0
    reader.engine.return_value = ([["40/100", .99]], None)
    assert reader.read(image).fraction == .4
    assert reader.engine.call_count == 4
    now[0] = 4.
    assert reader.read(image).fraction == .4
    assert reader.engine.call_count == 6


def profile():
    box = BarBox(x1=.5, y1=.6, x2=.9, y2=.8)
    return BarProfile(width=200, height=100, target_panel=BarBox(x1=0, y1=0, x2=1, y2=1),
                      numbers={"target_hp": box})


def cache_without_worker(monkeypatch):
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    return FarmIdentityCache(profile(), None)


def test_identity_cache_ignores_hp_changes_but_rejects_name_change_and_old_result(monkeypatch):
    cache = cache_without_worker(monkeypatch)
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[5:10, 5:20] = 240
    signature = cache.signature(image)
    observation = TargetObservation(at=1, kind="monster", name="Gremlin", hp=.8,
                                     confidence=.95)
    now = time.monotonic()
    cache.result = (now, signature, observation, 0, 52, [])
    image[60:80, 100:180] = 240
    result, level, _ = cache.snapshot(image, 10.)
    assert result.name == "Gremlin" and result.hp is None and result.dead is None
    assert level == 52
    image[5:10, 5:20] = 0
    assert cache.snapshot(image, 10.)[0].name is None
    cache.result = (now - 3, signature, observation, 0, 52, [])
    assert cache.snapshot(image, 10.)[0].name is None
    cache.invalidate()
    assert cache.result is None and cache.epoch == 1


def test_blocked_background_ocr_does_not_block_snapshot(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    reader = Mock()

    def read(image, at, numbers):
        entered.set()
        release.wait(2)
        return TargetObservation(at=at, name="Gremlin", kind="monster", confidence=.95)

    reader.read.side_effect = read
    monkeypatch.setattr("hid_pilot.farm_observer.TargetReader", lambda profile: reader)
    cache = FarmIdentityCache(profile(), None)
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    try:
        cache.snapshot(image, 0.)
        assert entered.wait(1)
        assert cache.snapshot(image, .3)[0].hp is None
        assert cache.worker.is_alive()
    finally:
        release.set()
        cache.close()
    assert not cache.worker.is_alive()


def test_slow_numeric_first_pass_retries_fresh_frame_instead_of_stopping(monkeypatch):
    now = [0.]
    box = BarBox(x1=0, y1=0, x2=1, y2=.2)
    own = BarProfile(width=200, height=100, bars={name: box for name in ("hp", "mp", "target_hp")})
    calls = [0]

    def read_detail(self, image, name):
        calls[0] += 1
        if calls[0] == 1:
            now[0] += 1.5
        return 1., "OCR"

    monkeypatch.setattr(BarProfile, "read_detail", read_detail)
    controller = Mock()
    controller.capture.return_value = np.zeros((100, 200, 3), dtype=np.uint8)
    controller.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
    farmer = Farmer(controller, own, FarmConfig(rest="="), Mock(), lambda: now[0])
    assert farmer.observe() == (1., 1., None)
    assert controller.capture.call_count == 2
    controller.key.assert_not_called()


def test_anonymous_death_count_requires_two_zero_frames_and_never_counts_loss():
    farmer, _, _, _ = harness(pickup_loot=False)
    readings = iter([.8, None])
    farmer.observe = lambda: (1., 1., next(readings, None))
    farmer.fight(1.)
    assert sum(farmer.mob_statistics.counts.values()) == 0
    readings = iter([.8, 0., None])
    farmer.fight(1.)
    assert sum(farmer.mob_statistics.counts.values()) == 0
    readings = iter([.8, 0., 0.])
    farmer.fight(1.)
    assert sum(farmer.mob_statistics.counts.values()) == 1
    assert farmer.training_kills == 1


def test_combat_poll_period_includes_processing_time():
    farmer, _, now, _ = harness(pickup_loot=False)
    starts = []

    def observe():
        starts.append(now[0])
        now[0] += .1
        return 1., 1., 0. if len(starts) >= 4 else 1. - .2 * len(starts)

    farmer.observe = observe
    farmer.fight(1.)
    assert len(starts) == 5
    assert all(abs(starts[index + 1] - starts[index] - 1 / 3) < .001 for index in range(3))


def test_named_death_is_not_counted_again_by_combat_confirmation():
    farmer, _, _, _ = harness(pickup_loot=False)
    readings = iter([.8, .6, 0., 0.])
    farmer.last_target = TargetObservation(at=0., kind="monster", name="Gremlin", level=1,
                                          hp=1., dead=False, confidence=.95)

    def observe():
        hp = next(readings)
        observation = farmer.last_target.model_copy(update={"at": farmer.clock(), "hp": hp, "dead": hp == 0})
        farmer.last_target = observation
        farmer.mob_statistics.observe(observation, farmer.clock())
        return 1., 1., hp

    farmer.observe = observe
    farmer.fight(1.)
    assert farmer.mob_statistics.counts == {"Gremlin": 1}
    assert farmer.training_kills == 1


def test_background_level_is_read_without_name_panel(monkeypatch):
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    monkeypatch.setattr("hid_pilot.farm_observer.time.monotonic", lambda: 0.)
    numbers = Mock()
    numbers.detect.return_value = [(None, "22", .99)]
    monkeypatch.setattr("hid_pilot.farm_observer.background_numbers", lambda: numbers)
    box = BarBox(x1=.1, y1=.1, x2=.5, y2=.15)
    cache = FarmIdentityCache(BarProfile(width=200, height=100, target_level=box), None)
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    cache.pending = (image, 0., b"", 0)
    cache.stop_event = Mock()
    cache.stop_event.wait.side_effect = [False, True]
    cache._run()
    assert cache.snapshot(image, 0.)[0].level == 22


def test_slow_identity_publishes_before_party_without_losing_levels(monkeypatch):
    cache = cache_without_worker(monkeypatch)
    now = [0.]
    monkeypatch.setattr("hid_pilot.farm_observer.time.monotonic", lambda: now[0])
    monkeypatch.setattr(BarProfile, "read_level", lambda *args: 32)
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    signature = cache.signature(image)

    def slow_read(*args):
        now[0] = 3.
        return TargetObservation(at=0., name="Mob", level=31, kind="monster", confidence=.99)

    cache.reader.read = Mock(side_effect=slow_read)
    cache.party = Mock()
    monkeypatch.setattr("hid_pilot.farm_observer.read_party_bars", lambda *args: [])

    def party(*args):
        observation, level, _ = cache.snapshot(image, now[0])
        assert observation.name == "Mob"
        assert observation.level == 31
        assert level == 32
        now[0] += .5
        return []

    monkeypatch.setattr("hid_pilot.farm_observer.read_party", party)
    cache.pending = (image, 0., signature, 0)
    cache.stop_event = Mock()
    cache.stop_event.wait.side_effect = [False, True]
    cache._run()
    assert cache.snapshot(image, now[0])[0].name == "Mob"


def test_unchanged_identity_reuses_ocr_without_reusing_hp(monkeypatch):
    cache = cache_without_worker(monkeypatch)
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    signature = cache.signature(image)
    cache.identity_result = (signature, 0, TargetObservation(at=0, name="Mob", kind="monster",
                                                            hp=.9, confidence=.99))
    cache.reader.read = Mock()
    cache.pending = (image, time.monotonic(), signature, 0)
    cache.stop_event = Mock()
    cache.stop_event.wait.side_effect = [False, True]
    cache._run()
    cache.reader.read.assert_not_called()
    observation, _, _ = cache.snapshot(image, time.monotonic())
    assert observation.name == "Mob"
    assert observation.hp is None


def test_changed_own_level_crop_rejects_cached_level(monkeypatch):
    cache = cache_without_worker(monkeypatch)
    cache.profile.own_level = BarBox(x1=0, y1=0, x2=.2, y2=.2)
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    from hid_pilot.target import target_text_signature

    cache.own_signature = target_text_signature(cache.profile.crop(image, cache.profile.own_level))
    signature = cache.signature(image)
    cache.result = (time.monotonic(), signature, TargetObservation(at=0), 0, 32, [])
    image[3:10, 3:10] = 230
    assert cache.snapshot(image, time.monotonic())[1] is None


def test_own_level_survives_new_target_but_not_changed_digits(monkeypatch):
    from hid_pilot.target import target_text_signature

    cache = cache_without_worker(monkeypatch)
    cache.profile.own_level = BarBox(x1=0, y1=0, x2=.2, y2=.2)
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    own = target_text_signature(cache.profile.crop(image, cache.profile.own_level))
    cache.own_level_result = (own, 32)
    cache.invalidate()
    assert cache.snapshot(image, time.monotonic())[1] == 32
    image[3:10, 3:10] = 230
    assert cache.snapshot(image, time.monotonic())[1] is None


def test_target_invalidation_resets_name_votes(monkeypatch):
    cache = cache_without_worker(monkeypatch)
    cache.consensus = Mock()
    cache.invalidate()
    cache.consensus.reset_target.assert_called_once()


def test_party_resources_survive_target_reset_slow_ocr_and_hidden_panel(monkeypatch):
    from test_party import profile as party_profile, scene
    from hid_pilot.party import PartyMember
    from hid_pilot.target import target_text_signature

    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    config = party_profile()
    cache = FarmIdentityCache(BarProfile(width=200, height=800), config)
    image, _ = scene(1)
    image[3:12, 30:100] = 220
    signature = target_text_signature(cache.profile.crop(image, config.box("name", 0)))
    cache.party_result = ([PartyMember(slot=1, name="Member0", confidence=.99, hp=1., mp=1.)],
                          {1: signature})
    cache.party_at = time.monotonic() - 10
    cache.result = (time.monotonic(), cache.signature(image), TargetObservation(at=0), 0, 32, [])
    assert cache.snapshot(image, time.monotonic())[2][0].hp == .5
    cache.invalidate()
    members = cache.snapshot(image, time.monotonic())[2]
    assert len(members) == 1
    assert members[0].name == "Member0"
    assert (members[0].hp, members[0].mp) == (.5, .25)
    cache.reader.read = Mock(return_value=TargetObservation(at=time.monotonic()))
    monkeypatch.setattr("hid_pilot.farm_observer.read_party",
                        lambda *args: [PartyMember(slot=1, hp=.5, mp=.25)])
    cache.pending = (image, time.monotonic(), cache.signature(image), cache.epoch)
    cache.stop_event = Mock()
    cache.stop_event.wait.side_effect = [False, True]
    cache._run()
    assert cache.party_result[0][0].name == "Member0"
    image[3:12, 30:100] = 0
    assert cache.snapshot(image, time.monotonic())[2][0].name is None
    assert cache.snapshot(np.zeros_like(image), time.monotonic())[2] == []
