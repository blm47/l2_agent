import pytest

from hid_pilot.farm_ee import EEConfig, EEDecision
from test_farm_ee import policy
from test_healer import snapshot


@pytest.mark.parametrize("party", [(), (1., 1.), (None, None)])
def test_default_self_heal_does_not_require_party_healing(party):
    p = policy()
    assert p.config.self_heal_hp_threshold == .75
    assert p.decide(snapshot(hp=.749, party=party), 10.) == EEDecision(skill="single", target="@self")
    decision = p.decide(snapshot(hp=.75, party=party), 10.)
    assert decision is None or decision != EEDecision(skill="single", target="@self")


def test_party_and_self_thresholds_are_independent():
    p = policy()
    p.config.self_heal_hp_threshold = .6
    p.config.heal_hp_threshold = .9
    assert p.decide(snapshot(hp=.59), 10.) == EEDecision(skill="single", target="@self")
    assert p.decide(snapshot(hp=.6), 10.) != EEDecision(skill="single", target="@self")
    p.config.heal_hp_threshold = .1
    assert p.decide(snapshot(hp=.59), 10.) == EEDecision(skill="single", target="@self")


def test_self_heal_respects_busy_and_cooldown():
    p = policy()
    state = snapshot(hp=.7)
    p.busy_until = 11.
    assert p.decide(state, 10.) is None
    p.busy_until = 0
    p.ready_at["single"] = 12.
    assert p.decide(state, 10.) != EEDecision(skill="single", target="@self")


@pytest.mark.parametrize("value", [0, -.1, 1.01])
def test_self_heal_threshold_rejects_invalid_fraction(value):
    with pytest.raises(ValueError):
        EEConfig(self_heal_hp_threshold=value)
