from unittest.mock import Mock

import pytest
from test_healer import snapshot
from test_healer_runtime import harness

from hid_pilot.auto_loot import AutoLoot
from hid_pilot.healer import HealDecision, HealerConfig, HealPolicy
from hid_pilot.mouse_path import mouse_path
from hid_pilot.train_healer import TrainConfig


def test_curved_paths_vary_stay_inside_window_and_end_exactly():
    paths = [mouse_path((10, 10), (850, 450), (0, 0, 900, 500)) for _ in range(10)]
    assert len({tuple(path) for path in paths}) > 1
    for path in paths:
        assert path[-1] == (850, 450)
        assert all(0 <= x < 900 and 0 <= y < 500 for x, y in path)


def test_dense_arc_has_small_steps_and_smooth_acceleration(monkeypatch):
    import math

    monkeypatch.setattr("hid_pilot.mouse_path.random.uniform", lambda low, high: high)
    path = mouse_path((50, 50), (850, 450), (0, 0, 900, 500))
    assert len(path) > 100
    steps = [math.dist(a, b) for a, b in zip([(50, 50), *path], path)]
    assert max(steps) < 16
    assert steps[0] < max(steps) and steps[-1] < max(steps)


def test_follow_and_buff_variation_never_shortens_cooldowns(monkeypatch):
    policy = HealPolicy(HealerConfig(), 0.)
    policy.humanize = True
    monkeypatch.setattr("hid_pilot.healer.random.uniform", lambda low, high: high)
    policy.committed(HealDecision(kind="follow", target="@slot1", reason="тест"), snapshot(), 10)
    assert policy.next_follow_at == 14.4
    policy.committed(HealDecision(kind="cast", skill="mp_buff", reason="тест"), snapshot(), 10)
    assert policy.ready_at["mp_buff"] == 915


def test_loot_series_has_three_presses_and_can_be_interrupted(monkeypatch):
    monkeypatch.setattr("hid_pilot.auto_loot.random.uniform", lambda low, high: (low + high) / 2)
    monkeypatch.setattr("hid_pilot.auto_loot.random.randint", lambda low, high: 3)
    loot = AutoLoot(0)
    assert not loot.due(3.9) and loot.due(4)
    loot.committed(4)
    assert loot.remaining == 2 and loot.next_at == 4.5
    loot.committed(4.5)
    assert loot.remaining == 1 and loot.next_at == 5
    loot.interrupt(4.7)
    assert loot.remaining == 0 and loot.next_at == 8.7


@pytest.mark.parametrize("injured", [False, True])
def test_runtime_loot_never_preempts_healing(monkeypatch, tmp_path, injured):
    healer, controller, observer, initial, now = harness(monkeypatch, tmp_path)
    healer.config.auto_loot = True
    healer.policy.next_follow_at = 100
    healer.loot.next_at = 10
    state = initial.model_copy(update={"members": (initial.members[0].model_copy(update={"hp": .4 if injured else 1.}),)})
    observer.snapshots.side_effect = lambda: (state.model_copy(update={"at": now[0]}),)
    controller.wait.side_effect = lambda _: now.__setitem__(0, now[0] + .1)
    healer.execute = Mock()
    healer.run(deadline=10.3)
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert ("C" in keys) is not injured
    assert healer.execute.called is injured


def test_loot_defaults_are_enabled_for_normal_healer_only():
    assert HealerConfig().auto_loot
    assert not TrainConfig().auto_loot
    assert HealerConfig().loot_key == "C"


def test_urgent_heal_gets_no_extra_reaction_wait(monkeypatch, tmp_path):
    healer, controller, observer, initial, _now = harness(monkeypatch, tmp_path)
    healer.config.humanize = True
    state = initial.model_copy(update={"members": (initial.members[0].model_copy(update={"hp": .2}),)})
    observer.snapshots.return_value = (state,)
    decision = healer.policy.decide(state, 10)
    healer.execute(decision, state)
    assert all(call.args[0] >= .1 for call in controller.wait.call_args_list)
