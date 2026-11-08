import numpy as np
import pytest
from unittest.mock import Mock

from hid_pilot.bars import BarBox
from hid_pilot.target import TargetObservation, TargetReader
from test_duo import make_duo, start_ma_attack
from test_melee_farm import harness
from test_target_observation import profile, row


@pytest.mark.parametrize("cp", [None, 1., .5])
@pytest.mark.parametrize("kind", ["unknown", "monster"])
def test_selected_target_without_hp_blocks_attack_even_under_damage(cp, kind):
    farmer, controller, _, _ = harness(aggro_key="2", damage_keys=("F7",))
    farmer.last_target = TargetObservation(at=0, kind=kind, name="Possible player", hp=None)
    farmer.own_cp = cp
    farmer.under_attack = True
    assert not farmer.combat_key(farmer.config.attack, 100)
    assert not farmer.combat_key("2", 100)
    assert not farmer.use_damage_skill(.9, None)
    controller.key.assert_called_once_with("ESC", 80)
    assert "PvP" in farmer.notify.call_args.args[0]
    assert ("CP 50.0%" in farmer.notify.call_args.args[0]) == (cp == .5)


@pytest.mark.parametrize("cp", [1., .5])
def test_duo_blocks_assist_without_hp_and_reports_fresh_support_cp(cp):
    duo, controller = make_duo()
    start_ma_attack(duo, controller)
    controller.events.clear()
    duo.last_target = TargetObservation(at=controller.now, name="Possible player", hp=None)
    duo.own_cp = 1.
    duo.previous_resources[f"party_{duo.config.support_party_slot}_cp"] = cp
    duo.resources_at = duo.clock()
    duo.resource_generation = controller.pause_generation
    duo.support_pending = True
    duo.assist_support()
    assert controller.events == [(1, "ESC")]
    assert ("саппорт CP 50.0%" in duo.notify.call_args.args[0]) == (cp == .5)


def test_visible_hp_strip_with_failed_numeric_ocr_is_not_missing_hp_bar():
    farmer, _, _, _ = harness()
    farmer.last_target = TargetObservation(at=0, name="Monster", hp_bar_present=True)
    assert not farmer.target_without_hp_bar()


def test_empty_target_does_not_claim_pvp():
    farmer, _, _, _ = harness()
    farmer.last_target = TargetObservation(at=0)
    assert not farmer.target_without_hp_bar()


def test_missing_bar_blocks_even_with_inconsistent_numeric_hp():
    farmer, controller, _, _ = harness()
    farmer.last_target = TargetObservation(at=0, name="Possible player", hp=.9,
                                           hp_bar_present=False)
    assert not farmer.combat_key(farmer.config.attack, 100)
    controller.key.assert_called_once_with("ESC", 80)


def test_reader_preserves_missing_hp_bar_evidence_in_cache():
    config = profile()
    config.target_hp_presence_box = BarBox(x1=.5, x2=.75, y1=.55, y2=.75)
    ocr = Mock()
    ocr.detect.return_value = [row("Possible player", 5)]
    image = np.zeros((126, 423, 3), dtype=np.uint8)
    reader = TargetReader(config)
    assert reader.read(image, 1., ocr).hp_bar_present is False
    assert reader.read(image, 2., ocr).hp_bar_present is False
