import pytest

from hid_pilot.healer import HealDecision, HealerConfig, HealerSnapshot, HealPolicy
from hid_pilot.party import PartyMember


def snapshot(at=10., hp=1., mp=1., cp=1., party=(1., 1.), known=True):
    return HealerSnapshot(at=at, hp=hp, mp=mp, cp=cp, roster_known=known,
                          members=tuple(PartyMember(slot=i + 1, name=chr(65 + i),
                                                    hp=value, mp=1., cp=1., confidence=.99)
                                        for i, value in enumerate(party)))


def policy(**changes):
    result = HealPolicy(HealerConfig(**({"mp_buff_key": None, "follow_key": None, "follow_seconds": 7} | changes)), 0.)
    result.follow_name = "@slot1"
    result.follow_needed = False
    result.support_ready_at = 0.
    result.next_follow_at = 10. + result.config.follow_seconds
    return result


def test_missing_hp_emergency_waits_fifteen_seconds_and_obeys_cooldown():
    p = policy(missing_hp_timeout=90)
    for index in range(76):
        at = 10 + index * .2
        state = snapshot(at=at, party=(None, 1.), known=False)
        decision = p.decide(state, at)
        if index < 75:
            assert decision.skill != "balance"
    assert decision.skill == "balance"
    p.committed(decision, state, at)
    p.pending = None
    follow = p.decide(snapshot(at=29, party=(None, 1.), known=False), 29)
    assert follow.kind == "follow"
    p.committed(follow, snapshot(at=29, party=(None, 1.), known=False), 29)
    assert p.decide(snapshot(at=29.2, party=(None, 1.), known=False), 29.2).kind == "wait"
    assert p.decide(snapshot(at=55, party=(None, 1.), known=False), 55).skill == "balance"
    p.decide(snapshot(at=55.2), 55.2)
    assert not p.emergency_slots(55.2)


def test_hp_retry_timers_are_independent_and_reset_on_recognition():
    p = policy()
    p.ingest(snapshot(at=10, party=(None, 1.), known=False))
    p.ingest(snapshot(at=24, party=(1., None), known=False))
    assert not p.emergency_slots(25)
    assert p.emergency_slots(39) == (2,)
    p.ingest(snapshot(at=40))
    assert not p.hp_missing_since


def test_disappeared_row_still_requires_hp_recognition():
    p = policy()
    p.ingest(snapshot(at=10))
    p.ingest(snapshot(at=11, party=(1.,), known=False))
    assert p.emergency_slots(26) == (2,)


def test_multiple_missing_members_expire_once_and_normal_monitoring_resumes(caplog):
    p = policy()
    p.ingest(snapshot(at=10, party=(1., None, None), known=False))
    current = snapshot(at=55, party=(1., None, None), known=False)
    p.decide(current, 55)
    assert p.ignored_slots == {2, 3}
    assert not p.hp_missing_since
    assert p.health_complete(p.monitored_state(current))
    assert "нужно переснять через OCR" in caplog.text
    assert len(caplog.records) == 1
    p.decide(current.model_copy(update={"at": 56}), 56)
    assert len(caplog.records) == 1


def test_leader_departure_switches_follow_to_remaining_first_slot():
    p = policy()
    p.ingest(snapshot(at=10, party=(None, 1.), known=False))
    decision = p.decide(snapshot(at=55, party=(None, 1.), known=False), 55)
    assert (decision.kind, decision.target) == ("follow", "@slot2")


def test_follow_stays_on_configured_slot_after_reordering():
    p = HealPolicy(HealerConfig(follow_slot=2), 0.)
    initial = snapshot()
    decision = p.decide(initial, 10.)
    assert (decision.kind, decision.target) == ("follow", "@slot2")
    p.committed(decision, initial, 10.)
    reordered = initial.model_copy(update={"at": 11., "members": tuple(
        member.model_copy(update={"slot": 3 - member.slot}) for member in initial.members)})
    p.follow_needed = True
    assert p.decide(reordered, 11.).target == "@slot2"


@pytest.mark.parametrize("interval", [7., 12.])
def test_follow_repeats_without_casts_only_after_committed_interval(interval):
    p = HealPolicy(HealerConfig(follow_seconds=interval, mp_buff_key=None), 0.)
    initial = p.decide(snapshot(at=0.), 0.)
    assert initial.kind == "follow"
    p.committed(initial, snapshot(at=0.), 0.)
    assert p.decide(snapshot(at=interval - .1), interval - .1).kind == "wait"
    due = p.decide(snapshot(at=interval), interval)
    assert (due.kind, due.target) == ("follow", "@slot1")
    assert p.decide(snapshot(at=interval + .2), interval + .2).kind == "follow"
    p.committed(due, snapshot(at=interval + .3), interval + .3)
    assert p.decide(snapshot(at=2 * interval), 2 * interval).kind == "wait"
    assert p.decide(snapshot(at=2 * interval + .3), 2 * interval + .3).kind == "follow"


@pytest.mark.parametrize("skill", ["battle", "invocation"])
def test_periodic_follow_waits_until_cast_finishes(skill):
    p = policy()
    p.next_follow_at = 7.
    p.committed(HealDecision(kind="cast", skill=skill, target="@self", reason="test"), snapshot(), 10.)
    assert p.decide(snapshot(at=10.1), 10.1).kind == "wait"
    end = p.busy_until
    result = p.decide(snapshot(at=end), end)
    assert result.kind == ("probe" if skill == "battle" else "follow")


def test_follow_interval_is_parsed_and_invalid_values_are_rejected():
    from hid_pilot.script import parse_script, script_variables

    script = 'set follow_seconds 12\nhealer "farm-bars.json" "party.json"'
    assert parse_script(script)[0].operation == "healer"
    assert HealerConfig.model_validate(script_variables(script)).follow_seconds == 12
    for value in ("0", "-1", "301", "nan"):
        with pytest.raises(ValueError):
            parse_script(f'set follow_seconds {value}\nhealer "farm-bars.json" "party.json"')


def test_self_healing_precedes_party_and_following():
    p = policy()
    p.follow_needed = True
    p.ingest(snapshot(at=9.2, hp=.8, party=(.1, .2)))
    p.ingest(snapshot(at=9.6, hp=.6, party=(.1, .2)))
    decision = p.decide(snapshot(hp=.4, party=(.1, .2)), 10.)
    assert decision.skill == "group"


def test_follow_heal_and_recharge_do_not_require_names():
    state = snapshot(party=(1., .7))
    state = state.model_copy(update={"members": tuple(
        member.model_copy(update={"name": None}) for member in state.members)})
    p = HealPolicy(HealerConfig(recharge_key="4", mp_buff_key=None), 0.)
    follow = p.decide(state, 10.)
    assert (follow.kind, follow.target) == ("follow", "@slot1")
    p.committed(follow, state, 10.)
    heal = p.decide(state, 10.)
    assert heal.kind == "wait"
    healthy = state.model_copy(update={"at": 11., "members": tuple(
        member.model_copy(update={"hp": 1., "mp": .4}) for member in state.members)})
    recharge = p.decide(healthy, 11.)
    assert (recharge.skill, recharge.target) == ("recharge", "@slot1")


def test_balance_for_one_critical_member_with_healthy_reserve():
    p = policy()
    state = snapshot(party=(.2, 1., 1.))
    action = p.decide(state, 10.)
    assert action.skill == "balance"
    p.committed(action, state, 10.)
    assert p.ready_at["balance"] == 40
    assert not p.ready("balance", 39.9)


def test_balance_does_not_redistribute_an_already_low_party():
    action = policy().decide(snapshot(party=(.1, .1, .2)), 10.)
    assert action.skill == "balance"


def test_moderate_static_group_injuries_do_not_trigger_heal():
    assert policy().decide(snapshot(party=(.65, .7)), 10.).skill == "major"


def test_ore_only_in_danger_and_can_be_disabled():
    assert policy().decide(snapshot(party=(.6, .65)), 10.).skill == "major"
    p = policy(allow_ore=False)
    p.ready_at["balance"] = 100
    assert p.decide(snapshot(party=(.2, .3, .4)), 10.).skill == "group"


def test_major_is_fallback_when_battle_unavailable_and_danger():
    p = policy()
    p.ready_at.update(battle=30., balance=30.)
    assert p.decide(snapshot(party=(.3, 1.)), 10.).skill == "major"


def test_failed_single_heal_escalates_to_mass_heal():
    p = policy()
    p.ready_at["balance"] = 100
    p.single_failed.add("@slot1")
    assert p.decide(snapshot(party=(.3, 1.)), 10.).skill == "mass_major"


def test_failed_single_without_danger_uses_free_group_heal():
    p = policy()
    p.single_failed.add("@slot1")
    assert p.decide(snapshot(party=(.7, 1.)), 10.).skill is None


def test_failed_group_on_static_far_members_does_not_waste_avatar():
    p = policy()
    p.group_failed = True
    p.ready_at["balance"] = 100
    p.excluded = {"@slot1": 100., "@slot2": 100.}
    assert p.decide(snapshot(party=(.4, .4)), 10.).skill is None


def test_avatar_for_fast_group_losses_despite_previous_group_heal():
    p = policy()
    p.group_failed = True
    p.ready_at["balance"] = 100
    p.ingest(snapshot(at=9.2, party=(1., 1.)))
    p.ingest(snapshot(at=9.6, party=(.7, .7)))
    assert p.decide(snapshot(at=10., party=(.4, .4)), 10.).skill == "avatar"


def test_avatar_after_balance_heals_self_and_party():
    p = policy()
    p.last_balance = 9
    p.ready_at.update(group=100., mass_major=100.)
    p.ingest(snapshot(at=9.2, hp=.8, party=(.8, .8)))
    p.ingest(snapshot(at=9.6, hp=.7, party=(.7, .7)))
    action = p.decide(snapshot(hp=.6, party=(.6, .6)), 10.)
    assert action.skill == "avatar"
    p.committed(action, snapshot(), 10.)
    assert p.ready_at["avatar"] == 1210


def test_avatar_not_spammed_during_long_cooldown():
    p = policy()
    p.last_balance = 9
    p.ready_at["avatar"] = 1200
    assert p.decide(snapshot(hp=.6, party=(.6, .6)), 10.).skill == "group"


def test_ineffective_mass_excludes_member_but_continues_damage_history():
    p = policy()
    before = snapshot(at=1, party=(.6, .6))
    p.ingest(before)
    action = HealDecision(kind="cast", skill="group", reason="test")
    p.committed(action, before, 1.)
    after = snapshot(at=p.busy_until, party=(.6, .9))
    p.ingest(after)
    assert "@slot1" in p.excluded and "@slot2" not in p.excluded
    assert not p.group_failed
    p.follow_needed = False
    p.ready_at["balance"] = 100
    p.ingest(snapshot(at=after.at + .2, party=(.45, .9)))
    fallen = snapshot(at=after.at + .4, party=(.3, .9))
    decision = p.decide(fallen, fallen.at)
    assert decision.kind == "chase" and decision.target == "@slot1"
    assert p.rate("@slot1") >= p.config.rapid_drop


def test_chase_deadline_cannot_extend_forever():
    p = policy(chase_seconds=30)
    p.committed(HealDecision(kind="chase", target="@slot2", reason="test"), snapshot(), 10.)
    assert p.chase_until == 40
    decision = p.decide(snapshot(at=40), 40)
    assert decision.kind == "follow" and decision.target == "@slot1"
    p.committed(decision, snapshot(at=40), 40)
    assert p.chase_name is None and p.excluded["@slot2"] > 40


def test_chase_decision_is_stable_until_action_commits():
    p = policy()
    p.excluded["@slot2"] = 100
    p.ready_at["balance"] = 100
    p.committed(HealDecision(kind="chase", target="@slot2", reason="test"), snapshot(), 10.)
    state = snapshot(at=13, party=(1., .3))
    p.ingest(snapshot(at=12.2, party=(1., .7)))
    p.ingest(snapshot(at=12.6, party=(1., .5)))
    first = p.decide(state, 13.)
    second = p.decide(state, 13.)
    assert first == second
    assert (first.kind, first.skill, first.target) == ("cast", "battle", "@slot2")


@pytest.mark.parametrize("target", ["@self", "@slot1"])
@pytest.mark.parametrize("hp,drop,battle", [
    (.49, .1, True), (.5, .1, False), (.6, .1, False),
    (.4, 0., False), (.4, .02, False), (.5, .05, False),
])
def test_battle_requires_low_hp_and_two_strong_damage_ticks(target, hp, drop, battle):
    p = policy()
    p.ready_at["balance"] = 100
    def sample(at, value):
        return snapshot(at=at, **({"hp": value} if target == "@self" else {"party": (value, 1.)}))
    p.ingest(sample(9.5, hp + drop))
    p.ingest(sample(9.75, hp + drop / 2))
    current = sample(10., hp)
    decision = p.decide(current, 10.)
    assert (decision.skill == "battle") is battle
    assert p.needs_single(current, target) == (hp < .7 or drop > .05 + 1e-9)



def test_chase_deadline_also_returns_when_mp_empty():
    p = policy()
    p.committed(HealDecision(kind="chase", target="@slot2", reason="test"), snapshot(), 10.)
    assert p.decide(snapshot(at=40, mp=0), 40).kind == "follow"


def calm(p, end=5.):
    for tick in range(int(end * 2) + 1):
        p.ingest(snapshot(at=tick / 2, mp=.1))


def test_invocation_requires_quiet_window_and_locks_all_actions():
    p = policy()
    calm(p)
    decision = p.decide(snapshot(at=5., mp=.1), 5.)
    assert decision.skill == "invocation"
    p.committed(decision, snapshot(at=5., mp=.1), 5.)
    assert p.ready_at["invocation"] == 305
    assert p.busy_until > 35
    assert p.decide(snapshot(at=20, hp=.2, party=(.1, .1)), 20).kind == "wait"
    end = p.busy_until
    assert p.decide(snapshot(at=end, mp=.8), end).kind == "follow"


@pytest.mark.parametrize("change", [{"cp": .8}, {"hp": .9}, {"known": False}, {"party": (None, 1.)}])
def test_damage_or_unknown_observation_blocks_invocation(change):
    p = policy()
    calm(p)
    action = p.decide(snapshot(at=5.2, mp=.1, **change), 5.2)
    assert action.skill != "invocation"


def test_observation_gap_does_not_count_as_peace():
    p = policy()
    p.ingest(snapshot(at=0, mp=.1))
    assert p.decide(snapshot(at=10, mp=.1), 10).skill != "invocation"


def test_self_is_never_excluded_from_healing():
    p = policy()
    p.excluded["@self"] = 1000
    p.ready_at["balance"] = 100
    assert p.decide(snapshot(hp=.4), 10.).target == "@self"


@pytest.mark.parametrize("hp,mp,at", [(None, 1., 10.), (1., None, 10.), (1., 1., 8.)])
def test_unknown_or_stale_snapshot_cannot_cast(hp, mp, at):
    assert policy().decide(snapshot(at=at, hp=hp, mp=mp), 10.).kind == "wait"


def test_recharge_disabled_unless_key_is_configured():
    state = snapshot()
    state = state.model_copy(update={"members": (state.members[0].model_copy(update={"mp": .4}),)})
    assert policy().decide(state, 10.).skill is None
    assert policy(recharge_key="4").decide(state, 10.).skill == "recharge"
    state = state.model_copy(update={"members": (state.members[0].model_copy(update={"mp": .8}),)})
    assert policy(recharge_key="4").decide(state, 10.).skill is None


def test_casting_formula_and_override_do_not_shorten_invocation():
    config = HealerConfig(casting_speed=850)
    assert config.cast_seconds("battle") == pytest.approx(2 * 333 / 850 + .25)
    assert config.cast_seconds("mass_major") == pytest.approx(7 * 333 / 850 + .25)
    assert HealerConfig(casting_speed=1100).cast_seconds("battle") < config.cast_seconds("battle")
    assert HealerConfig(battle_cast=1.7).cast_seconds("battle") == 1.7
    assert config.cast_seconds("invocation") >= 30


def test_recharge_allowed_for_static_hp_above_single_heal_threshold():
    p = policy(recharge_key="4")
    p.ready_at["battle"] = 100
    state = snapshot(party=(.7, 1.))
    state = state.model_copy(update={"members": tuple(m.model_copy(update={"mp": .3}) for m in state.members)})
    assert p.decide(state, 10.).skill == "recharge"


def test_new_script_and_configuration_validation():
    from hid_pilot.script import parse_script
    assert parse_script('set follow_slot 2\nhealer "farm-bars.json" "party.json"')[0].operation == "healer"
    for text in ('set follow_slot 9', 'set battle_key BACKSLASH', 'set chase_seconds 31', 'set casting_speed nan'):
        with pytest.raises(ValueError):
            parse_script(text + '\nhealer "farm-bars.json" "party.json"')
