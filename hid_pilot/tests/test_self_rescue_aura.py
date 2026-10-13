from unittest.mock import Mock, call

import pytest

from test_healer import snapshot
from test_healer_runtime import harness
from test_train_healer import collecting, state

from hid_pilot.healer import HealDecision, HealerConfig, HealPolicy
from hid_pilot.skill_aura import SkillAura
from hid_pilot.controller import Stopped


def test_skill_four_is_targetless_rescue_with_measured_cast_and_eight_second_cooldown():
    policy = HealPolicy(HealerConfig(), 0)
    current = snapshot(at=3, hp=.4)
    policy.ingest(current)
    policy.self_attempt_since, policy.self_attempt_count = 0, 2
    decision = policy.decide(current, 3)
    assert (decision.skill, decision.target) == ("self_rescue", None)
    assert policy.config.self_rescue_key == "4"
    assert policy.config.purify_key == "F4"
    assert policy.config.cast_seconds("self_rescue") == .2
    policy.committed(decision, current, 3)
    assert policy.ready_at["self_rescue"] == 11


def test_space_can_rescue_self_while_four_is_on_cooldown():
    policy = HealPolicy(HealerConfig(), 0)
    current = snapshot(at=3, hp=.4)
    policy.ingest(current)
    policy.self_attempt_since, policy.self_attempt_count = 0, 2
    policy.ready_at["self_rescue"] = 10
    assert policy.decide(current, 3).kind == "probe"


def test_collection_never_uses_aoe_self_rescue_or_space():
    policy = collecting()
    policy.ingest(state(2.7, hp=.4, tank=.3))
    from hid_pilot.train_healer import TrainPhase

    policy.phase = TrainPhase.COLLECT
    policy.self_attempt_since, policy.self_attempt_count = 0, 2
    decision = policy.decide(state(2.8, hp=.4, tank=.3), 2.8)
    assert decision.skill != "self_rescue" and decision.kind != "probe"


def test_aura_is_held_across_cast_chain_and_off_after_last_cast():
    controller = Mock()
    aura = SkillAura(controller, "F8", Mock())
    aura.before()
    aura.after(2)
    aura.before()
    aura.after(3)
    aura.tick(2.5)
    assert controller.key.call_count == 1
    aura.tick(3)
    assert [call.args for call in controller.key.call_args_list] == [("F8", 100), ("F8", 100)]
    assert not aura.on
    assert controller.wait.call_args_list == [call(.3), call(.3)]


def test_follow_does_not_turn_aura_off_before_a_healing_cast_finishes():
    controller = Mock()
    aura = SkillAura(controller, "F8", Mock())
    aura.before()
    aura.after(5)
    aura.before()
    aura.after(1)
    aura.tick(1)
    assert aura.on
    aura.tick(5)
    assert not aura.on


def test_runtime_four_uses_aura_without_target_click(monkeypatch, tmp_path):
    healer, controller, observer, initial, now = harness(monkeypatch, tmp_path)
    healer.config.self_rescue_key = "4"
    healer.config.aura_enabled = True
    current = initial.model_copy(update={"hp": .4})
    observer.snapshots.return_value = (current,)
    healer.policy.ingest(current)
    healer.policy.self_attempt_since, healer.policy.self_attempt_count = 0, 2
    healer.execute(HealDecision(kind="cast", skill="self_rescue", reason="тест"), current)
    assert [call.args[0] for call in controller.key.call_args_list] == ["F8", "4"]
    inputs = [entry for entry in controller.mock_calls if entry[0] in {"key", "wait"}]
    assert inputs[:3] == [call.key("F8", 100), call.wait(.3), call.key("4", 100)]
    controller.click.assert_not_called()
    assert healer.policy.ready_at["self_rescue"] == now[0] + 8
    healer.aura.tick(now[0] + .2)
    assert controller.key.call_args.args == ("F8", 100)


def test_follow_never_toggles_aura_without_heals(monkeypatch, tmp_path):
    healer, controller, observer, initial, _now = harness(monkeypatch, tmp_path)
    healer.config.aura_enabled = True
    healer.config.follow_key = "9"
    current = initial.model_copy(update={"members": (initial.members[0].model_copy(update={"hp": 1.}),)})
    observer.snapshots.return_value = (current,)
    healer.policy.follow_needed = True
    healer.execute(HealDecision(kind="follow", target="@slot1", reason="тест"), current)
    assert [call.args[0] for call in controller.key.call_args_list] == ["9"]
    assert not healer.aura.on


def test_aura_cast_delay_is_configurable():
    controller = Mock()
    aura = SkillAura(controller, "F8", Mock(), cast_seconds=.45)
    aura.before()
    aura.before()
    controller.wait.assert_called_once_with(.45)
    assert HealerConfig().aura_cast == .3


def test_stop_during_aura_cast_prevents_healing_key(monkeypatch, tmp_path):
    healer, controller, observer, initial, _now = harness(monkeypatch, tmp_path)
    healer.config.aura_enabled = True
    healer.config.self_rescue_key = "4"
    current = initial.model_copy(update={"hp": .4})
    observer.snapshots.return_value = (current,)
    healer.policy.ingest(current)
    healer.policy.self_attempt_since, healer.policy.self_attempt_count = 0, 2
    controller.wait.side_effect = Stopped("STOP")
    with pytest.raises(Stopped):
        healer.execute(HealDecision(kind="cast", skill="self_rescue", reason="тест"), current)
    controller.key.assert_called_once_with("F8", 100)
