import pytest

from test_duo import make_duo
from test_melee_farm import harness


@pytest.mark.parametrize("kills", [0, 1, 2])
@pytest.mark.parametrize("duo_mode", [False, True])
def test_damage_skills_are_available_before_three_kills(kills, duo_mode):
    settings = {"damage_keys": ("4", "5"), "overhit_keys": (), "next_target": "7",
                "next_target_near": "6", "heal": "F6", "loot": "C"}
    if duo_mode:
        farmer, controller = make_duo(**settings)
        controller.capture = lambda: (1., .9, False)
        farmer.observe_ma()
    else:
        farmer, controller, _, _ = harness(**settings)
    farmer.training_kills = kills
    assert farmer.estimated_damage(None) is None
    assert farmer.use_damage_skill(.9, None)
    assert farmer.use_damage_skill(.9, None)
    assert not farmer.use_damage_skill(.9, None)
    if duo_mode:
        assert (1, "4") in controller.events
        assert (1, "5") in controller.events
    else:
        assert [call.args[0] for call in controller.key.call_args_list] == ["4", "5"]


def test_overhit_can_use_damage_estimate_before_three_kills():
    farmer, controller, _, _ = harness(damage_keys=("4",), overhit_keys=("5",),
                                       next_target="7", next_target_near="6", heal="F6", loot="C")
    farmer.current_damage_samples = [.1]
    assert farmer.training_kills == 0
    assert farmer.use_damage_skill(.1, None)
    assert controller.key.call_args.args[0] == "5"
