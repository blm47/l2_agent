
import pytest
from test_healer_runtime import harness
from test_train_healer import state

from hid_pilot.healer import HealDecision
from hid_pilot.healer_runtime import TargetChanged
from hid_pilot.train_healer import TrainConfig, TrainPhase, TrainPolicy
from hid_pilot.train_runtime import TrainHealer


def runtime(monkeypatch, tmp_path):
    base, controller, observer, _snapshot, now = harness(monkeypatch, tmp_path)
    base.__class__ = TrainHealer
    base.config = TrainConfig(aura_enabled=False, self_rescue_key=None)
    base.policy = TrainPolicy(base.config, 9.5)
    base.policy.ingest(state(9.5))
    base.policy.phase = TrainPhase.COMBAT
    base.selecting = None
    observer.snapshots.return_value = (state(10., paralyzed=(2,)),)
    return base, controller, observer, now


def test_purify_selects_target_before_f4_and_reserves_own_cooldown(monkeypatch, tmp_path):
    healer, controller, observer, _now = runtime(monkeypatch, tmp_path)
    calls = []
    controller.click.side_effect = lambda *args, **kwargs: calls.append("select")
    controller.key.side_effect = lambda *args: calls.append("key")
    healer.execute(HealDecision(kind="cast", skill="purify", target="@slot2", reason="test"),
                   observer.snapshots.return_value[-1])
    assert calls == ["select", "key"]
    controller.key.assert_called_once_with("F4", 100)
    assert "purify" in healer.policy.ready_at
    assert healer.policy.ready("battle", 10.)


def test_disappearing_paralysis_cancels_f4_after_target_selection(monkeypatch, tmp_path):
    healer, controller, observer, _now = runtime(monkeypatch, tmp_path)
    controller.click.side_effect = lambda *args, **kwargs: setattr(
        observer.snapshots, "return_value", (state(10.),))
    with pytest.raises(TargetChanged):
        healer.execute(HealDecision(kind="cast", skill="purify", target="@slot2", reason="test"), state(10., paralyzed=(2,)))
    controller.key.assert_not_called()


def test_unknown_state_never_sends_recovery_movement(monkeypatch, tmp_path):
    healer, controller, _observer, _now = runtime(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="движения запрещены"):
        healer.recover_unknown_state(50.)
    controller.recover_view.assert_not_called()
    controller.key.assert_not_called()


def test_tank_purify_is_cancelled_if_collecting_begins_during_navigation(monkeypatch, tmp_path):
    healer, controller, observer, _now = runtime(monkeypatch, tmp_path)
    snapshot = state(10., paralyzed=(3,))
    observer.snapshots.return_value = (snapshot,)

    def click(*args, verify):
        healer.policy.phase = TrainPhase.COLLECT
        verify()

    controller.click.side_effect = click
    with pytest.raises(TargetChanged):
        healer.execute(HealDecision(kind="cast", skill="purify", target="@slot3", reason="test"), snapshot)
    controller.key.assert_not_called()


def test_purify_priority_is_not_blocked_by_non_emergency_own_hp(monkeypatch, tmp_path):
    healer, controller, observer, _now = runtime(monkeypatch, tmp_path)
    snapshot = state(10., hp=.4, paralyzed=(1,))
    observer.snapshots.return_value = (snapshot,)
    controller.click.side_effect = lambda *args, verify: verify()
    healer.execute(HealDecision(kind="cast", skill="purify", target="@slot1", reason="test"), snapshot)
    controller.key.assert_called_once_with("F4", 100)


def test_actual_train_constructor_preserves_shared_cast_and_purify_cooldowns(monkeypatch, tmp_path):
    base, controller, _observer, _snapshot, _now = harness(monkeypatch, tmp_path)
    base.policy.ready_at.update(balance=40., purify=14.)
    base.policy.busy_until = 13.
    base.save_timers()
    config = TrainConfig()
    healer = TrainHealer(controller, base.own, base.party, config, Mock(), base.state_path, Mock())
    try:
        assert healer.policy.ready_at["balance"] == 40.
        assert healer.policy.ready_at["purify"] == 14.
        assert healer.policy.busy_until == 13.
        assert config.poll_seconds == .1
        assert healer.policy.phase == TrainPhase.UNKNOWN
    finally:
        healer.observer.close()
from unittest.mock import Mock
