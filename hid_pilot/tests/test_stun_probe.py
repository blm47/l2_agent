import pytest
from test_healer import snapshot
from test_healer_runtime import harness
from test_train_healer import collecting, state
from test_train_runtime import runtime

from hid_pilot.healer import HealDecision, HealerConfig, HealPolicy
from hid_pilot.healer_runtime import TargetChanged
from hid_pilot.train_healer import TrainPhase


def suspected():
    policy = HealPolicy(HealerConfig(mp_buff_key=None), 0.)
    before = snapshot(at=0., hp=.4, party=(.4, .4))
    policy.ingest(before)
    policy.committed(HealDecision(kind="cast", skill="group", reason="test"), before, 0.)
    now = policy.busy_until
    current = snapshot(at=now, hp=.4, party=(.4, .4))
    result = policy.decide(current, now)
    assert result.kind == "probe"
    assert policy.stun_suspected
    return policy, current, now


def test_probe_starts_only_after_failed_cast_and_not_just_low_hp():
    policy = HealPolicy(HealerConfig(mp_buff_key=None), 0.)
    assert policy.decide(snapshot(at=0., hp=.4, party=(.4, .4)), 0.).kind == "cast"
    suspected()


@pytest.mark.parametrize("healed_target", ["self", "party"])
def test_observed_hp_gain_after_space_resumes_normal_healing(healed_target):
    policy, current, now = suspected()
    policy.begin_stun_probe(current, now)
    recovered = snapshot(at=now + .2, hp=.42 if healed_target == "self" else .4,
                         party=(.42 if healed_target == "party" else .4, .4))
    result = policy.decide(recovered, now + .2)
    assert not policy.stun_suspected
    assert result.kind != "probe"


def test_probe_waits_for_effect_not_for_stun_timer_to_expire():
    policy, current, now = suspected()
    policy.begin_stun_probe(current, now)
    for tick in range(1, 150):
        result = policy.decide(snapshot(at=now + tick * .2, hp=.4, party=(.4, .4)), now + tick * .2)
        assert result.kind == "probe"
    result = policy.decide(snapshot(at=now + 30., hp=.4, party=(.4, .4)), now + 30.)
    assert result.kind == "stop"


def test_small_gains_accumulate_and_damage_does_not_hide_later_heal():
    policy, current, now = suspected()
    policy.begin_stun_probe(current, now)
    policy.ingest(snapshot(at=now + .2, hp=.3, party=(.4, .4)))
    policy.begin_stun_probe(policy.previous, now + .2)
    policy.ingest(snapshot(at=now + .4, hp=.303, party=(.4, .4)))
    assert policy.stun_suspected
    policy.begin_stun_probe(policy.previous, now + .4)
    policy.ingest(snapshot(at=now + .6, hp=.306, party=(.4, .4)))
    assert not policy.stun_suspected


def test_stale_gap_and_changed_roster_do_not_prove_space_effect():
    policy, current, now = suspected()
    policy.begin_stun_probe(current, now)
    policy.ingest(snapshot(at=now + 2., hp=.8, party=(.4, .4)))
    assert policy.stun_suspected
    policy.ingest(snapshot(at=now + 2.2, hp=.9, party=(.4,)))
    assert policy.stun_suspected


def test_executor_holds_space_without_target_selection_and_processes_live_samples(monkeypatch, tmp_path):
    healer, controller, observer, current, _now = harness(monkeypatch, tmp_path)
    healer.policy.stun_suspected = True
    observer.snapshots.return_value = (current,)
    controller.key.side_effect = lambda *args: setattr(observer.snapshots, "return_value", (
        current, current.model_copy(update={"at": 10.2, "members": (
            current.members[0].model_copy(update={"hp": .42}),)})))
    healer.execute(HealDecision(kind="probe", reason="test"), current)
    controller.key.assert_called_once_with("SPACE", 500)
    controller.click.assert_not_called()
    assert not healer.policy.stun_suspected


def test_pause_during_probe_discards_effect_baseline_and_recovery_timer(monkeypatch, tmp_path):
    healer, controller, _observer, current, _now = harness(monkeypatch, tmp_path)
    healer.policy.stun_suspected = True
    controller.pause_generation = 0
    controller.key.side_effect = lambda *args: setattr(controller, "pause_generation", 1)
    with pytest.raises(TargetChanged, match="Пауза"):
        healer.execute(HealDecision(kind="probe", reason="test"), current)
    assert healer.policy.stun_suspected
    assert healer.policy.probe_started_at is None
    assert not healer.policy.probe_baseline


def test_train_collect_and_unknown_never_probe_but_combat_does():
    policy = collecting(.3)
    policy.stun_suspected = True
    assert policy.decide(state(2.7, tank=.3), 2.7).kind == "wait"
    assert policy.phase == TrainPhase.COLLECT
    policy.phase = TrainPhase.UNKNOWN
    assert policy.decide(state(2.8, tank=.3), 2.8).kind == "wait"
    policy.phase = TrainPhase.COMBAT
    assert policy.decide(state(2.9, tank=.3), 2.9).kind == "probe"


def test_train_probe_is_cancelled_when_phase_disallows_it(monkeypatch, tmp_path):
    healer, controller, observer, _now = runtime(monkeypatch, tmp_path)
    healer.policy.stun_suspected = True
    healer.policy.phase = TrainPhase.COLLECT
    observer.snapshots.return_value = (state(10., tank=.3),)
    with pytest.raises(TargetChanged):
        healer.execute(HealDecision(kind="probe", reason="test"), state(10., tank=.3))
    controller.key.assert_not_called()


def test_train_self_buff_is_enabled_and_allowed_after_start_delay_during_collect():
    policy = collecting(.3)
    assert policy.config.mp_buff_key == "F11"
    for tick in range(27, 300):
        policy.ingest(state(tick / 10, tank=.3))
    assert policy.decide(state(29.9, tank=.3), 29.9).skill != "mp_buff"
    result = policy.decide(state(30., tank=.3), 30.)
    assert (result.skill, result.target) == ("mp_buff", None)


def test_purify_without_observed_effect_can_trigger_stun_suspicion():
    from hid_pilot.train_healer import TrainConfig, TrainPolicy

    policy = TrainPolicy(TrainConfig(), 0.)
    initial = state(0., paralyzed=(2,))
    policy.ingest(initial)
    policy.phase = TrainPhase.COMBAT
    policy.committed(HealDecision(kind="cast", skill="purify", target="@slot2", reason="test"), initial, 0.)
    for tick in range(1, 11):
        policy.ingest(state(tick * .2, paralyzed=(2,)))
    assert policy.stun_suspected
    assert policy.decide(state(2., paralyzed=(2,)), 2.).kind == "probe"
