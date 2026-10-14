from unittest.mock import Mock

import pytest
from test_healer import snapshot
from test_healer_runtime import harness
from test_train_healer import state

from hid_pilot.farm import FarmConfig, Farmer
from hid_pilot.healer import HealerConfig, HealPolicy
from hid_pilot.runner import Runner
from hid_pilot.train_healer import TrainConfig, TrainPolicy


def test_both_mp_buffs_repeat_independently_throughout_farming():
    policy = HealPolicy(HealerConfig(), 0.)
    policy.follow_needed = False
    policy.next_follow_at = 40000
    for index in range(4):
        at = 30 + index * 900
        first = policy.decide(snapshot(at=at), at)
        assert first.skill == "mp_buff"
        policy.committed(first, snapshot(at=at), at)
        second = policy.decide(snapshot(at=at + 1.4), at + 1.4)
        assert second.skill == "mp_buff2"
        assert second.target is None
        policy.committed(second, snapshot(at=at + 1.4), at + 1.4)
        assert policy.ready_at["mp_buff"] == at + 900
        assert policy.ready_at["mp_buff2"] == at + 901.4


@pytest.mark.parametrize("policy_type,config,scene", [(HealPolicy, HealerConfig, snapshot),
                                                      (TrainPolicy, TrainConfig, state)])
def test_death_stops_during_cast_even_with_unknown_mp(policy_type, config, scene):
    policy = policy_type(config(), 0.)
    policy.busy_until = 100
    dead = scene(10, hp=0).model_copy(update={"mp": None})
    assert policy.decide(dead, 10).kind == "stop"


def test_ten_hour_limits_apply_to_configs_and_outer_runner(monkeypatch, tmp_path):
    monkeypatch.setattr("hid_pilot.runner.time.monotonic", lambda: 10.)
    assert Runner(Mock(), tmp_path, Mock()).deadline == 36010
    for cfg in (HealerConfig, TrainConfig):
        assert cfg().max_seconds == 36000
        with pytest.raises(ValueError):
            cfg(max_seconds=36001)
    assert FarmConfig(rest="S").max_seconds == 36000


def test_f10_runtime_is_targetless_and_preserves_its_cooldown(monkeypatch, tmp_path):
    healer, controller, observer, initial, now = harness(monkeypatch, tmp_path)
    healer.config.mp_buff_key = "F11"
    healer.policy.ready_at["mp_buff"] = 100
    healer.policy.support_ready_at = 0
    healthy = initial.model_copy(update={"members": (initial.members[0].model_copy(update={"hp": 1.}),)})
    observer.snapshots.return_value = (healthy,)
    decision = healer.policy.decide(healthy, now[0])
    assert decision.skill == "mp_buff2"
    healer.execute(decision, healthy)
    controller.key.assert_called_once_with("F10", 100)
    controller.click.assert_not_called()
    assert healer.policy.ready_at["mp_buff2"] == 910


def test_death_during_runtime_does_not_send_movement(monkeypatch, tmp_path):
    healer, controller, observer, initial, _now = harness(monkeypatch, tmp_path)
    healer.policy.busy_until = 100
    healer.approach_until = 50
    observer.snapshots.return_value = (initial.model_copy(update={"hp": 0, "mp": None}),)
    with pytest.raises(RuntimeError, match="HP = 0"):
        healer.run(deadline=20)
    controller.key.assert_not_called()
    controller.click.assert_not_called()


def test_generic_farm_stops_on_zero_hp_before_retrying_unknown_mp():
    controller, profile = Mock(), Mock()
    profile.read.side_effect = [0., None, 1.]
    farmer = Farmer(controller, profile, FarmConfig(rest="S"), Mock(), lambda: 0.)
    with pytest.raises(RuntimeError, match="HP = 0"):
        farmer.observe()
    controller.key.assert_not_called()


def test_healer_loop_finishes_at_ten_hour_deadline(monkeypatch, tmp_path):
    healer, controller, observer, initial, now = harness(monkeypatch, tmp_path)
    healer.policy.next_follow_at = 40000
    healthy = initial.model_copy(update={"members": (initial.members[0].model_copy(update={"hp": 1.}),)})
    observer.snapshots.side_effect = lambda: (healthy.model_copy(update={"at": now[0]}),)
    controller.wait.side_effect = lambda _: now.__setitem__(0, now[0] + 1200)
    healer.run()
    assert now[0] == 36010
    observer.close.assert_called_once()


def test_train_uses_f10_during_collection_without_touching_tank():
    from test_train_healer import collecting

    policy = collecting(.8)
    policy.support_ready_at = 0
    policy.ready_at["mp_buff"] = 100
    decision = policy.decide(state(2.7, tank=.8), 2.7)
    assert (decision.skill, decision.target) == ("mp_buff2", None)
