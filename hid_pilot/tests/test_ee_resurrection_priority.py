import pytest

from hid_pilot.farm_ee import EEConfig, EEDecision
from test_farm_ee import policy, runtime
from test_healer import snapshot


@pytest.mark.parametrize("mp", [0., .04, .2, 1.])
def test_resurrection_is_first_even_with_critical_self_and_party_hp(mp):
    p = policy()
    state = snapshot(hp=.1, mp=mp, party=(.1, 0., .1, .1))
    assert p.decide(state, 10.) == EEDecision(skill="resurrect", target="@slot2")
    p.ready_at["resurrect"] = 30.
    assert p.decide(state, 10.).skill != "resurrect"


def test_configured_resurrection_key_and_cooldown_are_used_and_persisted(tmp_path, monkeypatch):
    farmer, _ = runtime(tmp_path, monkeypatch, snapshot(party=(0., 1.)))
    farmer.ee.config.resurrect_key = "F4"
    farmer.ee.config.resurrect_cooldown = 45
    farmer.execute_ee(EEDecision(skill="resurrect", target="@slot1"), 100)
    farmer.controller.key.assert_called_once_with("F4", 100)
    assert farmer.ee.ready_at["resurrect"] == 55
    farmer.restore_timers()
    assert farmer.ee.ready_at["resurrect"] == pytest.approx(55.)


@pytest.mark.parametrize("settings", [{"resurrect_key": "BACKSLASH"}, {"resurrect_cooldown": 0},
                                      {"resurrect_cooldown": -1}, {"resurrect_cooldown": 3601}])
def test_resurrection_settings_are_validated(settings):
    with pytest.raises(ValueError):
        EEConfig(**settings)
