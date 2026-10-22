from unittest.mock import Mock

import pytest

from hid_pilot.target import TargetObservation
from test_melee_farm import harness


@pytest.mark.parametrize("own, target, rejected", [(20, 24, False), (20, 25, True),
                                                  (20, 30, True), (20, 19, False),
                                                  (None, 30, False), (20, None, False)])
def test_level_filter_requires_both_levels_and_five_level_difference(own, target, rejected):
    farmer, controller, _, _ = harness()
    farmer.display_data = ({"level": own}, {}, [])
    farmer.last_target = TargetObservation(at=0, kind="monster", level=target)
    assert farmer.reject_high_level_target() is rejected
    if rejected:
        controller.key.assert_called_once_with("ESC", 100)
    else:
        controller.key.assert_not_called()


def test_high_next_target_is_cancelled_and_replaced_from_name_pool():
    farmer, controller, _, _ = harness()
    farmer.display_data = ({"level": 20}, {}, [])
    farmer.known_mobs = {"Strong": 25, "Suitable": 24}

    def observe():
        name = "Suitable" if controller.target_by_name.called else "Strong"
        farmer.last_target = TargetObservation(at=0, kind="monster", name=name,
                                               level=farmer.known_mobs[name], confidence=.99)
        return 1., 1., 1.

    farmer.observe = observe
    assert farmer.acquire() == 1.
    controller.target_by_name.assert_called_once_with("Suitable")
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target, "ESC"]


def test_high_target_never_receives_opening_attack_or_overhit():
    farmer, controller, _, _ = harness(overhit_keys=("F1",), overhit_during_combat=True)
    farmer.display_data = ({"level": 20}, {}, [])
    farmer.last_target = TargetObservation(at=0, kind="monster", level=25)
    farmer.search_known_target = Mock(return_value=None)
    farmer.fight(1.)
    controller.key.assert_called_once_with("ESC", 100)
    farmer.search_known_target.assert_called_once_with()


def test_level_recognized_during_combat_stops_further_attacks():
    farmer, controller, _, _ = harness()
    farmer.display_data = ({"level": 20}, {}, [])
    farmer.search_known_target = Mock(return_value=None)

    def observe():
        farmer.last_target = TargetObservation(at=0, kind="monster", level=25)
        return 1., 1., 1.

    farmer.observe = observe
    farmer.fight(1.)
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.attack, "ESC"]
    farmer.search_known_target.assert_called_once_with()


def test_unknown_pool_level_is_checked_after_chat_selection():
    farmer, controller, _, _ = harness()
    farmer.display_data = ({"level": 20}, {}, [])
    farmer.known_mobs = {"Strong": None, "Suitable": 22}
    farmer.target_name_pool = ["Suitable", "Strong"]

    def observe():
        if not controller.target_by_name.called:
            return 1., 1., None
        name = controller.target_by_name.call_args.args[0]
        farmer.last_target = TargetObservation(at=0, kind="monster", name=name,
                                               level=25 if name == "Strong" else 22, confidence=.99)
        return 1., 1., 1.

    farmer.observe = observe
    assert farmer.search_known_target() == 1.
    assert [call.args[0] for call in controller.target_by_name.call_args_list] == ["Strong", "Suitable"]
    controller.key.assert_called_once_with("ESC", 100)
