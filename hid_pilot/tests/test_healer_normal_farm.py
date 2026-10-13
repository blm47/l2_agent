import pytest
from test_healer import snapshot
from test_healer_runtime import harness

from hid_pilot.healer import HealDecision, HealerConfig, HealPolicy
from hid_pilot.healer_runtime import TargetChanged


def normal_policy():
    policy = HealPolicy(HealerConfig(mp_buff_key=None), 0.)
    policy.follow_needed = False
    policy.support_ready_at = 0.
    policy.next_follow_at = 100.
    policy.ready_at["balance"] = 100.
    return policy


@pytest.mark.parametrize("values,expected", [
    ((1., .4, .4), False), ((.8, .6, .4), True),
    ((.9, .7, .6), False), ((.4, .4, .4), False),
])
def test_battle_uses_two_damage_intervals_and_low_hp(values, expected):
    policy = normal_policy()
    policy.ingest(snapshot(at=9.6, party=(values[0], 1.)))
    policy.ingest(snapshot(at=9.8, party=(values[1], 1.)))
    decision = policy.decide(snapshot(at=10., party=(values[2], 1.)), 10.)
    assert (decision.skill == "battle") is expected


def test_same_sample_does_not_confirm_second_tick_and_gap_resets_confirmation():
    policy = normal_policy()
    policy.ingest(snapshot(at=9.6, party=(.8, 1.)))
    one_hit = snapshot(at=9.8, party=(.4, 1.))
    policy.ingest(one_hit)
    policy.ingest(one_hit)
    assert not policy.strong_damage("@slot1")
    policy.ingest(snapshot(at=11., party=(.2, 1.)))
    assert not policy.strong_damage("@slot1")


def test_single_aoe_burst_uses_free_group_not_mass_major():
    policy = normal_policy()
    policy.ingest(snapshot(at=9.6, party=(1.,) * 7))
    policy.ingest(snapshot(at=9.8, party=(.4,) * 7))
    decision = policy.decide(snapshot(at=10., party=(.4,) * 7), 10.)
    assert decision.skill == "group"


@pytest.mark.parametrize("mp,ready,expected", [(.79, True, True), (.8, True, False), (.3, False, False)])
def test_invocation_uses_eighty_percent_threshold_quiet_window_and_cooldown(mp, ready, expected):
    policy = normal_policy()
    for i in range(151):
        policy.ingest(snapshot(at=i * .2, mp=mp))
    if not ready:
        policy.ready_at["invocation"] = 100.
    decision = policy.decide(snapshot(at=30., mp=mp), 30.)
    assert (decision.skill == "invocation") is expected


def test_recent_party_damage_prevents_invocation():
    policy = normal_policy()
    for i in range(26):
        policy.ingest(snapshot(at=i * .2, mp=.55))
    assert policy.decide(snapshot(at=5.2, mp=.55, party=(.95, 1.)), 5.2).skill != "invocation"


def test_loading_delay_before_first_frame_does_not_count_as_quiet_time():
    policy = normal_policy()
    assert policy.decide(snapshot(at=10., mp=.55), 10.).skill != "invocation"
    assert policy.last_damage == 10.


def test_mp_self_buff_is_targetless_and_repeats_after_fifteen_minutes():
    policy = HealPolicy(HealerConfig(), 0.)
    first = policy.decide(snapshot(at=0.), 0.)
    assert first.kind == "follow"
    policy.committed(first, snapshot(at=0.), 0.)
    policy.support_ready_at = 0.
    buff = policy.decide(snapshot(at=.1), .1)
    assert (buff.skill, buff.target) == ("mp_buff", None)
    policy.committed(buff, snapshot(at=.1), .1)
    assert policy.busy_until >= 1.1
    assert policy.ready_at["mp_buff"] == 900.1
    policy.next_follow_at = 2000.
    assert policy.decide(snapshot(at=900.), 900.).skill != "mp_buff"
    assert policy.decide(snapshot(at=900.1), 900.1).skill == "mp_buff"


def healthy_runtime(monkeypatch, tmp_path):
    healer, controller, observer, state, now = harness(monkeypatch, tmp_path)
    healer.config.follow_key = "F1"
    healer.config.follow_seconds = 4
    healer.policy.follow_needed = True
    healthy = state.model_copy(update={"members": (state.members[0].model_copy(update={"hp": 1.}),)})
    observer.snapshots.return_value = (healthy,)
    return healer, controller, observer, healthy, now


def test_follow_selects_once_f1_then_returns_cursor_to_random_center(monkeypatch, tmp_path):
    healer, controller, _observer, state, _now = healthy_runtime(monkeypatch, tmp_path)
    order = []
    controller.click.side_effect = lambda *args, **kwargs: order.append("click")
    controller.key.side_effect = lambda *args: order.append("F1")
    controller.move.side_effect = lambda *args, **kwargs: order.append("center")
    healer.execute(HealDecision(kind="follow", target="@slot1", reason="test"), state)
    assert order == ["click", "F1", "center"]
    controller.click.assert_called_once()
    controller.key.assert_called_once_with("F1", 100)
    assert all(.35 <= value <= .65 for value in controller.move.call_args.args)
    assert healer.policy.next_follow_at == 14.


def test_f1_is_cancelled_if_heal_priority_changes_while_selecting(monkeypatch, tmp_path):
    healer, controller, observer, state, now = healthy_runtime(monkeypatch, tmp_path)
    def click(*args, **kwargs):
        now[0] = 10.1
        observer.snapshots.return_value = (state.model_copy(update={
            "at": 10.1, "members": (state.members[0].model_copy(update={"hp": .4}),)}),)
    controller.click.side_effect = click
    with pytest.raises(TargetChanged):
        healer.execute(HealDecision(kind="follow", target="@slot1", reason="test"), state)
    controller.key.assert_not_called()
    controller.move.assert_not_called()


def test_f11_does_not_select_target_and_its_timer_survives_restart(monkeypatch, tmp_path):
    healer, controller, _observer, state, now = healthy_runtime(monkeypatch, tmp_path)
    healer.config.mp_buff_key = "F11"
    healer.policy.support_ready_at = 0.
    healer.policy.follow_needed = False
    healer.policy.next_follow_at = 100.
    healer.execute(HealDecision(kind="cast", skill="mp_buff", reason="test"), state)
    controller.click.assert_not_called()
    controller.key.assert_called_once_with("F11", 100)
    assert healer.policy.ready_at["mp_buff"] == 910.
    now[0] = 15.
    healer.policy.ready_at.clear()
    healer.restore_timers()
    assert healer.policy.ready_at["mp_buff"] == 910.


def test_no_group_heal_effect_does_not_exclude_whole_party_as_unreachable():
    policy = normal_policy()
    before = snapshot(at=0., party=(.4, .4))
    policy.ingest(before)
    policy.committed(HealDecision(kind="cast", skill="group", reason="test"), before, 0.)
    policy.ingest(snapshot(at=policy.busy_until, party=(.4, .4)))
    assert policy.group_failed
    assert not policy.excluded
    assert "стан" in policy.feedback_note


@pytest.mark.parametrize("hp,needs_heal", [(.69, True), (.7, False), (.71, False)])
def test_ordinary_heal_maintains_seventy_percent_without_using_battle(hp, needs_heal):
    policy = normal_policy()
    current = snapshot(at=10., party=(hp, 1.))
    decision = policy.decide(current, 10.)
    assert policy.needs_single(current, "@slot1") is needs_heal
    assert decision.skill != "battle"
    if needs_heal:
        assert (decision.skill, decision.target) == ("major", "@slot1")


@pytest.mark.parametrize("count,expected", [(2, "major"), (3, "group"), (4, "group")])
def test_three_or_more_single_heal_targets_choose_free_mass_heal(count, expected):
    policy = normal_policy()
    result = policy.decide(snapshot(at=10., party=(.65,) * count), 10.)
    assert result.skill == expected


def test_mass_heal_count_includes_self_and_does_not_invent_unknown_hp():
    policy = normal_policy()
    assert policy.decide(snapshot(at=10., hp=.65, party=(.65, .65)), 10.).skill == "group"
    another = normal_policy()
    assert another.decide(snapshot(at=10., party=(.65, .65, None), known=False), 10.).skill == "major"


@pytest.mark.parametrize("buff_key,expected", [(None, "invocation"), ("F11", "mp_buff")])
def test_support_skills_wait_thirty_seconds_and_can_start_at_boundary(buff_key, expected):
    policy = HealPolicy(HealerConfig(mp_buff_key=buff_key), 0.)
    policy.follow_needed = False
    policy.next_follow_at = 100.
    for i in range(150):
        policy.ingest(snapshot(at=i * .2, mp=.55))
    assert policy.decide(snapshot(at=29.9, mp=.55), 29.9).skill not in {"mp_buff", "invocation"}
    result = policy.decide(snapshot(at=30., mp=.55), 30.)
    assert result.skill == expected
    if buff_key:
        policy.committed(result, snapshot(at=30., mp=.55), 30.)
        for i in range(1, 8):
            policy.ingest(snapshot(at=30. + i * .2, mp=.55))
        second = policy.decide(snapshot(at=31.4, mp=.55), 31.4)
        assert second.skill == "mp_buff2"
        policy.committed(second, snapshot(at=31.4, mp=.55), 31.4)
        for i in range(1, 8):
            policy.ingest(snapshot(at=31.4 + i * .2, mp=.55))
        assert policy.decide(snapshot(at=32.8, mp=.55), 32.8).skill == "invocation"


def test_healing_is_active_during_first_thirty_seconds():
    policy = HealPolicy(HealerConfig(), 0.)
    policy.ready_at["balance"] = 100.
    assert policy.decide(snapshot(at=1., party=(.65, .65, .65)), 1.).skill == "group"
