import pytest

from hid_pilot.summon_recovery import SummonRecovery


def sample(state, at, hp, generation=0, damaged=False, **resources):
    return state.observe({"farmer_hp": 1., "support_hp": 1., "summon_hp": hp,
                          **resources}, at, generation, damaged, .005)


def test_startup_requires_three_separate_missing_readings():
    state = SummonRecovery()
    sample(state, 0, None)
    sample(state, 0, None)
    sample(state, .2, None)
    assert not state.pending
    assert sample(state, .4, None)
    assert state.checked and state.pending


def test_alive_startup_and_single_missing_reading_do_not_request_summon():
    state = SummonRecovery()
    sample(state, 0, 1.)
    sample(state, .2, .4)
    sample(state, .4, None)
    sample(state, .6, .4)
    assert state.checked and not state.pending


def test_zero_hp_requests_recovery_once_without_killing_party():
    state = SummonRecovery()
    sample(state, 0, 1.)
    assert sample(state, .2, 0.)
    assert not sample(state, .4, None)
    assert state.pending


def test_missing_hp_requires_two_seconds_after_recent_damage():
    state = SummonRecovery()
    sample(state, 0, 1.)
    sample(state, .2, .2)
    for at in (.4, .8, 1.2, 1.6, 2.):
        sample(state, at, None)
        assert not state.pending
    assert sample(state, 2.4, None)


@pytest.mark.parametrize("reason", ["no_damage", "healed", "old_damage", "pause", "gap"])
def test_sustained_missing_resources_request_recovery_even_without_recent_damage(reason):
    state = SummonRecovery()
    sample(state, 0, 1.)
    sample(state, .2, 1. if reason == "no_damage" else .2)
    if reason == "healed":
        sample(state, .3, 1.)
    offset = 3. if reason == "gap" else 0.
    if reason == "old_damage":
        for at in (.6, 1., 1.4, 1.8, 2.2, 2.6):
            sample(state, at, .2)
        offset = 2.4
    for at in (.4, .8, 1.2, 1.6, 2., 2.4, 2.8):
        sample(state, at + offset, None, generation=int(reason == "pause"))
    assert state.pending


@pytest.mark.parametrize("generation,at", [(1, .4), (0, 3.)])
def test_pause_or_capture_gap_requires_new_missing_interval(generation, at):
    state = SummonRecovery()
    sample(state, 0, 1.)
    sample(state, .2, None)
    sample(state, at, None, generation=generation)
    assert not state.pending
    for offset in (.5, 1., 1.5):
        sample(state, at + offset, None, generation=generation)
        assert not state.pending
    assert sample(state, at + 2., None, generation=generation)


@pytest.mark.parametrize("initial_hp", [None, 1.])
def test_readable_mp_prevents_recovery_for_unreadable_hp(initial_hp):
    state = SummonRecovery()
    sample(state, 0, initial_hp)
    for at in (.2, .6, 1., 1.4, 1.8, 2.2, 2.6):
        sample(state, at, None, summon_mp=.5)
    assert not state.pending


def test_returning_panel_resets_missing_interval():
    state = SummonRecovery()
    sample(state, 0, 1.)
    for at in (.2, .7, 1.2, 1.7):
        sample(state, at, None)
    sample(state, 1.8, .2)
    sample(state, 2., None)
    assert not state.pending
    for at in (2.5, 3., 3.5):
        sample(state, at, None)
    assert sample(state, 4., None)


def test_all_party_health_and_known_cp_must_stay_observable_during_quiet_window():
    state = SummonRecovery()
    for at in (0., .5, 1., 1.5, 2., 2.5):
        sample(state, at, None, party_2_hp=.8, party_2_cp=.7)
    assert state.quiet(2.5, 2.5, 0)
    sample(state, 2.6, None, party_2_hp=.8, party_2_cp=None)
    assert not state.quiet(2.6, 2.5, 0)
    sample(state, 2.8, None)
    assert state.required_resources == {"farmer_hp", "support_hp"}
    assert state.quiet_since == 2.8
    for at in (3.3, 3.8, 4.3, 4.8, 5.3):
        sample(state, at, None)
    assert state.quiet(5.3, 2.5, 0)


@pytest.mark.parametrize("reason", ["damage", "gap", "pause", "unknown_hp"])
def test_damage_unknown_resources_and_pause_reset_quiet_interval(reason):
    state = SummonRecovery()
    for at in (0., .5, 1., 1.5, 2., 2.5):
        sample(state, at, None)
    assert state.quiet(2.5, 2.5, 0)
    at = 4. if reason == "gap" else 2.6
    generation = int(reason == "pause")
    sample(state, at, None, generation=generation, damaged=reason == "damage",
           support_hp=None if reason == "unknown_hp" else .9)
    assert not state.quiet(at, 2.5, generation)
    assert state.pending


def test_same_frame_and_stale_observation_cannot_complete_quiet_interval():
    state = SummonRecovery()
    sample(state, 0., None)
    assert not state.quiet(3., 2.5, 0)


def test_configured_but_unreadable_farmer_cp_does_not_confirm_safety():
    state = SummonRecovery()
    for at in (0., .5, 1., 1.5, 2., 2.5):
        sample(state, at, None, farmer_cp=None)
    assert not state.quiet(2.5, 2.5, 0)


def test_temporary_party_row_cannot_block_summon_forever():
    state = SummonRecovery()
    sample(state, 0., None, party_3_hp=.8, party_3_cp=.5)
    for index in range(1, 3601):
        sample(state, index * .5, None)
    assert state.pending
    assert state.quiet(1800., 2.5, 0)
    assert state.required_resources == {"farmer_hp", "support_hp"}


def test_visible_party_member_with_unknown_hp_still_blocks_safety():
    state = SummonRecovery()
    for at in (0., .5, 1., 1.5, 2., 2.5, 3.):
        sample(state, at, None, party_2_hp=None, party_2_cp=.5)
    assert not state.quiet(3., 2.5, 0)
    assert "party_2_hp" in state.wait_reason(
        {"farmer_hp": 1., "support_hp": 1., "party_2_cp": .5}, 3., 2.5, 0)
