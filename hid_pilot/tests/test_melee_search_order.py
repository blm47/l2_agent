from test_melee_farm import harness


def test_missing_next_target_uses_name_before_movement():
    farmer, controller, _, _ = harness(regen_enabled=False)
    farmer.known_mobs = {"Gremlin": None}
    farmer.observe = lambda: (1., 1., 1. if controller.target_by_name.called else None)

    assert farmer.search_cycle() == 1.

    controller.target_by_name.assert_called_once_with("Gremlin")
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target]


def test_movement_follows_failed_next_target_and_name_selection():
    farmer, controller, _, state = harness(regen_enabled=False, search_moves=1)
    farmer.known_mobs = {"Gremlin": None}
    state[2] = None
    events = []
    controller.key.side_effect = lambda key, *args, **kwargs: events.append(
        "next" if key == farmer.config.next_target else "move")
    controller.target_by_name.side_effect = lambda name: events.append("name")
    farmer.on_target_missing = lambda: events.append("follow")

    assert farmer.search_cycle() is None

    assert events == ["next", "name", "follow", "move", "next", "name", "follow"]


def test_live_next_target_does_not_use_name_or_movement():
    farmer, controller, _, _ = harness(regen_enabled=False)

    assert farmer.search_cycle() == 1.

    controller.target_by_name.assert_not_called()
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target]
