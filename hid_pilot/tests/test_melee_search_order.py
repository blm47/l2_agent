import pytest

from test_melee_farm import harness


@pytest.mark.parametrize("delay", [0, .2, .25, 1])
def test_far_target_is_checked_after_near_and_wait_before_each_observation(delay):
    farmer, controller, _, _ = harness(regen_enabled=False, target_delay=delay)
    events = []
    controller.key.side_effect = lambda key, *args, **kwargs: events.append(key)
    controller.wait.side_effect = lambda seconds: events.append(seconds)

    def observe():
        assert events[-1] == .3
        return 1., 1., .9 if events[-2] == farmer.config.next_target else None

    farmer.observe = observe
    assert farmer.search_cycle() == .9
    assert events == ["7", .3, "6", .3]
    controller.target_by_name.assert_not_called()


def test_name_lookup_follows_both_next_target_waits():
    farmer, controller, _, _ = harness(regen_enabled=False, target_delay=.2)
    farmer.known_mobs = {"Gremlin": None}
    events = []
    controller.key.side_effect = lambda key, *args, **kwargs: events.append(key)
    controller.wait.side_effect = lambda seconds: events.append(seconds)
    controller.target_by_name.side_effect = lambda name: events.append(name)
    farmer.observe = lambda: (1., 1., .9 if "Gremlin" in events else None)
    assert farmer.search_cycle() == .9
    assert events[:13] == ["7", .3, "6", .3] * 3 + ["Gremlin"]


def test_missing_next_target_uses_name_before_movement():
    farmer, controller, _, _ = harness(regen_enabled=False)
    farmer.known_mobs = {"Gremlin": None}
    farmer.observe = lambda: (1., 1., 1. if controller.target_by_name.called else None)

    assert farmer.search_cycle() == 1.

    controller.target_by_name.assert_called_once_with("Gremlin")
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target_near, farmer.config.next_target] * 3


def test_movement_follows_failed_next_target_and_name_selection():
    farmer, controller, _, state = harness(regen_enabled=False, search_moves=1)
    farmer.known_mobs = {"Gremlin": None}
    state[2] = None
    events = []
    controller.key.side_effect = lambda key, *args, **kwargs: events.append(
        "next" if key in (farmer.config.next_target_near, farmer.config.next_target) else "move")
    controller.target_by_name.side_effect = lambda name: events.append("name")
    farmer.on_target_missing = lambda: events.append("follow")

    assert farmer.search_cycle() is None

    assert events == ["next"] * 6 + ["name", "follow", "move"] + ["next"] * 6 + ["name", "follow"]


def test_live_next_target_does_not_use_name_or_movement():
    farmer, controller, _, _ = harness(regen_enabled=False)

    assert farmer.search_cycle() == 1.

    controller.target_by_name.assert_not_called()
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target_near]


def test_incoming_damage_retries_next_target_without_names_or_movement():
    farmer, controller, _, _ = harness(regen_enabled=False, search_moves=1)
    farmer.known_mobs = {"Gremlin": None}

    def observe():
        farmer.under_attack = True
        return .7, 1., None

    farmer.observe = observe
    assert farmer.search_cycle() is None
    controller.target_by_name.assert_not_called()
    assert [call.args[0] for call in controller.key.call_args_list] == ["7", "6"] * 6


def test_direct_name_search_is_blocked_under_attack():
    farmer, controller, _, _ = harness(regen_enabled=False)
    farmer.known_mobs = {"Gremlin": None}
    farmer.under_attack = True
    assert farmer.search_known_target() is None
    controller.target_by_name.assert_not_called()


def test_damage_detected_during_name_preparation_preserves_pool():
    farmer, controller, _, _ = harness(regen_enabled=False)
    farmer.known_mobs = {"Gremlin": None}
    farmer.before_target_selection = lambda: setattr(farmer, "under_attack", True)
    assert farmer.search_known_target() is None
    assert farmer.target_name_pool == ["Gremlin"]
    controller.target_by_name.assert_not_called()


@pytest.mark.parametrize("success_at", range(1, 7))
def test_retries_stop_at_first_live_target(success_at):
    farmer, controller, _, _ = harness(regen_enabled=False)
    reads = 0

    def observe():
        nonlocal reads
        reads += 1
        return 1., 1., .9 if reads == success_at else 0.

    farmer.observe = observe
    assert farmer.search_next_targets() == .9
    assert reads == success_at
    assert [call.args[0] for call in controller.key.call_args_list] == (["7", "6"] * 3)[:success_at]
    assert [call.args for call in controller.wait.call_args_list] == [(.3,)] * success_at


def test_pause_cancels_retries_before_next_key():
    farmer, controller, _, _ = harness(regen_enabled=False)
    controller.pause_generation = 0

    def observe():
        controller.pause_generation += 1
        return 1., 1., None

    farmer.observe = observe
    assert farmer.search_next_targets() is None
    assert controller.key.call_count == 1


def test_regular_farmer_support_uses_three_passes_and_stops_on_success():
    from unittest.mock import Mock
    from test_duo import make_duo

    duo, controller = make_duo()
    assert not duo.config.offparty_support
    duo.observe = Mock(side_effect=[(1., 1., 0.)] * 4 + [(1., 1., .8)])
    assert duo.search_next_targets() == .8
    assert [event[1] for event in controller.events] == ["7", "6", "7", "6", "7"]
    assert controller.now == pytest.approx(1.5)
