import pytest

from hid_pilot.farm_ee import EEDecision
from test_farm_ee import policy, mana_reserve_state


@pytest.mark.parametrize("player_class", [None, "Elven Elder", "Shilien Elder", "Hawkeye"])
@pytest.mark.parametrize("own_mp", [.08, 1.])
def test_class_never_excludes_mana_without_chat_warning(player_class, own_mp):
    p = policy()
    p.ready_at["bless"] = 9999
    state = mana_reserve_state(own_mp=own_mp, members_mp=(.01, .1))
    state = state.model_copy(update={"selected_name": "A", "selected_class": player_class,
                                     "selected_confidence": .99})
    assert p.decide(state, 10.) == EEDecision(skill="mana", target="@slot1")


def test_warning_exclusion_follows_name_not_party_slot():
    p = policy()
    p.mana_chat_blocked_names.add("a")
    state = mana_reserve_state(own_mp=1., members_mp=(.01, .1))
    assert p.decide(state, 10.).target == "@slot2"
    moved = state.model_copy(update={"members": tuple(
        m.model_copy(update={"name": "B" if m.slot == 1 else "a"}) for m in state.members)})
    assert p.decide(moved, 10.).target == "@slot1"
