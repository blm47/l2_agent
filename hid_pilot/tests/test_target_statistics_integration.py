from unittest.mock import Mock

import numpy as np
from test_healer_observer import profiles, scene
from test_player_target import row

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.farm import FarmConfig, Farmer
from hid_pilot.healer_observer import HealerObserver, HealerReader
from hid_pilot.target import TargetObservation


def test_background_identity_ocr_updates_monster_counter_without_control(monkeypatch):
    own, party = profiles()
    own.target_panel = BarBox(x1=.1, x2=.9, y1=.75, y2=.85)
    reader = HealerReader(own, party)
    ocr = Mock()
    ocr.detect.side_effect = [[row("originalNutta", 2)], [row("Gremlin", 2), row("0 / 58", 40)]]
    monkeypatch.setattr("hid_pilot.player_target.shared_numbers", lambda: ocr)
    now = [0.]
    monkeypatch.setattr("hid_pilot.healer_observer.time.monotonic", lambda: now[0])
    observer = HealerObserver(Mock(), reader, .1, lambda: False)
    for at, hp in ((0., 1.), (.5, .5)):
        observer.mob_statistics.observe(TargetObservation(at=at, kind="monster", name="Gremlin",
                                                         hp=hp, dead=False, confidence=.99), at)
    observer.identity_image = scene()
    observer.identity_at = now[0] = 1.
    observer.stop_event = Mock()
    observer.stop_event.is_set.side_effect = [False, False, True]

    def wait(seconds):
        now[0] += seconds
        observer.identity_at = now[0]

    observer.stop_event.wait.side_effect = wait
    observer._identity_loop()
    assert observer.mob_statistics.counts == {"Gremlin": 1}
    observer.desktop.capture.assert_not_called()


def test_farm_observations_count_and_report_monster_death(monkeypatch):
    panel = BarBox(x1=0, y1=0, x2=1, y2=.6)
    profile = BarProfile(width=200, height=200, target_panel=panel)
    now, hp = [0.], [1.]
    ocr = Mock()
    ocr.detect.side_effect = lambda image: [row("Gremlin", 2), row(f"{round(hp[0] * 100)} / 100", 40)]
    monkeypatch.setattr("hid_pilot.numbers.shared_numbers", lambda: ocr)
    monkeypatch.setattr("hid_pilot.target.shared_numbers", lambda: ocr)
    monkeypatch.setattr(BarProfile, "read_detail", lambda self, image, kind: (hp[0] if kind == "target_hp" else 1., "OCR"))
    controller, notify = Mock(), Mock()
    farm = Farmer(controller, profile, FarmConfig(rest="I"), notify, lambda: now[0])
    for index, (at, value) in enumerate(((0., 1.), (.5, .5), (1., 0.), (1.5, 0.), (60., 0.))):
        now[0], hp[0] = at, value
        image = np.zeros((200, 200, 3), dtype=np.uint8)
        image[5:10, index * 15:index * 15 + 10] = 240
        controller.capture.return_value = image
        farm._observe()
    assert farm.mob_statistics.counts == {"Gremlin": 1}
    assert "1. 1 — Gremlin" in notify.state.call_args.args[0]["text"]
    notify.assert_not_called()
    controller.key.assert_not_called()

