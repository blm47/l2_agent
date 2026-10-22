from unittest.mock import Mock

import numpy as np
import pytest
from test_melee_farm import harness

from hid_pilot.numbers import NumbersReader


def test_overhit_at_two_percent_needs_neither_name_nor_training():
    farmer, controller, _, _ = harness(overhit_keys="F3")
    assert farmer.training_kills == 0
    assert farmer.use_damage_skill(.02, None)
    controller.key.assert_called_once_with("F3", 700)


def test_low_hp_opens_with_overhit_without_ordinary_attack():
    farmer, controller, _, state = harness(overhit_keys="F3", pickup_loot=False)
    state[2] = 0.
    farmer.fight(.01)
    assert [call.args[0] for call in controller.key.call_args_list] == ["F3"]


def test_anonymous_hp_diffs_produce_mean_and_adaptive_overhit():
    farmer, controller, _, _ = harness(overhit_keys="F3", pickup_loot=False)
    farmer.training_kills = 3
    values = iter([.8, .6, .4, .21, 0., 0.])
    farmer.observe = lambda: (1., 1., next(values))
    farmer.fight(1.)
    assert farmer.estimated_damage(None) == pytest.approx((.2 + .2 + .2 + .19) / 4)
    assert [call.args[0] for call in controller.key.call_args_list] == ["R", "F3"]


def test_anonymous_estimate_does_not_carry_to_next_monster():
    farmer, _, _, state = harness(pickup_loot=False)
    farmer.current_damage_samples.extend([.2, .2])
    state[2] = 0.
    farmer.fight(1.)
    assert farmer.estimated_damage(None) is None


def test_small_damage_diffs_are_kept():
    farmer, _, _, _ = harness(pickup_loot=False)
    values = iter([.999, .998, 0., 0.])
    farmer.observe = lambda: (1., 1., next(values))
    farmer.fight(1.)
    assert farmer.estimated_damage(None) == pytest.approx(.001)


def test_name_line_preprocessing_uses_recognition_without_detection():
    reader = NumbersReader()
    reader.engine = Mock(side_effect=[([["Gremiin", .91]], None), ([["Gremlin", .99]], None)])
    assert reader.read_text(np.full((20, 120, 3), 200, dtype=np.uint8)) == ("Gremlin", .99)
    assert reader.engine.call_count == 2
    assert all(call.kwargs["use_det"] is False for call in reader.engine.call_args_list)
