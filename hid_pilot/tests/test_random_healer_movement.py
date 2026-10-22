from pathlib import Path
from unittest.mock import Mock

import pytest

from hid_pilot.healer import HealDecision
from hid_pilot.script import parse_script, script_variables
from test_farm import harness as farm_harness
from test_healer_runtime import harness
from test_train_runtime import runtime as train_runtime


@pytest.mark.parametrize("train", [False, True])
def test_bishop_periodic_movement_uses_same_timer_and_returns_to_follow(monkeypatch, tmp_path, train):
    if train:
        healer, controller, observer, now = train_runtime(monkeypatch, tmp_path)
    else:
        healer, controller, observer, _state, now = harness(monkeypatch, tmp_path)
    state = observer.snapshots.return_value[-1]
    state = state.model_copy(update={"members": tuple(m.model_copy(update={"hp": 1.}) for m in state.members)})
    observer.snapshots.return_value = (state,)
    healer.config.random_movement = True
    healer.next_random_move = 10
    healer.policy.busy_until = 10
    healer.policy.decide = Mock(return_value=HealDecision(kind="wait", reason="test"))
    monkeypatch.setattr("hid_pilot.healer_runtime.random.randint", lambda low, high: 1700)
    monkeypatch.setattr("hid_pilot.healer_runtime.random.choice", lambda keys: "D")
    monkeypatch.setattr("hid_pilot.healer_runtime.random.uniform", lambda low, high: 45.)
    assert healer.try_random_movement(100)
    controller.key.assert_called_once_with("D", 1700)
    assert healer.next_random_move == 55
    assert healer.policy.follow_needed
    assert not healer.try_random_movement(100)


@pytest.mark.parametrize("block", ["disabled", "cast", "priority", "missing", "deadline", "not_due"])
def test_bishop_movement_is_optional_and_yields_to_safety(monkeypatch, tmp_path, block):
    healer, controller, observer, state, _now = harness(monkeypatch, tmp_path)
    monkeypatch.setattr("hid_pilot.healer_runtime.random.randint", lambda low, high: high)
    healer.config.random_movement = True
    healer.next_random_move = 10
    healer.policy.decide = Mock(return_value=HealDecision(kind="wait", reason="test"))
    state = state.model_copy(update={"members": tuple(m.model_copy(update={"hp": 1.}) for m in state.members)})
    observer.snapshots.return_value = (state,)
    if block == "disabled":
        healer.config.random_movement = False
    elif block == "cast":
        healer.active_cast = (HealDecision(kind="cast", skill="major", reason="test"), 11, False)
    elif block == "priority":
        healer.policy.decide.return_value = HealDecision(kind="cast", skill="major", reason="test")
    elif block == "missing":
        observer.snapshots.return_value = (state.model_copy(update={"mp": None}),)
    elif block == "not_due":
        healer.next_random_move = 11
    assert not healer.try_random_movement(10.5 if block == "deadline" else 100)
    controller.key.assert_not_called()


def test_bishop_farm_moves_between_fights_and_respects_switch(monkeypatch):
    farm, controller, _state = farm_harness()
    farm.config.random_movement = True
    farm.next_random_move = 0
    monkeypatch.setattr("hid_pilot.farm.random.randint", lambda low, high: 1500)
    assert farm.try_random_movement()
    key, duration = controller.key.call_args.args
    assert key in "WASD" and duration == 1500
    assert 30 <= farm.next_random_move <= 60
    farm.config.random_movement = False
    farm.next_random_move = 0
    assert not farm.try_random_movement()


@pytest.mark.parametrize("script", ["farm_ee", "farm_bish", "healer", "healer-train"])
def test_all_ee_and_bishop_scripts_expose_movement_switch(script):
    path = Path(__file__).parents[1] / "extensions" / "scripts" / f"{script}.pilot"
    text = path.read_text("utf-8")
    assert script_variables(text)["random_movement"] == "true"
    assert parse_script(text)


@pytest.mark.parametrize("kind", ["bishop", "train", "farm"])
@pytest.mark.parametrize("protocol,maximum", [("HID_PILOT_V2", 1000),
                                              ("HID_PILOT_V3", 1000), ("HID_PILOT_V4", 3000)])
@pytest.mark.parametrize("endpoint", ["minimum", "maximum"])
def test_all_bishop_movement_ranges_follow_firmware_limits(monkeypatch, tmp_path, kind, protocol, maximum, endpoint):
    if kind == "farm":
        actor, controller, _state = farm_harness()
        module = "hid_pilot.farm"
    else:
        if kind == "train":
            actor, controller, observer, _now = train_runtime(monkeypatch, tmp_path)
        else:
            actor, controller, observer, _state, _now = harness(monkeypatch, tmp_path)
        state = observer.snapshots.return_value[-1]
        observer.snapshots.return_value = (state.model_copy(update={"members": tuple(
            m.model_copy(update={"hp": 1.}) for m in state.members)}),)
        actor.policy.busy_until = 0
        actor.policy.decide = Mock(return_value=HealDecision(kind="wait", reason="test"))
        module = "hid_pilot.healer_runtime"
    actor.config.random_movement = True
    actor.next_random_move = 0
    controller.transport.protocol = protocol
    pick = Mock(side_effect=lambda low, high: low if endpoint == "minimum" else high)
    monkeypatch.setattr(module + ".random.randint", pick)
    assert actor.try_random_movement() if kind == "farm" else actor.try_random_movement(100)
    pick.assert_called_once_with(300, maximum)
    assert controller.key.call_args.args[1] == (300 if endpoint == "minimum" else maximum)
