from test_healer import snapshot
from test_healer_runtime import harness

from hid_pilot.healer import HealDecision, HealerConfig, HealPolicy


def chained_policy():
    policy = HealPolicy(HealerConfig(mp_buff_key=None, major_cast=5, battle_cast=3), 0.)
    policy.chain_heals = True
    policy.follow_needed = False
    policy.next_follow_at = 1000
    policy.ready_at["balance"] = 1000
    return policy


def test_next_heal_is_allowed_before_previous_cast_finishes():
    policy = chained_policy()
    policy.ingest(snapshot(at=0, party=(.8, 1.)))
    policy.ingest(snapshot(at=.2, party=(.6, 1.)))
    current = snapshot(at=.4, party=(.4, 1.))
    first = policy.decide(current, .4)
    assert first.skill == "battle"
    policy.committed(first, current, .4)
    second = policy.decide(snapshot(at=.6, party=(.35, 1.)), .6)
    assert second.skill == "major"
    assert policy.ready_at["battle"] > .6
    assert not policy.stun_suspected


def test_thirty_seconds_without_effect_requests_follow_and_resets_after_command():
    policy = chained_policy()
    for index in range(151):
        at = index * .2
        current = snapshot(at=at, party=(.4, 1.))
        decision = policy.decide(current, at)
        if at < 30 and decision.kind == "cast":
            policy.committed(decision, current, at)
        if at < 30:
            assert not policy.recovery_follow
    assert (decision.kind, decision.target) == ("follow", "@slot1")
    assert not policy.stun_suspected
    policy.committed(decision, current, 30)
    assert not policy.recovery_follow
    assert policy.heal_attempt_since is None


def test_observed_gain_resets_failed_heal_window():
    policy = chained_policy()
    initial = snapshot(at=0, party=(.4, 1.))
    policy.ingest(initial)
    policy.committed(HealDecision(kind="cast", skill="major", target="@slot1", reason="тест"), initial, 0)
    for index in range(1, 100):
        policy.ingest(snapshot(at=index * .2, party=(.4, 1.)))
    policy.ingest(snapshot(at=20, party=(.42, 1.)))
    assert policy.heal_attempt_since is None
    assert not policy.recovery_follow


def test_runtime_reserves_short_input_interval_instead_of_cast_wait(monkeypatch, tmp_path):
    healer, controller, observer, state, now = harness(monkeypatch, tmp_path)
    healer.policy.chain_heals = True
    healer.config.chain_heals = True
    observer.snapshots.return_value = (state,)
    decision = healer.policy.decide(state, now[0])
    healer.execute(decision, state)
    assert healer.policy.busy_until == now[0] + healer.config.heal_press_interval
    controller.key.assert_called_once()
