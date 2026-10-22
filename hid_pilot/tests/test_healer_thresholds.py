import pytest

from hid_pilot.healer import HealerConfig, HealerSnapshot, HealPolicy
from hid_pilot.party import PartyMember


def group_state(hps, at):
    return HealerSnapshot(at=at, hp=hps[0], mp=1., cp=1., roster_known=True,
                          members=tuple(PartyMember(slot=i, hp=hp, mp=1., cp=1.)
                                        for i, hp in enumerate(hps[1:], 1)))


def ready_policy():
    policy = HealPolicy(HealerConfig(mp_buff_key=None), 0.)
    policy.follow_needed = False
    policy.next_follow_at = 20.
    return policy


@pytest.mark.parametrize("count,expected", [(1, True), (2, True), (3, True), (4, False)])
def test_balance_requires_one_to_three_low_outliers(count, expected):
    policy = ready_policy()
    health = group_state([.9] * (8 - count) + [.4] * count, 10.).health()
    assert policy.needs_balance(health) is expected
    result = policy.decide(group_state([.9] * (8 - count) + [.4] * count, 10.), 10.)
    assert (result.skill == "balance") is expected


@pytest.mark.parametrize("low,expected", [(.55, False), (.549, True)])
def test_balance_gap_is_strict_and_uses_other_members_average(low, expected):
    policy = ready_policy()
    assert policy.needs_balance(group_state([.9, .9, .9, low], 10.).health()) is expected


@pytest.mark.parametrize("count,drop,expected", [
    (6, .151, "group"), (6, .15, None),
    (6, .331, "group"), (6, .33, "group"),
    (5, .34, None),
])
def test_mass_heal_requires_more_than_seventy_percent_and_strict_drop(count, drop, expected):
    policy = ready_policy()
    before = [1.] * 8
    after = [1. - drop] * count + [1.] * (8 - count)
    policy.ingest(group_state(before, 9.5))
    policy.ingest(group_state([(a+b)/2 for a,b in zip(before, after)], 9.75))
    result = policy.decide(group_state(after, 10.), 10.)
    if expected is None:
        assert result.skill != "mass_major"
        assert "70% ??????" not in result.reason
    else:
        assert result.skill == expected


def test_mass_heal_ignores_hp_gain_and_observation_gaps():
    for before, after, earlier in [([.5] * 8, [1.] * 8, 9.5), ([1.] * 8, [.5] * 8, 8.)]:
        policy = ready_policy()
        policy.ingest(group_state(before, earlier))
        assert policy.decide(group_state(after, 10.), 10.).skill != "mass_major"


def test_mass_major_disabled_by_ore_setting_falls_back_to_free_group():
    policy = ready_policy()
    policy.config = HealerConfig(allow_ore=False)
    policy.ingest(group_state([1.] * 8, 9.5))
    assert policy.decide(group_state([.6] * 8, 10.), 10.).skill == "group"


def test_unknown_hp_does_not_create_mass_damage():
    policy = ready_policy()
    policy.ingest(group_state([1.] * 8, 9.5))
    state = group_state([.6] * 8, 10.)
    state = state.model_copy(update={"roster_known": False, "members": tuple(
        member.model_copy(update={"hp": None}) for member in state.members)})
    assert policy.decide(state, 10.).skill not in {"group", "mass_major"}
