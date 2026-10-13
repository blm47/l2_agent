import pytest
from test_healer_runtime import harness
from test_train_runtime import runtime


@pytest.mark.parametrize("factory", [harness, runtime])
def test_game_follow_macro_needs_no_mouse_or_recognized_party_leader(monkeypatch, tmp_path, factory):
    result = factory(monkeypatch, tmp_path)
    healer, controller, observer = result[:3]
    healer.config.follow_key = "9"
    healer.config.follow_select_mouse = False
    state = observer.snapshots.return_value[-1].model_copy(update={"members": (), "roster_known": False})
    observer.snapshots.return_value = (state,)
    healer.policy.follow_needed = True
    decision = healer.policy.decide(state, 10)
    assert decision.kind == "follow"
    healer.execute(decision, state)
    controller.key.assert_called_once_with("9", 100)
    controller.click.assert_not_called()
    controller.move.assert_not_called()


def test_mouse_selection_can_be_enabled_again(monkeypatch, tmp_path):
    healer, controller, observer, initial, _now = harness(monkeypatch, tmp_path)
    healer.config.follow_key = "9"
    healer.config.follow_select_mouse = True
    state = initial.model_copy(update={"members": (initial.members[0].model_copy(update={"hp": 1.}),)})
    observer.snapshots.return_value = (state,)
    healer.policy.follow_needed = True
    healer.execute(healer.policy.decide(state, 10), state)
    controller.click.assert_called_once()
    controller.key.assert_called_once_with("9", 100)


@pytest.mark.parametrize("factory", [harness, runtime])
def test_known_wrong_player_never_enables_mouse_in_macro_mode(monkeypatch, tmp_path, factory):
    healer, controller, observer = factory(monkeypatch, tmp_path)[:3]
    healer.config.follow_key = "9"
    healer.config.follow_select_mouse = False
    initial = observer.snapshots.return_value[-1]
    members = tuple(member.model_copy(update={"hp": 1., "name": "Leader" if member.slot == 1 else None,
                                              "confidence": .99}) for member in initial.members)
    current = initial.model_copy(update={"members": members, "selected_name": "Other", "selected_class": "Hawkeye",
                                         "selected_confidence": .99, "paralyzed_slots": ()})
    observer.snapshots.return_value = (current,)
    healer.policy.follow_needed = True
    healer.execute(healer.policy.decide(current, 10), current)
    controller.click.assert_not_called()
    controller.key.assert_called_once_with("9", 100)
    inputs = [call[0] for call in controller.mock_calls if call[0] in {"click", "key", "move"}]
    assert inputs == ["key"]
