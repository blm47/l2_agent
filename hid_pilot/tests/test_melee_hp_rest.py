from unittest.mock import Mock

import pytest
from test_melee_farm import harness

from hid_pilot.farm import Farmer
from hid_pilot.farm_melee import MeleeConfig


def test_low_hp_sits_even_with_selected_live_target_and_normal_mp():
    farmer, controller, now, state = harness()
    farmer.quiet_since = 0.
    state[:] = [.29, .5, 1.]
    farmer.fight = Mock()
    original_wait = controller.wait.side_effect
    seated_at = []

    def wait(seconds):
        original_wait(seconds)
        if farmer.sitting:
            seated_at.append(now[0])
            state[0] = .90

    controller.wait.side_effect = wait
    farmer.recover()
    assert seated_at[0] >= farmer.config.quiet_seconds
    assert [call.args[0] for call in controller.key.call_args_list] == ["=", "-"]
    farmer.fight.assert_not_called()
    assert not farmer.sitting


def test_hp_rest_kills_aggro_then_sits_until_ninety_percent():
    farmer, controller, now, state = harness()
    state[:] = [.2, .5, 1.]
    farmer.last_resources = (.21, None)
    farmer.under_attack = True
    farmer.quiet_since = 0.

    def fight():
        assert not farmer.sitting
        farmer.under_attack = False
        now[0] = 3.
        state[2] = 0.

    farmer.fight = Mock(side_effect=fight)
    original_wait = controller.wait.side_effect

    def wait(seconds):
        original_wait(seconds)
        if farmer.sitting:
            state[0] = min(.9, state[0] + .1)

    controller.wait.side_effect = wait
    farmer.recover()
    farmer.fight.assert_called_once_with()
    assert state[0] == .9
    assert [call.args[0] for call in controller.key.call_args_list] == ["=", "-"]


@pytest.mark.parametrize("hp,expected", [(.29, True), (.3, False), (.4, False)])
def test_run_hp_rest_trigger_is_strictly_below_thirty_percent(hp, expected):
    farmer, _, _, state = harness(cycles=1)
    state[0] = hp
    farmer.recover = Mock()
    farmer.fight = Mock()
    farmer.run()
    assert farmer.recover.called is expected


def test_rest_request_survives_heal_above_thirty_percent():
    farmer, controller, _, state = harness()
    farmer.rest_requested = True
    farmer.quiet_since = -5.
    state[:] = [.4, .7, None]
    original_wait = controller.wait.side_effect

    def wait(seconds):
        original_wait(seconds)
        if farmer.sitting:
            state[0] = .9

    controller.wait.side_effect = wait
    farmer.recover()
    assert [call.args[0] for call in controller.key.call_args_list] == ["=", "-"]
    assert not farmer.rest_requested


def test_resume_hp_must_exceed_rest_threshold():
    with pytest.raises(ValueError):
        MeleeConfig(rest="=", hp_rest_threshold=.3, hp_resume=.3)


def test_quiet_seconds_defaults_to_two_and_half_seconds():
    assert MeleeConfig(rest="=").quiet_seconds == 2.5


@pytest.mark.parametrize("seconds", [1, 2.5, 10])
def test_quiet_seconds_accepts_requested_range(seconds):
    assert MeleeConfig(rest="=", quiet_seconds=seconds).quiet_seconds == seconds


@pytest.mark.parametrize("seconds", [.99, 10.01])
def test_quiet_seconds_rejects_values_outside_requested_range(seconds):
    with pytest.raises(ValueError):
        MeleeConfig(rest="=", quiet_seconds=seconds)


def test_observation_latches_rest_after_hp_drops_below_threshold(monkeypatch):
    farmer, _, _, state = harness()
    del farmer.observe
    monkeypatch.setattr(Farmer, "observe", lambda self: tuple(state))
    state[0] = .29
    farmer.observe()
    assert farmer.rest_requested
    state[0] = .4
    farmer.observe()
    assert farmer.rest_requested


def test_acquire_checks_health_before_next_target():
    farmer, controller, _, state = harness()
    state[0] = .2

    def recover():
        controller.key.assert_not_called()
        state[0] = .9

    farmer.recover = Mock(side_effect=recover)
    assert farmer.acquire() == 1.
    farmer.recover.assert_called_once_with()
    assert controller.key.call_args_list[0].args[0] == farmer.config.next_target


def test_attack_during_rest_stands_then_selects_next_target_without_recursive_rest():
    farmer, controller, _, state = harness()
    farmer.quiet_since = 0.
    state[:] = [.2, .8, 1.]
    original_wait = controller.wait.side_effect

    def wait(seconds):
        original_wait(seconds)
        if farmer.sitting:
            state[0] = .19
            farmer.under_attack = True

    def fight():
        assert not farmer.sitting
        assert farmer.in_recovery
        assert farmer.acquire() == 1.
        state[0] = .9
        farmer.under_attack = False

    controller.wait.side_effect = wait
    farmer.fight = Mock(side_effect=fight)
    farmer.recover()
    farmer.fight.assert_called_once_with()
    assert [call.args[0] for call in controller.key.call_args_list] == ["=", farmer.config.next_target]
    assert not farmer.in_recovery
