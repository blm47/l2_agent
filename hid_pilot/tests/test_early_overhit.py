from collections import deque
from unittest.mock import Mock

from test_melee_farm import harness


def setup(monkeypatch, roll):
    farmer, controller, _, _ = harness(overhit_keys="F3", damage_keys="F1", overhit_early_chance=.5)
    farmer.training_kills = 3
    farmer.damage_samples[("Gremlin", 1)] = deque([.2])
    random = Mock(return_value=roll)
    monkeypatch.setattr("hid_pilot.farm_melee.random.random", random)
    return farmer, controller, random


def test_successful_coin_press_overhit_one_hit_early_for_seven_hundred_ms(monkeypatch):
    farmer, controller, random = setup(monkeypatch, .49)
    assert farmer.use_damage_skill(.4, ("Gremlin", 1))
    controller.key.assert_called_once_with("F3", 700)
    random.assert_called_once_with()


def test_failed_coin_not_repeated_each_frame_and_normal_finish_still_fires(monkeypatch):
    farmer, controller, random = setup(monkeypatch, .5)
    for hp in (.42, .41, .40, .39):
        assert not farmer.use_damage_skill(hp, ("Gremlin", 1))
    random.assert_called_once_with()
    controller.key.assert_not_called()
    assert farmer.use_damage_skill(.2, ("Gremlin", 1))
    controller.key.assert_called_once_with("F3", 700)


def test_outside_one_hit_lookahead_uses_regular_damage_without_coin(monkeypatch):
    farmer, controller, random = setup(monkeypatch, .1)
    assert farmer.use_damage_skill(.6, ("Gremlin", 1))
    controller.key.assert_called_once_with("F1", 100)
    random.assert_not_called()


def test_coin_waits_for_available_skill(monkeypatch):
    farmer, controller, random = setup(monkeypatch, .1)
    farmer.skill_ready["F3"] = 10.
    assert not farmer.use_damage_skill(.4, ("Gremlin", 1))
    random.assert_not_called()
    controller.key.assert_not_called()
