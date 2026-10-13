from test_train_healer import state

from hid_pilot.train_healer import TrainConfig, TrainPhase, TrainPolicy


def two_collectors():
    policy = TrainPolicy(TrainConfig(mp_buff_key=None), 0.)
    for index in range(201):
        at = index / 10
        policy.ingest(state(at, others=(1 - at * .025, 1 - at * .03)))
    assert policy.phase == TrainPhase.COLLECT
    assert policy.collectors == {"@slot1", "@slot2"}
    return policy


def test_two_collectors_need_not_include_last_party_member():
    policy = two_collectors()
    decision = policy.decide(state(20.1, others=(.5, .4), paralyzed=(1, 2)), 20.1)
    assert (decision.skill, decision.target) == ("group", None)
    assert policy.phase == TrainPhase.COLLECT


def test_collector_below_thirty_percent_gets_mass_heal_without_selection():
    policy = two_collectors()
    decision = policy.decide(state(20.1, others=(.29, .4)), 20.1)
    assert (decision.skill, decision.target) == ("group", None)


def test_emergency_balance_below_sixteen_percent_applies_to_any_collector():
    policy = two_collectors()
    decision = policy.decide(state(20.1, others=(.15, .4)), 20.1)
    assert decision.skill == "balance"


def test_exact_sixteen_percent_triggers_emergency_balance():
    policy = two_collectors()
    assert policy.decide(state(20.1, others=(.16, .4)), 20.1).skill == "balance"


def test_damage_to_third_member_starts_combat():
    policy = two_collectors()
    policy.decide(state(20.1, tank=.99, others=(.499, .399)), 20.1)
    assert policy.phase == TrainPhase.COMBAT


def test_outside_collection_ordinary_hp_above_seventy_does_not_trigger_heal():
    policy = TrainPolicy(TrainConfig(mp_buff_key=None), 0.)
    policy.ingest(state(0))
    decision = policy.decide(state(.1, hp=.94, others=(.94, .94), tank=.94), .1)
    assert policy.phase == TrainPhase.COMBAT
    assert decision.skill is None
    assert policy.config.maintain_hp == .7


def test_collectors_do_not_receive_purify_but_other_members_do():
    policy = two_collectors()
    decision = policy.decide(state(20.1, others=(.5, .4), paralyzed=(1, 2, 3)), 20.1)
    assert (decision.skill, decision.target) == ("purify", "@slot3")
