import pytest

from hid_pilot.farm_ee import EEDecision
from test_farm_ee import runtime, mana_reserve_state


def sample(at, mp=.1):
    return mana_reserve_state(at=at, own_mp=1., members_mp=(mp, .5))


def test_unchanged_mp_excludes_recharge_for_thirty_seconds_without_wait(tmp_path, monkeypatch):
    farmer, now = runtime(tmp_path, monkeypatch, sample(10.))
    farmer.record_mana_effect(EEDecision(skill="mana", target="@slot1"), sample(10.), 10., 1.)
    now[0] = 11.5
    farmer.consume_mana_effects(tuple(sample(at) for at in (10.25, 10.75, 11.5)))
    assert not farmer.ee.can_receive_mana("@slot1", sample(11.5))
    assert farmer.ee.can_receive_mana("@slot1", sample(11.5), "gain")
    assert farmer.ee.can_receive_mana("@slot2", sample(11.5))
    assert farmer.ee.can_receive_mana("@slot1", sample(41.5))
    farmer.controller.wait.assert_not_called()
    farmer.report.assert_called_once()


def test_any_observed_mp_increase_cancels_no_effect_check(tmp_path, monkeypatch):
    farmer, now = runtime(tmp_path, monkeypatch, sample(10.))
    farmer.record_mana_effect(EEDecision(skill="mana", target="@slot1"), sample(10.), 10., 1.)
    now[0] = 11.5
    farmer.consume_mana_effects((sample(10.3, .101), sample(10.9), sample(11.5)))
    assert not farmer.ee.mana_no_effect_until
    assert not farmer.mana_effect_checks


@pytest.mark.parametrize("reason", ["gap", "missing_mp", "missing_name", "revision", "pause", "stale", "duplicate"])
def test_incomplete_observations_do_not_imply_failed_recharge(tmp_path, monkeypatch, reason):
    farmer, now = runtime(tmp_path, monkeypatch, sample(10.))
    farmer.record_mana_effect(EEDecision(skill="mana", target="@slot1"), sample(10.), 10., 1.)
    states = [sample(10.2), sample(10.8), sample(11.5)]
    if reason == "gap":
        states = [sample(11.5)]
    elif reason == "missing_mp":
        states[1] = sample(10.8, None)
    elif reason == "missing_name":
        states[1] = states[1].model_copy(update={"members": tuple(
            m.model_copy(update={"name": None}) for m in states[1].members)})
    elif reason == "revision":
        states[1] = states[1].model_copy(update={"profile_revision": 1})
    elif reason == "pause":
        farmer.controller.pause_generation = 1
    elif reason == "duplicate":
        states = [sample(10.2)] * 5
    now[0] = 20. if reason == "stale" else 11.5
    farmer.consume_mana_effects(tuple(states))
    assert not farmer.ee.mana_no_effect_until


def test_mana_gain_does_not_start_recharge_effect_check(tmp_path, monkeypatch):
    farmer, _ = runtime(tmp_path, monkeypatch, sample(10.))
    farmer.record_mana_effect(EEDecision(skill="gain", target="@slot1"), sample(10.), 10., 2.)
    assert not getattr(farmer, "mana_effect_checks", {})
