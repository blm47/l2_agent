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
    assert farmer.observe() == (1., 1., 1.)
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
