import pytest

from hid_pilot.healer import HealDecision
from hid_pilot.party import PartyMember
from hid_pilot.train_healer import TrainConfig, TrainPhase, TrainPolicy, TrainSnapshot


def state(at, tank=1., others=(1., 1.), hp=1., paralyzed=(), known=True):
    return TrainSnapshot(at=at, hp=hp, mp=1., cp=1., roster_known=known,
                         members=tuple(PartyMember(slot=i + 1, hp=value, mp=1., cp=1.)
                                       for i, value in enumerate((*others, tank))),
                         paralyzed_slots=paralyzed, debuffs_known=True)


def collecting(tank=.3):
    policy = TrainPolicy(TrainConfig(), 0.)
    count = 300
    start = 2.6 - count / 10
    policy.ingest(state(start))
    for i in range(1, count + 1):
        policy.ingest(state(start + i / 10, tank=1. - (1. - tank - .003) * i / count))
    assert policy.phase == TrainPhase.COLLECT
    return policy


def test_missing_hp_emergency_does_not_balance_in_unknown_train_phase():
    policy = collecting()
    for index in range(161):
        at = 2.7 + index * .1
        decision = policy.decide(state(at, tank=.3, others=(None, 1.), known=False), at)
        assert decision.skill != "balance"
    assert policy.emergency_slots(at) == (1,)
    assert policy.phase == TrainPhase.UNKNOWN


def test_train_missing_hp_emergency_continues_following_without_balance():
    policy = collecting()
    for index in range(151):
        at = 2.7 + index * .1
        decision = policy.decide(state(at, tank=.3, others=(1., None), known=False), at)
    assert (decision.kind, decision.target) == ("follow", "@slot1")


def test_train_departed_members_are_excluded_and_phase_can_recover():
    policy = TrainPolicy(TrainConfig(), 0.)
    policy.ingest(state(0))
    policy.ingest(state(.2, tank=None, others=(1., None), known=False))
    policy.ingest(state(45.2, tank=None, others=(1., None), known=False))
    policy.ingest(state(45.4, tank=None, others=(1., None), known=False))
    assert policy.ignored_slots == {2, 3}
    assert policy.tank_slot == 1
    assert policy.phase == TrainPhase.IDLE


@pytest.mark.parametrize("tank", [.3, .2, .1])
def test_collecting_low_tank_is_not_healed_purified_or_followed(tank):
    policy = collecting(tank)
    decision = policy.decide(state(2.7, tank=tank, paralyzed=(3,)), 2.7)
    assert decision.kind == "wait"
    assert policy.phase == TrainPhase.COLLECT
    assert policy.config.poll_seconds == .1


def test_slow_damage_establishes_collection_without_healing():
    policy = TrainPolicy(TrainConfig(), 0.)
    policy.ingest(state(0.))
    for i in range(1, 21):
        decision = policy.decide(state(i / 10, tank=1. - .02 * i / 10), i / 10)
        assert decision.skill is None
    assert policy.phase == TrainPhase.COLLECT
    assert policy.tank_slot == 3


def test_tank_assignment_stays_on_bound_last_slot_when_members_disappear():
    policy = TrainPolicy(TrainConfig(), 0.)
    policy.ingest(state(0.))
    policy.ingest(state(.2, others=(1.,), tank=.8))
    assert policy.tank_slot == 3
    assert policy.phase == TrainPhase.UNKNOWN


def test_returning_tank_burst_needs_two_ticks_then_prefers_balance():
    policy = collecting(.3)
    first = policy.decide(state(2.7, tank=.24), 2.7)
    assert first.kind == "wait"
    assert policy.phase == TrainPhase.COLLECT
    decision = policy.decide(state(2.8, tank=.19), 2.8)
    assert policy.phase == TrainPhase.COMBAT
    assert (decision.skill, decision.target) == ("balance", None)


def test_emergency_under_ten_percent_can_balance_during_collection():
    policy = collecting(.095)
    result = policy.decide(state(2.7, tank=.095), 2.7)
    assert result.skill == "balance"
    assert policy.phase == TrainPhase.COLLECT


def test_combat_rescue_alternates_battle_and_major_while_cooldowns_are_active():
    policy = TrainPolicy(TrainConfig(), 0.)
    policy.ingest(state(2.4, tank=.2))
    policy.phase = TrainPhase.COMBAT
    policy.ingest(state(2.5, tank=.16))
    policy.ingest(state(2.6, tank=.12))
    policy.arrival_pending = True
    policy.ready_at["balance"] = 30.
    snapshot = state(2.7, tank=.095)
    first = policy.decide(snapshot, 2.7)
    assert (first.skill, first.target) == ("battle", "@slot3")
    policy.committed(first, snapshot, 2.7)
    assert policy.decide(state(2.8, tank=.095), 2.8).kind == "wait"
    policy.busy_until = 2.9
    policy.ready_at["battle"] = 30.
    second = policy.decide(state(3., tank=.095), 3.)
    assert (second.skill, second.target) == ("major", "@slot3")


def test_collecting_emergency_only_allows_balance_or_battle():
    policy = collecting(.095)
    policy.ready_at.update(balance=30., battle=30.)
    result = policy.decide(state(2.7, tank=.095), 2.7)
    assert result.kind == "wait"
    assert policy.ready("major", 2.7)


def test_damage_to_another_member_switches_to_normal_combat_rules():
    policy = collecting(.3)
    result = policy.decide(state(2.7, tank=.3, others=(.97, 1.)), 2.7)
    assert policy.phase == TrainPhase.COMBAT
    assert policy.config.poll_seconds == .2
    assert result.skill == "balance"


def test_purify_skips_collecting_tank_and_uses_party_order():
    policy = collecting(.3)
    result = policy.decide(state(2.7, tank=.3, paralyzed=(3, 2, 1)), 2.7)
    assert (result.skill, result.target) == ("purify", "@slot1")
    policy.committed(result, state(2.7, tank=.3), 2.7)
    assert policy.ready_at["purify"] > 2.7
    assert policy.ready("battle", 2.7)


def test_purify_tank_is_allowed_in_combat_and_precedes_healing():
    policy = TrainPolicy(TrainConfig(), 0.)
    policy.ingest(state(0.))
    policy.phase = TrainPhase.COMBAT
    result = policy.decide(state(.4, tank=.7, others=(.4, 1.), paralyzed=(3,)), .4)
    assert (result.skill, result.target) == ("purify", "@slot3")


def test_unknown_low_tank_is_not_mistaken_for_returned_tank():
    policy = TrainPolicy(TrainConfig(), 0.)
    result = policy.decide(state(0., tank=.25, paralyzed=(3,)), 0.)
    assert policy.phase == TrainPhase.UNKNOWN
    assert result.kind == "wait"


def test_other_member_can_receive_solo_purify_during_unknown_phase():
    policy = TrainPolicy(TrainConfig(), 0.)
    result = policy.decide(state(0., tank=.25, others=(.4, 1.), paralyzed=(1,)), 0.)
    assert (result.skill, result.target) == ("purify", "@slot1")


def test_combat_quiet_period_restores_following_first_member():
    policy = TrainPolicy(TrainConfig(), 0.)
    policy.ingest(state(0.))
    policy.ingest(state(.2, others=(.95, 1.)))
    assert policy.phase == TrainPhase.COMBAT
    for i in range(3, 35):
        result = policy.decide(state(i / 10), i / 10)
    assert policy.phase == TrainPhase.IDLE
    assert (result.kind, result.target) == ("follow", "@slot1")


def test_invocation_cast_blocks_purify_and_follow():
    policy = TrainPolicy(TrainConfig(), 0.)
    snapshot = state(0.)
    policy.ingest(snapshot)
    policy.committed(HealDecision(kind="cast", skill="invocation", reason="test"), snapshot, 0.)
    assert policy.decide(state(.2, paralyzed=(1,)), .2).kind == "wait"


def test_purify_cast_and_cooldown_scale_with_casting_speed():
    config = TrainConfig(casting_speed=1000, cast_margin=.1)
    assert config.cast_seconds("purify") == pytest.approx(3 * 333 / 1000 + .1)
    assert config.cooldown("purify") == pytest.approx(4 * 333 / 1000)
    assert TrainConfig(casting_speed=1500).cooldown("purify") < config.cooldown("purify")


def test_collecting_solo_heal_does_not_touch_tank_or_use_mass_effect():
    policy = collecting(.3)
    snapshot = state(2.7, tank=.3, hp=.4)
    # Пропуск наблюдения не является доказательством возвращения танка.
    snapshot = snapshot.model_copy(update={"at": 4.})
    decision = policy.decide(snapshot, 4.)
    assert policy.phase == TrainPhase.UNKNOWN
    assert (decision.skill, decision.target) == ("major", "@self")


def test_collecting_hp_gain_does_not_switch_to_combat():
    policy = collecting(.3)
    result = policy.decide(state(2.7, tank=.4), 2.7)
    assert policy.phase == TrainPhase.COLLECT
    assert result.kind == "wait"


def test_train_example_is_registered_and_parses():
    from pathlib import Path

    from hid_pilot.script import parse_script, script_variables

    path = Path(__file__).parents[1] / "extensions" / "scripts" / "healer-train.pilot"
    text = path.read_text("utf-8")
    instructions = parse_script(text)
    assert instructions[0].operation == "healer_train"
    assert len(instructions[0].args) == 3
    config = TrainConfig.model_validate(script_variables(text))
    assert config.tank_slot == 0
    assert config.purify_key == "F4"


def test_intermediate_tank_only_damage_never_opens_normal_healing():
    policy = TrainPolicy(TrainConfig(), 0.)
    policy.ingest(state(0.))
    for i in range(1, 101):
        result = policy.decide(state(i / 10, tank=1. - .06 * i / 10), i / 10)
        assert result.skill is None
    assert policy.phase == TrainPhase.UNKNOWN


def test_exact_five_percent_damage_is_normal_collection():
    policy = TrainPolicy(TrainConfig(), 0.)
    policy.ingest(state(0.))
    for i in range(1, 21):
        policy.ingest(state(i / 10, tank=1. - .05 * i / 10))
    assert policy.phase == TrainPhase.COLLECT
