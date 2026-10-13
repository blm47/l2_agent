from test_train_healer import collecting, state
import pytest

from hid_pilot.healer import HealerConfig
from hid_pilot.train_healer import TrainConfig, TrainPhase, TrainPolicy


def test_both_healers_default_to_nine_and_train_interval_is_two_point_five():
    assert HealerConfig().follow_key == TrainConfig().follow_key == "9"
    assert TrainConfig().follow_seconds == 2.5


@pytest.mark.parametrize("phase", list(TrainPhase))
def test_follow_remains_enabled_in_every_phase_between_casts(phase):
    policy = TrainPolicy(TrainConfig(mp_buff_key=None), 0.)
    current = state(.1)
    policy.ingest(current)
    policy.phase = phase
    result = policy.decide(current, .1)
    assert (result.kind, result.target) == ("follow", "@slot1")
    policy.busy_until = 1.
    assert policy.decide(current, .1).kind == "wait"


def test_follow_repeats_in_combat_without_postponing_timer_each_tick():
    policy = TrainPolicy(TrainConfig(mp_buff_key=None), 0.)
    policy.ingest(state(0))
    first_state = state(.1, hp=.999, others=(.999, 1.))
    first = policy.decide(first_state, .1)
    assert policy.phase == TrainPhase.COMBAT
    assert first.kind == "follow"
    policy.committed(first, first_state, .1)
    for index in range(2, 26):
        current = state(index / 10, hp=1 - index * .001, others=(1 - index * .001, 1.))
        assert policy.decide(current, index / 10).kind == "wait"
        assert policy.phase == TrainPhase.COMBAT
    assert policy.decide(state(2.6, hp=.974, others=(.974, 1.)), 2.6).kind == "follow"


def test_collection_mass_heal_never_follows_or_selects_collector():
    policy = collecting(.4)
    current = state(2.7, tank=.4, paralyzed=(3,))
    follow = policy.decide(current, 2.7)
    assert (follow.skill, follow.target) == ("group", None)
    policy.committed(follow, current, 2.7)
    assert policy.decide(state(2.8, tank=.4, paralyzed=(3,)), 2.8).kind == "wait"


def test_follow_available_with_unknown_tank_and_low_mp():
    policy = TrainPolicy(TrainConfig(), 0.)
    current = state(0, tank=None, known=False)
    assert policy.decide(current, 0).kind == "follow"
    policy.ingest(state(.05))
    low_mp = state(.1).model_copy(update={"mp": 0.})
    assert policy.decide(low_mp, .1).kind == "follow"
