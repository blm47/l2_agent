from test_train_healer import state

from hid_pilot.train_healer import TrainConfig, TrainPolicy


def test_three_members_losing_over_fifteen_points_triggers_mass_heal_on_first_tick():
    policy = TrainPolicy(TrainConfig(mp_buff_key=None), 0)
    policy.ingest(state(0))
    current = state(.1, tank=.84, others=(.84, .84))
    assert policy.decide(current, .1).skill == "group"
    assert policy.heal_goal(current) == .7


def test_exact_fifteen_points_does_not_trigger_single_tick_burst_rule():
    policy = TrainPolicy(TrainConfig(mp_buff_key=None), 0)
    policy.ingest(state(0))
    current = state(.1, tank=.85, others=(.85, .85))
    assert policy.decide(current, .1).skill is None
    assert not policy.burst_targets


def test_full_health_goal_requires_two_strong_damage_intervals():
    policy = TrainPolicy(TrainConfig(mp_buff_key=None), 0)
    policy.ingest(state(0))
    first = state(.2, tank=.95, others=(.95, .95))
    assert policy.decide(first, .2).skill is None
    assert policy.heal_goal(first) == .7
    second = state(.4, tank=.9, others=(.9, .9))
    decision = policy.decide(second, .4)
    assert (decision.skill, decision.target) == ("group", None)
    assert policy.heal_goal(second) == 1
    for index in range(1, 17):
        current = state(.4 + index * .2, tank=.9, others=(.9, .9))
        policy.ingest(current)
    assert policy.heal_goal(current) == .7


def test_ordinary_injured_party_is_healed_to_seventy_percent():
    policy = TrainPolicy(TrainConfig(mp_buff_key=None), 0)
    policy.ingest(state(0))
    policy.ingest(state(.2, hp=.9, others=(.68, .68), tank=.68))
    current = state(.4, hp=.9, others=(.68, .68), tank=.68)
    assert policy.decide(current, .4).skill == "group"
    assert policy.heal_goal(current) == .7
