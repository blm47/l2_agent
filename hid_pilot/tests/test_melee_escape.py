from unittest.mock import Mock

import pytest

from hid_pilot.controller import Stopped
from hid_pilot.target import TargetObservation
from test_melee_farm import harness


def named(farmer, name="Gremlin"):
    farmer.last_target = TargetObservation(at=farmer.clock(), kind="monster", name=name, confidence=.99)


def test_escape_starts_only_after_five_failed_selections_of_same_name():
    farmer, _, _, _ = harness(approach_delay=.2)
    farmer.escape_from_unreachable = Mock(return_value=None)
    for attempt in range(5):
        named(farmer)
        farmer.fight(1.)
        assert farmer.escape_from_unreachable.call_count == (attempt == 4)
    farmer.escape_from_unreachable.assert_called_once_with("Gremlin")


def test_different_name_or_real_damage_breaks_failure_sequence():
    farmer, _, _, _ = harness(approach_delay=.2, stuck_timeout=.5)
    farmer.unreachable_name, farmer.unreachable_selections = "Gremlin", 4
    named(farmer, "Cloudy Beast")
    farmer.fight(1.)
    assert farmer.unreachable_name == "Cloudy Beast" and farmer.unreachable_selections == 1
    named(farmer, "Cloudy Beast")
    farmer.observe = lambda: (1., 1., .8)
    farmer.fight(1.)
    assert farmer.unreachable_name is None and farmer.unreachable_selections == 0


@pytest.mark.parametrize("known_at_start", [False, True])
def test_ocr_name_gaps_do_not_restart_five_failed_selections(known_at_start):
    farmer, controller, _, _ = harness(approach_delay=.8)
    farmer.escape_from_unreachable = Mock(return_value=None)
    for attempt in range(5):
        farmer.last_target = None
        if known_at_start:
            named(farmer, "Giggling Wind")
        reads = [0]

        def observe():
            reads[0] += 1
            if reads[0] == 1:
                named(farmer, "Giggling Wind")
            else:
                farmer.last_target = None
            return 1., 1., 1.

        farmer.observe = observe
        farmer.fight(1.)
        assert farmer.unreachable_selections == attempt + 1
        assert farmer.escape_from_unreachable.call_count == int(attempt == 4)
    farmer.escape_from_unreachable.assert_called_once_with("Giggling Wind")
    assert farmer.kill_watch_failed_names == {"Giggling Wind"}
    assert farmer.training_kills == 0
    assert not farmer.damage_samples
    assert [call.args[0] for call in controller.key.call_args_list].count("ESC") == 5


def test_entirely_unknown_target_keeps_old_failures_without_assigning_old_name():
    farmer, _, _, _ = harness(approach_delay=.2)
    farmer.unreachable_name, farmer.unreachable_selections = "Giggling Wind", 4
    farmer.escape_from_unreachable = Mock()
    farmer.fight(1.)
    assert farmer.unreachable_name == "Giggling Wind"
    assert farmer.unreachable_selections == 4
    assert not farmer.kill_watch_failed_names
    farmer.escape_from_unreachable.assert_not_called()


def test_actual_damage_resets_failures_even_if_name_disappears():
    farmer, _, _, _ = harness(approach_delay=.8, stuck_timeout=.5)
    farmer.unreachable_name, farmer.unreachable_selections = "Giggling Wind", 4
    named(farmer, "Giggling Wind")

    def observe():
        farmer.last_target = None
        return 1., 1., .8

    farmer.observe = observe
    farmer.fight(1.)
    assert farmer.unreachable_name is None
    assert farmer.unreachable_selections == 0


def test_pre_pause_name_is_not_used_for_unreachable_target():
    farmer, controller, _, _ = harness(approach_delay=.2)
    controller.pause_generation = 0
    named(farmer, "Giggling Wind")

    def observe():
        controller.pause_generation = 1
        farmer.last_target = None
        return 1., 1., 1.

    farmer.observe = observe
    farmer.fight(1.)
    assert farmer.unreachable_name is None
    assert farmer.unreachable_selections == 0
    assert not farmer.kill_watch_failed_names


def test_target_drop_log_explains_observed_hp_and_timeout():
    farmer, _, _, _ = harness(approach_delay=.2)
    named(farmer, "Giggling Wind")
    farmer.fight(1.)
    messages = [call.args[0] for call in farmer.notify.call_args_list]
    drop = next(message for message in messages if "сбрасываем недоступную цель" in message)
    assert "причина: сближение без урона" in drop
    assert "HP 100.0%" in drop and "/0.2 с" in drop
    assert "последнее подтверждённое имя в этом бою: Giggling Wind" in drop


def escape_harness(monkeypatch, direction="WA", steps=20):
    farmer, controller, now, _ = harness()
    monkeypatch.setattr("hid_pilot.farm_melee.random.choice", lambda choices: direction)
    monkeypatch.setattr("hid_pilot.farm_melee.random.randint", lambda low, high: steps)

    def observe():
        named(farmer)
        return 1., 1., 1.

    farmer.observe = observe
    return farmer, controller, now


def movements(controller):
    return [call.args for call in controller.key.call_args_list if call.args[0] in "WASD"]


@pytest.mark.parametrize("direction,steps", [("WA", 20), ("SD", 30)])
def test_two_bounded_escape_passes_alternate_keys_and_toggle_rest_once(monkeypatch, direction, steps):
    farmer, controller, _ = escape_harness(monkeypatch, direction, steps)
    assert farmer.escape_from_unreachable("Gremlin") is None
    assert movements(controller) == [(direction[i % 2], 1500) for i in range(steps)] * 2
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys.count(farmer.config.next_target) == steps * 2
    assert keys.count(farmer.config.rest) == 1
    assert keys.index(farmer.config.rest) == steps * 2
    assert farmer.training_kills == 0


@pytest.mark.parametrize("damage_after_selection", [False, True])
def test_damage_stops_escape_and_returns_next_target_for_immediate_combat(monkeypatch, damage_after_selection):
    farmer, controller, _ = escape_harness(monkeypatch)

    def observe():
        hp = .9 if (any(c.args[0] == farmer.config.next_target for c in controller.key.call_args_list)
                     if damage_after_selection else bool(movements(controller))) else 1.
        named(farmer)
        return hp, 1., .7

    farmer.observe = observe
    assert farmer.escape_from_unreachable("Gremlin") == .7
    assert len(movements(controller)) == 1
    assert controller.key.call_args_list[-1].args[0] == farmer.config.next_target
    assert farmer.under_attack
    assert not farmer.sitting


def test_new_named_target_stops_escape_without_waiting_for_all_steps(monkeypatch):
    farmer, controller, _ = escape_harness(monkeypatch)

    def observe():
        named(farmer, "Cloudy Beast" if len(movements(controller)) >= 3 else "Gremlin")
        return 1., 1., .8

    farmer.observe = observe
    assert farmer.escape_from_unreachable("Gremlin") == .8
    assert len(movements(controller)) == 3


def test_unknown_or_dead_new_target_does_not_claim_escape_success(monkeypatch):
    farmer, controller, _ = escape_harness(monkeypatch)

    def observe():
        named(farmer, "Cloudy Beast")
        return 1., 1., 0.

    farmer.observe = observe
    assert farmer.escape_from_unreachable("Gremlin") is None
    assert len(movements(controller)) == 40


def test_second_escape_pass_also_stops_early_on_damage(monkeypatch):
    farmer, controller, _ = escape_harness(monkeypatch)

    def observe():
        named(farmer)
        return (.9 if len(movements(controller)) > 20 else 1.), 1., .6

    farmer.observe = observe
    assert farmer.escape_from_unreachable("Gremlin") == .6
    assert len(movements(controller)) == 21
    assert sum(c.args[0] == farmer.config.rest for c in controller.key.call_args_list) == 1


@pytest.mark.parametrize("sitting", [False, True])
def test_disabled_regen_never_toggles_posture_during_escape(monkeypatch, sitting):
    farmer, controller, _ = escape_harness(monkeypatch)
    farmer.config = farmer.config.model_copy(update={"regen_enabled": False})
    farmer.sitting = sitting
    assert farmer.escape_from_unreachable("Gremlin") is None
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert farmer.config.rest not in keys
    assert farmer.config.stand not in keys
    assert len(movements(controller)) == 40
    assert farmer.sitting is sitting


def test_stop_interrupts_escape_without_more_input(monkeypatch):
    farmer, controller, _ = escape_harness(monkeypatch)
    controller.key.side_effect = Stopped("STOP")
    with pytest.raises(Stopped):
        farmer.escape_from_unreachable("Gremlin")
    controller.key.assert_called_once_with("W", 1500)


def test_pause_during_movement_cancels_remaining_escape_steps(monkeypatch):
    farmer, controller, _ = escape_harness(monkeypatch)
    controller.pause_generation = 0
    key = controller.key.side_effect

    def pause(name, duration):
        key(name, duration)
        controller.pause_generation = 1

    controller.key.side_effect = pause
    assert farmer.escape_from_unreachable("Gremlin") is None
    controller.key.assert_called_once_with("W", 1500)


def test_escape_does_not_exceed_session_deadline(monkeypatch):
    farmer, controller, _ = escape_harness(monkeypatch)
    farmer.deadline = 1
    with pytest.raises(RuntimeError, match="Лимит времени"):
        farmer.escape_from_unreachable("Gremlin")
    controller.key.assert_not_called()


def test_combat_continues_with_escape_target_without_recursive_calls():
    farmer, _, _, _ = harness()

    def fight(target):
        farmer.escape_target = .7 if target is None else None

    farmer._fight = Mock(side_effect=fight)
    farmer.fight()
    assert [call.args for call in farmer._fight.call_args_list] == [(None,), (.7,)]
