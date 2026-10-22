import pytest

from hid_pilot.farm_ee import EEDecision
from test_farm_ee import runtime, mana_reserve_state


@pytest.mark.parametrize("skill", ["mana", "gain"])
@pytest.mark.parametrize("player_class", [None, "Elven Elder", "Shilien Elder"])
def test_cast_has_no_ocr_wait_and_accepts_all_classes(tmp_path, monkeypatch, skill, player_class):
    state = mana_reserve_state(own_mp=1., members_mp=(.01, .1))
    state = state.model_copy(update={"selected_class": player_class})
    farmer, now = runtime(tmp_path, monkeypatch, state)
    if skill == "gain":
        farmer.ee.ready_at.update(mana=9999, clarity=9999, shock=9999, kiss=9999)
        farmer.ee.ready_at["buff:gain:self"] = 9999
    farmer.execute_ee(EEDecision(skill=skill, target="@slot1"), 100)
    farmer.controller.key.assert_called_once_with("3" if skill == "mana" else "NUM0", 100)
    cast = 1. if skill == "mana" else farmer.config.buff_cast
    farmer.controller.wait.assert_called_once_with(cast)
    assert now[0] == 10. + cast
