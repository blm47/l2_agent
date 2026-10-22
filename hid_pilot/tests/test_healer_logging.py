import pytest
from test_healer import snapshot
from test_healer_runtime import harness

from hid_pilot.healer import HealDecision, HealerConfig, HealPolicy
from hid_pilot.healer_log import format_state, support_status


def test_log_reports_hp_retry_timer_and_emergency_reason():
    policy = HealPolicy(HealerConfig(), 0.)
    policy.ingest(snapshot(at=10, party=(None, 1.), known=False))
    state = snapshot(at=25, party=(None, 1.), known=False)
    decision = policy.decide(state, 25)
    output = format_state(policy, state, decision, 25)
    assert "№1: 15/15 сек" in output
    assert "Аварийный режим" in output


def test_log_has_own_line_then_centered_member_pairs_in_slot_order():
    policy = HealPolicy(HealerConfig(), 0.)
    state = snapshot(at=10., party=(1., .8, .7))
    state = state.model_copy(update={"members": tuple(reversed(state.members))})
    output = format_state(policy, state, HealDecision(kind="wait", reason="тест"), 10.)
    lines = output.splitlines()
    assert lines[0].strip().startswith("СЕБЯ")
    left, right = lines[1].split(" │ ")
    assert len(left) == len(right) == 48
    assert "№1" in left and "№2" in right
    assert "№3" in lines[2] and "№4" not in lines[2]
    assert "F11:" in output and "Invocation:" in output


def test_log_explains_persisted_buff_cooldown_and_own_mp_threshold():
    policy = HealPolicy(HealerConfig(), 0.)
    policy.ready_at["mp_buff"] = 930.
    output = support_status(policy, snapshot(at=40., mp=.8), 40.)
    assert "откат 890 с" in output
    assert "своё MP 80% ≥ 80%" in output


@pytest.mark.parametrize("skill", ["mp_buff", "invocation"])
def test_missing_member_mp_does_not_block_support_with_known_hp(skill):
    policy = HealPolicy(HealerConfig(mp_buff_key="F11" if skill == "mp_buff" else None), 0.)
    policy.follow_needed = False
    policy.next_follow_at = 100.
    for tick in range(151):
        state = snapshot(at=tick * .2, mp=.55)
        state = state.model_copy(update={"roster_known": False, "members": tuple(
            member.model_copy(update={"mp": None}) for member in state.members)})
        policy.ingest(state)
    decision = policy.decide(state, 30.)
    assert decision.skill == skill
    assert "HP/состав пати неизвестны" not in support_status(policy, state, 30.)


def test_click_randomness_stays_in_point_one_percent_and_uses_same_double_click(monkeypatch, tmp_path):
    healer, controller, _observer, _state, _now = harness(monkeypatch, tmp_path)
    monkeypatch.setattr("hid_pilot.healer_runtime.random.uniform", lambda low, high: high)
    healer.select("@slot1", twice=True)
    calls = controller.click.call_args_list
    assert len(calls) == 2
    assert calls[0].args == calls[1].args
    assert calls[0].args[1] == pytest.approx(.451)
    assert calls[0].args[2] == pytest.approx(.106)
    assert healer.click_point(.999, .999, 1)[0] < healer.party.name.x2
