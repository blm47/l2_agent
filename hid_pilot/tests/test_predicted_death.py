from collections import deque
from unittest.mock import Mock

import pytest

from hid_pilot.target import TargetObservation
from test_melee_farm import harness


def prediction(**values):
    farmer, controller, now, _ = harness(predict_target_death=True, **values)
    controller.pause_generation = 0
    farmer.attacking = True
    farmer.monster_identity = Mock(return_value=("Моб", 20))
    farmer.last_target = TargetObservation(at=2 / 3, epoch=1, kind="monster", hp=.1)
    farmer.switch_fast_target = Mock()
    farmer.observe = Mock(side_effect=AssertionError("Не должно быть подтверждающего кадра"))
    now[0] = 2 / 3
    samples = deque([(0., .6), (1 / 3, .3), (2 / 3, .1)])
    return farmer, controller, now, samples


@pytest.mark.parametrize("corpse", [None, "F8"])
def test_prediction_waits_half_second_and_switches_without_confirming(corpse):
    farmer, controller, now, samples = prediction(corpse_key=corpse, pickup_loot=False)
    assert farmer.try_predicted_death(samples, ("Моб", 20))
    assert now[0] == pytest.approx(2 / 3 + .5 + (.1 if corpse else 0))
    farmer.observe.assert_not_called()
    farmer.switch_fast_target.assert_called_once()
    assert farmer.training_kills == 0
    assert sum(farmer.mob_statistics.counts.values()) == 0
    assert [call.args[0] for call in controller.key.call_args_list] == ([corpse] if corpse else [])


@pytest.mark.parametrize("change", ["disabled", "player", "unknown", "changed", "slow", "stale", "short"])
def test_prediction_requires_current_monster_and_recent_damage(change):
    farmer, controller, now, samples = prediction(pickup_loot=False)
    if change == "disabled":
        farmer.config.predict_target_death = False
    elif change in {"player", "unknown"}:
        farmer.last_target = farmer.last_target.model_copy(update={"kind": change})
    elif change == "changed":
        farmer.monster_identity.return_value = ("Другой", 20)
    elif change == "slow":
        samples = deque([(0., .6), (1 / 3, .55), (2 / 3, .5)])
    elif change == "stale":
        now[0] += 2
    else:
        samples = deque([(0., .6), (.1, .3), (.2, .1)])
        now[0] = .2
    assert not farmer.try_predicted_death(samples, ("Моб", 20))
    controller.key.assert_not_called()
    farmer.switch_fast_target.assert_not_called()


def test_pause_cancels_pending_prediction():
    farmer, controller, now, samples = prediction(corpse_key="F8", pickup_loot=False)
    controller.wait.side_effect = lambda seconds: setattr(controller, "pause_generation", 1)
    assert not farmer.try_predicted_death(samples, ("Моб", 20))
    controller.key.assert_not_called()
    farmer.switch_fast_target.assert_not_called()


def test_fight_collects_damage_then_predicts_without_death_verification():
    farmer, controller, now, _ = harness(predict_target_death=True, pickup_loot=False)
    controller.pause_generation = 0
    farmer.monster_identity = Mock(return_value=("Моб", 20))
    farmer.switch_fast_target = Mock()
    farmer.verify_target = Mock(side_effect=AssertionError("Нет проверки смерти"))

    def observe():
        hp = max(.01, 1. - now[0] * .8)
        farmer.last_target = TargetObservation(at=now[0], epoch=1, kind="monster", hp=hp)
        return 1., 1., hp

    farmer.observe = observe
    farmer.fight(1.)
    farmer.switch_fast_target.assert_called_once()
    farmer.verify_target.assert_not_called()


def test_measured_damage_prevents_false_no_kills_watchdog():
    farmer, controller, now, samples = prediction(pickup_loot=False)
    now[0] += 200
    samples = deque((at + 200, hp) for at, hp in samples)
    farmer.kill_watch_at = 0
    farmer.kill_watch_attempts = 3
    assert farmer.try_predicted_death(samples, ("Моб", 20))
    assert not farmer.escape_if_no_kills()
    assert farmer.training_kills == 0
