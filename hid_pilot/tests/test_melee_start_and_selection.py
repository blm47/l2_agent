from unittest.mock import Mock

import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.farm import prepare_extension
from hid_pilot.target import TargetObservation
from test_melee_farm import harness


def test_chat_pool_is_shuffled_and_consumed_without_repeats(monkeypatch):
    farmer, controller, _, state = harness()
    farmer.known_mobs = {"Gremlin": 1, "Cloudy Beast": 22, "Other Mob": 10}
    shuffle = Mock(side_effect=lambda names: names.reverse())
    monkeypatch.setattr("hid_pilot.farm_melee.random.shuffle", shuffle)
    controller.target_by_name.side_effect = lambda name: state.__setitem__(2, .8)
    for _ in range(3):
        state[2] = None
        assert farmer.acquire() == .8
    assert [call.args[0] for call in controller.target_by_name.call_args_list] == list(farmer.known_mobs)
    shuffle.assert_called_once()


def test_chat_skips_failed_and_temporarily_blocked_names():
    farmer, controller, now, state = harness()
    farmer.known_mobs = {"Gremlin": 1, "Cloudy Beast": 22, "Other Mob": 10}
    farmer.target_name_pool = list(farmer.known_mobs)
    farmer.unreachable_name, farmer.unreachable_selections = "Gremlin", 1
    farmer.unreachable_until["Cloudy Beast"] = now[0] + 60
    state[2] = None
    controller.target_by_name.side_effect = lambda name: state.__setitem__(2, .8)
    assert farmer.acquire() == .8
    controller.target_by_name.assert_called_once_with("Other Mob")


def test_next_target_skips_blocked_monster_until_cooldown_expires():
    farmer, controller, now, state = harness()
    farmer.last_target = TargetObservation(at=0, kind="monster", name="Gremlin", confidence=.99)
    farmer.unreachable_until["Gremlin"] = 60
    farmer.invalidate_target = Mock()
    with pytest.raises(RuntimeError, match="/target"):
        farmer.acquire()
    assert any(call.args[0] == "ESC" for call in controller.key.call_args_list)
    now[0] = 61
    assert farmer.acquire() == state[2]


@pytest.mark.parametrize("identity", [None, "unknown", "monster"])
def test_first_live_chat_target_stops_all_remaining_names(identity):
    farmer, controller, _, state = harness()
    state[2] = None
    farmer.known_mobs = {"Gremlin": 1, "Cloudy Beast": 22, "Other Mob": 10}

    def select(name):
        state[2] = .8
        farmer.last_target = (None if identity is None else
                              TargetObservation(at=0, kind=identity, name=None, confidence=0))

    controller.target_by_name.side_effect = select
    assert farmer.acquire() == .8
    controller.target_by_name.assert_called_once()
    assert controller.target_by_name.call_args.args[0] in farmer.known_mobs


def test_chat_acquisition_approaches_immediately_after_first_live_target():
    farmer, controller, _, state = harness()
    state[2] = None
    farmer.known_mobs = {"Gremlin": 1, "Cloudy Beast": 22}

    def select(name):
        state[2] = .8
        farmer.last_target = TargetObservation(at=0, kind="monster", name="OCR pending", confidence=.3)

    controller.target_by_name.side_effect = select
    target = farmer.acquire()
    state[2] = None
    farmer.fight(target)
    controller.target_by_name.assert_called_once()
    assert controller.target_by_name.call_args.args[0] in farmer.known_mobs
    assert controller.key.call_args_list[-1].args == (farmer.config.attack, 100)


@pytest.mark.parametrize("kind", ["player", "npc"])
def test_chat_does_not_accept_known_player_or_npc(kind):
    farmer, controller, _, state = harness()
    state[2] = None
    farmer.known_mobs = {"Gremlin": 1, "Cloudy Beast": 22}

    def select(name):
        state[2] = 1.
        farmer.last_target = TargetObservation(at=0, kind=kind, name=name, confidence=.99)

    controller.target_by_name.side_effect = select
    with pytest.raises(RuntimeError, match="/target"):
        farmer.acquire()
    assert controller.target_by_name.call_count == 2


@pytest.mark.parametrize("target", [None, 0.])
def test_chat_keeps_searching_without_living_target_hp(target):
    farmer, controller, _, state = harness()
    state[2] = target
    farmer.known_mobs = {"Gremlin": 1, "Cloudy Beast": 22}
    with pytest.raises(RuntimeError, match="/target"):
        farmer.acquire()
    assert controller.target_by_name.call_count == 2


@pytest.mark.parametrize("ocr_source", ["numbers", "party", "panel", "none"])
def test_farm_prepares_ocr_before_controller_is_started(tmp_path, monkeypatch, ocr_source):
    box = BarBox(x1=.1, x2=.8, y1=.1, y2=.11)
    profile = BarProfile(width=200, height=800, bars={key: box for key in ("hp", "mp", "target_hp")},
                         numbers={"hp": box} if ocr_source == "numbers" else {},
                         target_panel=box if ocr_source == "panel" else None)
    (tmp_path / "bars.json").write_text(profile.model_dump_json(), "utf-8")
    if ocr_source == "party":
        (tmp_path / "party.json").write_text("{}", "utf-8")
    reader = Mock()
    background = Mock()
    monkeypatch.setattr("hid_pilot.numbers.shared_numbers", lambda: reader)
    monkeypatch.setattr("hid_pilot.farm_observer.background_numbers", lambda: background)
    connect = Mock(side_effect=AssertionError("Pico не должен подключаться во время подготовки"))
    monkeypatch.setattr("hid_pilot.transport.PicoTransport", connect)
    prepare_extension(tmp_path, ("bars.json",))
    assert reader.prepare.call_count == (ocr_source != "none")
    assert background.prepare.call_count == (ocr_source in {"party", "panel"})
    connect.assert_not_called()


def test_farm_observer_reuses_prepared_background_engine(monkeypatch):
    from hid_pilot.farm_observer import FarmIdentityCache
    from hid_pilot.numbers import shared_numbers

    background = Mock()
    monkeypatch.setattr("hid_pilot.farm_observer.background_numbers", lambda: background)
    monkeypatch.setattr("hid_pilot.farm_observer.threading.Thread.start", lambda self: None)
    box = BarBox(x1=.1, x2=.8, y1=.1, y2=.11)
    cache = FarmIdentityCache(BarProfile(width=200, height=800, target_panel=box), None)
    assert cache.numbers is background
    assert cache.numbers is not shared_numbers()
