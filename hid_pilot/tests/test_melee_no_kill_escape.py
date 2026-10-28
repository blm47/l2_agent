from unittest.mock import Mock

import pytest

from test_melee_escape import escape_harness, movements, named
from test_melee_farm import harness
from hid_pilot.controller import Stopped


@pytest.mark.parametrize("elapsed,attempts,expected", [(120, 2, False), (120.01, 2, True),
                                                     (150, 1, False), (150, 0, False)])
def test_no_kill_escape_requires_over_two_minutes_and_repeated_targets(elapsed, attempts, expected):
    farmer, controller, now, _ = harness()
    farmer.kill_watch_attempts = attempts
    now[0] = elapsed
    farmer.escape_from_unreachable = Mock(return_value=None)
    assert farmer.escape_if_no_kills() is expected
    assert farmer.escape_from_unreachable.call_count == int(expected)
    if not expected:
        controller.key.assert_not_called()


def test_different_names_and_unconfirmed_kills_do_not_reset_watch():
    farmer, _, now, _ = harness()
    farmer.kill_watch_failed_names.update({"Sentinel of Water", "Patin Archer"})
    farmer.kill_watch_attempts = 5
    farmer.reset_unreachable()
    farmer.mob_statistics.record_unconfirmed_kill()
    now[0] = 121
    farmer.escape_from_unreachable = Mock(return_value=None)
    assert farmer.escape_if_no_kills()
    farmer.escape_from_unreachable.assert_called_once_with(
        None, blocked_names={"Sentinel of Water", "Patin Archer"})
    assert not farmer.escape_if_no_kills()


def test_confirmed_kill_restarts_watch_and_clears_failed_targets():
    farmer, _, now, _ = harness()
    farmer.kill_watch_attempts = 8
    farmer.kill_watch_failed_names.add("Old Mob")
    now[0] = 121
    farmer.mob_statistics.record_kill("Killed Mob")
    farmer.escape_from_unreachable = Mock()
    assert not farmer.escape_if_no_kills()
    assert farmer.kill_watch_at == 121
    assert farmer.kill_watch_attempts == 0
    assert not farmer.kill_watch_failed_names
    farmer.escape_from_unreachable.assert_not_called()


@pytest.mark.parametrize("flag", ["sitting", "in_recovery", "under_attack", "casting_ma_buff", "buffing"])
def test_escape_defers_to_active_rest_combat_or_buff(flag):
    farmer, _, now, _ = harness()
    farmer.kill_watch_attempts = 3
    now[0] = 121
    setattr(farmer, flag, True)
    farmer.escape_from_unreachable = Mock()
    assert not farmer.escape_if_no_kills()
    farmer.escape_from_unreachable.assert_not_called()


def test_run_cycle_uses_shared_escape_and_attacks_returned_target():
    farmer, _, now, _ = harness(regen_enabled=False)
    farmer.kill_watch_attempts = 3
    now[0] = 121
    farmer.cast_buffs = Mock()
    farmer.escape_from_unreachable = Mock(return_value=.7)
    farmer.fight = Mock()
    farmer.run_cycle(0)
    farmer.escape_from_unreachable.assert_called_once()
    farmer.fight.assert_called_once_with(.7)


def test_timed_escape_does_not_stop_on_another_previously_failed_name(monkeypatch):
    farmer, controller, _ = escape_harness(monkeypatch)
    names = ["Patin Archer", "Lakin Salamander"]

    def observe():
        named(farmer, names[len(movements(controller)) % 2])
        return 1., 1., 1.

    farmer.observe = observe
    assert farmer.escape_from_unreachable(None, blocked_names=set(names)) is None
    assert len(movements(controller)) == 40


def test_timed_escape_moves_before_accepting_an_unblocked_target(monkeypatch):
    farmer, controller, _ = escape_harness(monkeypatch)

    def observe():
        named(farmer, "New Mob")
        return 1., 1., .8

    farmer.observe = observe
    assert farmer.escape_from_unreachable(None, blocked_names={"Old Mob"}) == .8
    assert len(movements(controller)) == 4


def test_incoming_damage_interrupts_timed_escape_even_for_blocked_name(monkeypatch):
    farmer, controller, _ = escape_harness(monkeypatch)

    def observe():
        named(farmer, "Blocked Mob")
        return (.9 if movements(controller) else 1.), 1., .7

    farmer.observe = observe
    assert farmer.escape_from_unreachable(None, blocked_names={"Blocked Mob"}) == .7
    assert len(movements(controller)) == 1
    assert farmer.under_attack


def test_stop_during_timed_recovery_sends_no_movement():
    farmer, controller, now, _ = harness()
    now[0] = 121
    farmer.kill_watch_attempts = 3
    controller.key.side_effect = Stopped("STOP")
    with pytest.raises(Stopped):
        farmer.escape_if_no_kills()
    controller.key.assert_called_once_with("ESC", 100)
