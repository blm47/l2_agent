from unittest.mock import Mock

import pytest
from test_train_healer import state
from test_train_runtime import runtime

from hid_pilot.healer import HealDecision
from hid_pilot.healer_runtime import TargetChanged
from hid_pilot.train_healer import TrainConfig, TrainPolicy


def overwhelmed(strong=True):
    policy = TrainPolicy(TrainConfig(mass_major_cast=.4, verify_seconds=.1), 0.)
    policy.ready_at["balance"] = 100.
    policy.ready_at["area_heal"] = 100.
    initial = state(0., tank=.48)
    policy.ingest(initial)
    policy.committed(HealDecision(kind="cast", skill="mass_major", reason="test"), initial, 0.)
    for index in range(1, 8):
        current = state(index / 10, tank=.48 - index * (.02 if strong else .001))
        policy.ingest(current)
    return policy, current


def test_strong_damage_after_mass_heal_allows_targeted_battle_without_hp_gain():
    policy, current = overwhelmed()
    assert "@slot3" not in policy.reachable_until
    result = policy.decide(current, current.at)
    assert (result.skill, result.target) == ("battle", "@slot3")


def test_slow_damage_without_mass_effect_does_not_allow_targeted_heal():
    policy, current = overwhelmed(False)
    result = policy.decide(current, current.at)
    assert result.target is None or result.kind == "follow"
    assert not policy.needs_single(current, "@slot3")


def test_single_large_hit_does_not_confirm_continuing_strong_damage():
    policy, current = overwhelmed(False)
    current = state(.6, tank=.38)
    policy.ingest(current)
    assert not policy.urgent_single(current, "@slot3")


def test_destroyer_still_blocks_emergency_targeted_heal():
    policy, current = overwhelmed()
    current = current.model_copy(update={"destroyer_slots": (3,)})
    assert not policy.urgent_single(current, "@slot3")
    assert not policy.needs_single(current, "@slot3")


@pytest.mark.parametrize("hp, expected", [(.35, "area_heal"), (.36, "group")])
def test_space_heals_low_party_hp_when_balance_on_cooldown(hp, expected):
    policy = TrainPolicy(TrainConfig(), 0.)
    policy.ready_at["balance"] = 30.
    current = state(0., tank=hp)
    result = policy.decide(current, 0.)
    assert (result.skill, result.target) == (expected, None)


def test_space_cooldown_is_twenty_five_seconds_and_cast_is_point_four():
    policy = TrainPolicy(TrainConfig(), 0.)
    policy.ready_at["balance"] = 100.
    current = state(0., tank=.3)
    decision = policy.decide(current, 0.)
    policy.committed(decision, current, 0.)
    assert policy.config.cast_seconds("area_heal") == .4
    assert policy.ready_at["area_heal"] == 25.
    assert not policy.ready("area_heal", 24.99)
    assert policy.ready("area_heal", 25.)
    assert policy.decide(state(.1, tank=.3), .1).kind == "wait"


def test_ready_balance_keeps_emergency_priority():
    policy = TrainPolicy(TrainConfig(), 0.)
    assert policy.decide(state(0., tank=.1), 0.).skill == "balance"


def test_runtime_space_has_no_target_click_and_schedules_post_heal_step(monkeypatch, tmp_path):
    healer, controller, observer, now = runtime(monkeypatch, tmp_path)
    current = state(now[0], tank=.3)
    observer.snapshots.return_value = (current,)
    healer.policy.ready_at["balance"] = now[0] + 30
    healer.step_after_heal = Mock()
    decision = healer.policy.decide(current, now[0])
    healer.execute(decision, current)
    controller.key.assert_called_once_with("SPACE", 100)
    controller.click.assert_not_called()
    healer.step_after_heal.assert_called_once()
    assert healer.active_cast[1] == now[0] + .4
    healer.policy.ready_at.clear()
    healer.restore_timers()
    assert healer.policy.ready_at["area_heal"] == now[0] + 25


def test_emergency_target_is_selected_but_new_snapshot_can_cancel_cast(monkeypatch, tmp_path):
    healer, controller, observer, now = runtime(monkeypatch, tmp_path)
    healer.policy, current = overwhelmed()
    now[0] = current.at
    observer.snapshots.return_value = (current,)
    healer.step_after_heal = Mock()
    decision = healer.policy.decide(current, now[0])

    def click(*args, verify):
        observer.snapshots.return_value = (state(.8),)
        now[0] = .8
        verify()

    controller.click.side_effect = click
    with pytest.raises(TargetChanged):
        healer.execute(decision, current)
    controller.click.assert_called_once()
    controller.key.assert_not_called()


def test_emergency_single_heal_clicks_member_before_battle_key(monkeypatch, tmp_path):
    healer, controller, observer, now = runtime(monkeypatch, tmp_path)
    healer.policy, current = overwhelmed()
    now[0] = current.at
    observer.snapshots.return_value = (current,)
    healer.step_after_heal = Mock()
    order = []
    controller.click.side_effect = lambda *args, verify: (verify(), order.append("click"))
    controller.key.side_effect = lambda *args: order.append("key")
    healer.execute(healer.policy.decide(current, now[0]), current)
    assert order == ["click", "key"]
    controller.key.assert_called_once_with("2", 100)


def test_old_self_probe_cannot_bypass_space_cooldown():
    policy = TrainPolicy(TrainConfig(), 0.)
    current = state(3., hp=.3, tank=.3, others=(.3, .3))
    policy.ingest(current)
    from hid_pilot.train_healer import TrainPhase

    policy.phase = TrainPhase.COMBAT
    policy.self_attempt_since, policy.self_attempt_count = 0., 2
    policy.ready_at.update(balance=30., self_rescue=30., area_heal=25.)
    result = policy.decide(current, 3.)
    assert result.kind != "probe"
    assert result.skill != "area_heal"
