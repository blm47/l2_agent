from unittest.mock import Mock

import pytest
from test_train_healer import collecting, state
from test_train_runtime import runtime

from hid_pilot.healer import HealDecision
from hid_pilot.healer_runtime import TargetChanged
from hid_pilot.train_healer import TrainConfig, TrainPhase, TrainPolicy


def mass_attempt(chain=False):
    policy = collecting(.4)
    policy.chain_heals = chain
    initial = state(2.7, tank=.4, others=(.5, 1.))
    decision = policy.decide(initial, initial.at)
    assert (decision.skill, decision.target) == ("group", None)
    policy.committed(decision, initial, initial.at)
    return policy


@pytest.mark.parametrize("chain", [False, True])
@pytest.mark.parametrize("helped", [False, True])
def test_mass_feedback_is_per_member_and_independent_of_chain_mode(chain, helped):
    policy = mass_attempt(chain)
    until = policy.mass_check[2]
    at = 2.7
    while at < until:
        at = min(at + .1, until)
        current = state(at, tank=.42 if helped else .4, others=(.5, 1.))
        policy.ingest(current)
    assert ("@slot3" in policy.reachable_until) is helped
    assert not policy.needs_single(current, "@slot3")
    assert not policy.needs_single(current, "@slot1")
    assert policy.emergency_follow(current, at).kind == "follow"
    policy.ready_at.update(balance=100., mass_major=100., group=100., avatar=100.)
    result = policy.decide(current, at)
    assert (result.kind, result.target) == ("follow", "@slot1")
    assert result.skill is None


@pytest.mark.parametrize("change", ["gap", "revision"])
def test_missing_observation_or_new_layout_never_proves_mass_effect(change):
    policy = mass_attempt()
    current = state(2.8 if change == "revision" else 5., tank=.6)
    if change == "revision":
        current = current.model_copy(update={"profile_revision": 1})
    policy.ingest(current)
    assert policy.mass_check is None
    assert not policy.needs_single(current, "@slot3")


def test_old_recovery_flag_cannot_start_follow_after_failed_heals():
    policy = collecting(.4)
    policy.recovery_follow = True
    policy.heal_attempt_since = -40.
    policy.heal_attempt_targets = {"@slot3"}
    policy.chain_heals = True
    decision = policy.decide(state(2.7, tank=.4), 2.7)
    assert decision.kind == "cast"
    assert decision.target is None
    assert not policy.recovery_follow


def test_mass_heal_without_ore_is_available_outside_collection():
    policy = TrainPolicy(TrainConfig(allow_ore=False), 0.)
    policy.ingest(state(0.))
    current = state(.1, hp=.6, others=(.6, .6), tank=.6)
    policy.ingest(current)
    assert policy.phase == TrainPhase.COMBAT
    result = policy.decide(current, .1)
    assert (result.skill, result.target) == ("group", None)


def test_expired_positive_effect_cannot_authorize_new_single_heal():
    policy = collecting(.4)
    policy.reachable_until["@slot3"] = 2.75
    assert not policy.needs_single(state(2.7, tank=.4), "@slot3")
    assert not policy.needs_single(state(2.8, tank=.4), "@slot3")


@pytest.mark.parametrize("key", list("WASD"))
def test_step_waits_for_cast_then_holds_one_random_key(monkeypatch, tmp_path, key):
    healer, controller, observer, now = runtime(monkeypatch, tmp_path)
    healer.active_cast = (HealDecision(kind="cast", skill="mass_major", reason="test"), 10.3, False)
    controller.pause_generation = 0
    controller.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
    observer.snapshots.side_effect = lambda: (state(now[0]),)
    choose = Mock(return_value=key)
    monkeypatch.setattr("hid_pilot.train_runtime.random.choice", choose)
    healer.step_after_heal()
    assert now[0] == pytest.approx(10.3)
    choose.assert_called_once_with(("W", "A", "S", "D"))
    controller.key.assert_called_once_with(key, 1000)


@pytest.mark.parametrize("change", ["pause", "death", "stale"])
def test_interrupted_cast_never_starts_random_movement(monkeypatch, tmp_path, change):
    healer, controller, observer, now = runtime(monkeypatch, tmp_path)
    healer.active_cast = (HealDecision(kind="cast", skill="group", reason="test"), 11., False)
    controller.pause_generation = 0

    def wait(seconds):
        now[0] += seconds
        if change == "pause":
            controller.pause_generation = 1
        elif change == "death":
            observer.snapshots.return_value = (state(now[0], hp=0.),)

    controller.wait.side_effect = wait
    with pytest.raises(TargetChanged):
        healer.step_after_heal()
    controller.key.assert_not_called()


def test_collection_does_not_stop_existing_follow(monkeypatch, tmp_path):
    healer, controller, _observer, _now = runtime(monkeypatch, tmp_path)
    healer.policy = collecting(.4)
    assert not healer.guard_approach(state(2.7, tank=.4))
    controller.key.assert_not_called()


def test_real_mass_cast_schedules_step_but_cancelled_cast_does_not(monkeypatch, tmp_path):
    healer, controller, observer, now = runtime(monkeypatch, tmp_path)
    current = state(now[0], tank=.6, others=(.6, .6))
    observer.snapshots.return_value = (current,)
    healer.step_after_heal = Mock()
    decision = healer.policy.decide(current, now[0])
    assert decision.skill == "group"
    healer.execute(decision, current)
    healer.step_after_heal.assert_called_once()
    healer.step_after_heal.reset_mock()
    with pytest.raises(TargetChanged):
        healer.execute(decision, current)
    healer.step_after_heal.assert_not_called()


def test_unconfirmed_single_heal_is_rejected_before_any_target_click(monkeypatch, tmp_path):
    healer, controller, observer, now = runtime(monkeypatch, tmp_path)
    current = state(now[0], tank=.4)
    observer.snapshots.return_value = (current,)
    with pytest.raises(TargetChanged):
        healer.execute(HealDecision(kind="cast", skill="major", target="@slot3", reason="test"), current)
    controller.click.assert_not_called()
    controller.key.assert_not_called()
