import pytest
from test_healer import snapshot
from test_train_healer import state

from hid_pilot.healer import HealerConfig, HealPolicy
from hid_pilot.train_healer import TrainConfig, TrainPolicy


@pytest.mark.parametrize("factory,make_state", [(HealPolicy, snapshot), (TrainPolicy, state)])
@pytest.mark.parametrize("mp,expected", [(.79, True), (.8, False)])
def test_invocation_requires_thirty_seconds_of_party_peace(factory, make_state, mp, expected):
    config = TrainConfig(mp_buff_key=None) if factory is TrainPolicy else HealerConfig(mp_buff_key=None)
    policy = factory(config, 0.)
    for index in range(151):
        at = index / 5
        current = make_state(at=at).model_copy(update={"mp": mp})
        decision = policy.decide(current, at)
        if at < 30:
            assert decision.skill != "invocation"
    assert (decision.skill == "invocation") is expected


@pytest.mark.parametrize("change", ["party_hp", "party_cp", "gap", "missing"])
def test_party_damage_or_missing_data_resets_invocation_window(change):
    policy = HealPolicy(HealerConfig(mp_buff_key=None), 0.)
    for index in range(150):
        policy.ingest(snapshot(at=index / 5, mp=.1))
    current = snapshot(at=30., mp=.1)
    if change == "party_hp":
        current = snapshot(at=30., mp=.1, party=(.99, 1.))
    elif change == "party_cp":
        current = current.model_copy(update={"members": (current.members[0].model_copy(update={"cp": .9}), current.members[1])})
    elif change == "gap":
        current = snapshot(at=31., mp=.1)
    else:
        current = snapshot(at=30., mp=.1, party=(None, 1.))
    assert policy.decide(current, current.at).skill != "invocation"
    assert not policy.urgent_invocation(current, current.at)


def test_group_heal_precedes_mass_major_and_falls_back_on_cooldown():
    policy = TrainPolicy(TrainConfig(), 0.)
    current = state(0., tank=.6, others=(.6, .6))
    assert policy.decide(current, 0.).skill == "group"
    policy.ready_at["group"] = 10.
    assert policy.decide(current, 0.).skill == "mass_major"
