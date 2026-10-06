from unittest.mock import Mock

import pytest
from test_healer_observer import profiles, scene
from test_healer_runtime import harness

from hid_pilot.bars import BarBox
from hid_pilot.healer import HealDecision
from hid_pilot.healer_observer import HealerReader
from hid_pilot.player_target import PlayerIdentityReader


def row(text, y, score=.99):
    return ([[0, y], [100, y], [100, y + 8], [0, y + 8]], text, score)


def identities(monkeypatch, rows):
    own, party = profiles()
    own.target_player_panel = BarBox(x1=.1, x2=.9, y1=.75, y2=.85)
    reader = PlayerIdentityReader(own, party)
    ocr = Mock()
    ocr.detect.side_effect = [[row("originalNutta", 2)], rows]
    monkeypatch.setattr("hid_pilot.player_target.shared_numbers", lambda: ocr)
    return reader, own, party


def test_player_panel_reads_nickname_class_clan_and_matches_current_party(monkeypatch):
    reader, own, party = identities(monkeypatch, [row("AES", 45), row("Hawkeye", 23), row("originalNutta", 2)])
    image = scene()
    reader.update(image)
    observer = HealerReader(own, party)
    observer.identities = reader
    state = observer.read(image, 1)
    assert (state.selected_name, state.selected_class, state.selected_clan) == ("originalNutta", "Hawkeye", "AES")
    assert state.follows_selected("@slot1")
    image[600:605, 30:40] = 255
    assert not observer.read(image, 2).follows_selected("@slot1")


@pytest.mark.parametrize("rows", [[], [row("Mob", 2)],
                                   [row("Mob", 2), row("120 / 300", 23)],
                                   [row("originalNutta", 2, .8), row("Hawkeye", 23)]])
def test_unreliable_or_mob_panel_never_confirms_player(monkeypatch, rows):
    reader, own, party = identities(monkeypatch, rows)
    image = scene()
    reader.update(image)
    observer = HealerReader(own, party)
    observer.identities = reader
    assert not observer.read(image, 1).follows_selected("@slot1")


def test_changed_party_name_invalidates_cached_match(monkeypatch):
    reader, own, party = identities(monkeypatch, [row("originalNutta", 2), row("Hawkeye", 23)])
    image = scene()
    reader.update(image)
    observer = HealerReader(own, party)
    observer.identities = reader
    image[0:20] = 0
    state = observer.read(image, 1)
    assert state.selected_name == "originalNutta"
    assert not state.follows_selected("@slot1")


@pytest.mark.parametrize("same", [True, False])
def test_normal_follow_keeps_f1_but_skips_only_confirmed_same_player(monkeypatch, tmp_path, same):
    healer, controller, observer, state, _now = harness(monkeypatch, tmp_path)
    healer.config.follow_key = "F1"
    state = state.model_copy(update={"members": (state.members[0].model_copy(update={"hp": 1., "name": "originalNutta", "confidence": .99}),),
                                     "selected_name": "originalNutta" if same else "Other",
                                     "selected_class": "Hawkeye", "selected_confidence": .99})
    observer.snapshots.return_value = (state,)
    healer.policy.follow_needed = True
    healer.execute(HealDecision(kind="follow", target="@slot1", reason="тест"), state)
    assert controller.click.call_count == (0 if same else 1)
    controller.key.assert_called_once_with("F1", 100)
    assert controller.move.call_count == (0 if same else 1)


def test_train_follow_skips_double_click_for_confirmed_same_player(monkeypatch, tmp_path):
    from test_train_runtime import runtime

    from hid_pilot.train_healer import TrainPhase

    healer, controller, observer, _now = runtime(monkeypatch, tmp_path)
    state = observer.snapshots.return_value[-1]
    state = state.model_copy(update={"paralyzed_slots": (),
                                     "members": tuple(member.model_copy(update={"hp": 1., "name": "originalNutta" if member.slot == 1 else None,
                                                                                "confidence": .99}) for member in state.members),
                                     "selected_name": "originalNutta", "selected_class": "Hawkeye", "selected_confidence": .99})
    observer.snapshots.return_value = (state,)
    healer.policy.phase = TrainPhase.IDLE
    healer.execute(HealDecision(kind="follow", target="@slot1", reason="тест"), state)
    controller.click.assert_not_called()
    controller.move.assert_not_called()


def test_target_change_before_f1_cancels_approach(monkeypatch, tmp_path):
    from hid_pilot.healer_runtime import TargetChanged

    healer, controller, _observer, state, _now = harness(monkeypatch, tmp_path)
    healer.config.follow_key = "F1"
    state = state.model_copy(update={"members": (state.members[0].model_copy(update={"hp": 1., "name": "originalNutta", "confidence": .99}),),
                                     "selected_name": "originalNutta", "selected_class": "Hawkeye", "selected_confidence": .99})
    healer.latest = Mock(side_effect=[state, state.model_copy(update={"selected_name": "Other"})])
    with pytest.raises(TargetChanged, match="изменился перед F1"):
        healer.execute(HealDecision(kind="follow", target="@slot1", reason="тест"), state)
    controller.key.assert_not_called()
    controller.click.assert_not_called()
