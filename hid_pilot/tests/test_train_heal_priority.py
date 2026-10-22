import pytest
from test_train_healer import state

from hid_pilot.train_healer import TrainConfig, TrainPhase, TrainPolicy


def after_opening():
    policy = TrainPolicy(TrainConfig(group_cast=.1, verify_seconds=.1), 0.)
    initial = state(0., tank=.6)
    first = policy.decide(initial, 0.)
    assert first.skill == "group"
    policy.committed(first, initial, 0.)
    policy.ingest(state(.11, tank=.5))
    policy.ingest(state(.21, tank=.45))
    current = state(.31, tank=.4)
    policy.ingest(current)
    return policy, current


@pytest.mark.parametrize("phase", list(TrainPhase))
def test_strong_damage_targets_member_before_non_emergency_balance_in_every_phase(phase):
    policy, current = after_opening()
    policy.phase = phase
    policy.ready_at["group"] = 0.
    result = policy.decide(current, current.at)
    assert (result.skill, result.target) == ("battle", "@slot3")


@pytest.mark.parametrize("battle_ready, expected", [(True, "battle"), (False, "major")])
def test_targeted_heals_precede_ready_repeat_group(battle_ready, expected):
    policy, current = after_opening()
    policy.ready_at.update(balance=100., group=0.)
    if not battle_ready:
        policy.ready_at["battle"] = 100.
    result = policy.decide(current, current.at)
    assert (result.skill, result.target) == (expected, "@slot3")


def test_stable_low_hp_without_mass_effect_does_not_authorize_single():
    policy, _ = after_opening()
    policy.ready_at.update(balance=100., circlet_of_heal=100., battle=100., group=0.)
    for index in range(4, 41):
        current = state(index / 10, tank=.3)
        policy.ingest(current)
    assert not policy.strong_damage("@slot3")
    assert "@slot3" not in policy.reachable_until
    result = policy.decide(current, current.at)
    assert result.target is None or result.kind == "follow"
    assert result.skill != "major"


def test_waiting_for_mass_feedback_does_not_block_emergency_balance():
    policy, current = after_opening()
    current = state(.41, tank=.3)
    policy.ingest(current)
    policy.mass_check = (current.health(), 0., 1., current.profile_revision)
    assert policy.decide(current, current.at).skill == "balance"


def test_mass_major_precedes_repeat_group_when_targeted_heals_on_cooldown():
    policy, current = after_opening()
    policy.ready_at.update(balance=100., battle=100., major=100., group=0.)
    assert policy.decide(current, current.at).skill == "mass_major"


def test_destroyer_still_blocks_balance_and_targeted_heal():
    policy, _ = after_opening()
    current = state(.41, tank=.2).model_copy(update={"destroyer_slots": (3,)})
    result = policy.decide(current, .41)
    assert result.skill != "balance"
    assert result.target != "@slot3"


def test_recovered_party_keeps_group_buff_until_fifteen_seconds_expire():
    policy, _ = after_opening()
    for index in range(4, 40):
        policy.ingest(state(index / 10))
    assert not policy.mass_opened
    assert policy.decide(state(4., tank=.6), 4.).skill == "mass_major"
    for index in range(41, 153):
        policy.ingest(state(index / 10))
    assert policy.decide(state(15.3, tank=.6), 15.3).skill == "group"
