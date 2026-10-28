from unittest.mock import Mock

import pytest
import numpy as np

from hid_pilot.bars import BarProfile
from hid_pilot.farm_melee import MeleeFarmer
from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from pydantic import ValidationError
from hid_pilot.target import TargetObservation, TargetReader
from test_duo import make_duo, start_ma_attack
from test_melee_farm import harness
from test_target_observation import profile, row


@pytest.mark.parametrize("maximum,blocked", [(9999, False), (10000, True), (10001, True)])
def test_target_max_hp_ten_times_own_blocks_all_offensive_keys(maximum, blocked):
    farmer, controller, _, _ = harness(
        aggro_key="2", damage_keys=("F7",), overhit_keys=("F8",), vampiric_key="F9")
    farmer.last_target = TargetObservation(at=0, kind="monster", hp=.1)
    farmer.observation_details = {"hp": "OCR 100/1000", "target_hp": f"OCR 1/{maximum}"}
    for key in (farmer.config.attack, "2", "F7", "F8", "F9"):
        assert farmer.combat_key(key, 100) is not blocked
    if blocked:
        assert [call.args[0] for call in controller.key.call_args_list] == ["ESC"]
        assert not farmer.alive_target(.1)


def test_duo_does_not_assist_mob_with_ten_times_own_max_hp():
    duo, controller = make_duo()
    start_ma_attack(duo, controller)
    duo.track_support_attack(.9)
    controller.events.clear()
    duo.last_target = TargetObservation(at=0, kind="monster", hp=.9)
    duo.observation_details = {"hp": "OCR 1000/1000", "target_hp": "OCR 9000/10000"}
    duo.support_pending = True
    duo.assist_support()
    assert controller.events == [(1, "ESC")]


@pytest.mark.parametrize("multiplier,maximum,blocked", [
    (5, 4999, False), (5, 5000, True), (5, 5001, True),
    (20, 10000, False), (20, 20000, True), (2.5, 2500, True),
])
def test_configured_hp_multiplier_uses_maxima_and_inclusive_boundary(multiplier, maximum, blocked):
    farmer, controller, _, _ = harness(target_max_hp_multiplier=multiplier)
    farmer.last_target = TargetObservation(at=0, kind="monster", hp=.1)
    farmer.observation_details = {"hp": "OCR 100/1000", "target_hp": f"OCR 1/{maximum}"}
    assert farmer.combat_key(farmer.config.attack, 100) is not blocked
    if blocked:
        assert controller.key.call_args.args[0] == "ESC"
        assert f"{multiplier:g} ×" in farmer.notify.call_args.args[0]


@pytest.mark.parametrize("own_current", [1, 100, 500, 1000])
def test_current_own_hp_does_not_lower_target_maximum_limit(own_current):
    farmer, controller, _, _ = harness(target_max_hp_multiplier=5)
    farmer.last_target = TargetObservation(at=0, kind="monster", name="Gremlin", hp=.1)
    farmer.observation_details = {"hp": f"OCR {own_current}/1000", "target_hp": "OCR 1/4999"}
    assert not farmer.target_hp_too_large()
    assert farmer.combat_key(farmer.config.attack, 100)
    assert all(call.args[0] != "ESC" for call in controller.key.call_args_list)


@pytest.mark.parametrize("name", ["Giggling Wind", None])
def test_hp_limit_rejection_logs_target_name_both_maxima_and_limit(caplog, name):
    farmer, controller, _, _ = harness(target_max_hp_multiplier=5)
    farmer.last_target = TargetObservation(at=0, kind="monster", name=name, hp=.1)
    farmer.observation_details = {"hp": "OCR 100/1000", "target_hp": "OCR 1/5000"}
    with caplog.at_level("INFO", logger="hid_pilot.farm"):
        assert farmer.reject_unsafe_target()
    message = farmer.notify.call_args.args[0]
    assert f"цель «{name or 'неизвестно'}»" in message
    assert "максимальное HP МА 1000" in message
    assert "максимальное HP цели 5000" in message
    assert "порог 5000" in message
    assert "5 ×" in message
    assert message in caplog.text
    controller.key.assert_called_once_with("ESC", 80)
    assert farmer.last_target is None


def test_duo_hp_multiplier_roundtrips_and_blocks_support_assist():
    config = DuoConfig(target_max_hp_multiplier=5)
    assert parse_duo_settings(format_duo_settings(config)) == config
    duo, controller = make_duo(target_max_hp_multiplier=5)
    start_ma_attack(duo, controller)
    duo.track_support_attack(.9)
    controller.events.clear()
    duo.last_target = TargetObservation(at=0, kind="monster", hp=.9)
    duo.observation_details = {"hp": "OCR 1000/1000", "target_hp": "OCR 4500/5000"}
    duo.assist_support()
    assert controller.events == [(1, "ESC")]


@pytest.mark.parametrize("multiplier", [0, -1, float("inf"), float("nan")])
def test_hp_multiplier_rejects_nonpositive_or_nonfinite_values(multiplier):
    with pytest.raises(ValidationError):
        DuoConfig(target_max_hp_multiplier=multiplier)


@pytest.mark.parametrize("own,target", [("полоса", "OCR 1/10000"),
                                        ("OCR 1/1000", "полоса"), ("", "")])
def test_hp_ratio_is_not_invented_from_percentages(own, target):
    farmer, _, _, _ = harness()
    farmer.observation_details = {"hp": own, "target_hp": target}
    assert not farmer.target_hp_too_large()


@pytest.mark.parametrize("kind", ["player", "npc"])
@pytest.mark.parametrize("hp", [None, .8])
def test_next_target_discards_player_or_npc_then_finds_monster(monkeypatch, kind, hp):
    farmer, controller, _, state = harness(aggro_key="2")
    original = controller.key.side_effect
    selections = [0]
    monkeypatch.setattr("hid_pilot.farm.Farmer.observe", lambda self: tuple(state))
    farmer.observe = lambda: MeleeFarmer.observe(farmer)

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name == farmer.config.next_target:
            selections[0] += 1
            state[2] = hp if selections[0] == 1 else .9
            farmer.last_target = TargetObservation(
                at=farmer.clock(), kind=kind if selections[0] == 1 else "monster", hp=state[2])
        elif name == "ESC":
            state[2] = None

    controller.key.side_effect = key
    assert farmer.acquire() == .9
    assert [call.args[0] for call in controller.key.call_args_list] == [
        farmer.config.next_target, "ESC", farmer.config.next_target]
    assert not farmer.unsafe_target_rejected


@pytest.mark.parametrize("kind", ["player", "npc", "unknown"])
def test_direct_fight_and_skills_never_attack_player_panel(kind):
    farmer, controller, _, _ = harness(
        aggro_key="2", damage_keys=("F7",), overhit_keys=("F8",), vampiric_key="F9")
    farmer.last_target = TargetObservation(at=0, kind=kind, player_class="Hawkeye", clan="PvP")
    farmer.fight(.9)
    farmer.open_with_aggro()
    assert not farmer.use_damage_skill(.1, None)
    assert not farmer.use_vampiric(.1, .9)
    assert not farmer.combat_key(farmer.config.attack, 100)
    controller.key.assert_called_once_with("ESC", 80)
    assert not farmer.training_kills
    assert not farmer.mob_statistics.counts


@pytest.mark.parametrize("hp", [None, 0.])
def test_missing_or_zero_hp_blocks_offensive_input_with_real_cv_profile(hp):
    farmer, controller, _, _ = harness(aggro_key="2")
    farmer.profile = BarProfile(width=100, height=100)
    farmer.last_target = TargetObservation(at=0, hp=hp)
    for key in (farmer.config.attack, farmer.config.aggro_key):
        assert not farmer.combat_key(key, 100)
    controller.key.assert_not_called()
    farmer.last_target = TargetObservation(at=1, hp=.8)
    assert farmer.combat_key(farmer.config.attack, 100)
    controller.key.assert_called_once_with(farmer.config.attack, 100)


def test_player_selected_during_fight_is_not_counted_as_dead_monster():
    farmer, controller, _, state = harness()
    original = controller.key.side_effect

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name == farmer.config.attack:
            state[2] = None
            farmer.last_target = TargetObservation(at=farmer.clock(), kind="player", player_class="Hawkeye")

    controller.key.side_effect = key
    farmer.fight(.9)
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.attack, "ESC"]
    assert not farmer.training_kills
    assert not farmer.mob_statistics.counts


def test_duo_does_not_assist_rejected_player():
    duo, controller = make_duo(aggro_key="2", vampiric_key="F8")
    duo.support_pending = True
    duo.last_target = TargetObservation(at=0, kind="player", player_class="Hawkeye")
    assert not duo.combat_key(duo.config.attack, 100)
    duo.open_with_aggro()
    assert not duo.use_vampiric(.1, .9)
    assert controller.events == [(1, "ESC")]


def test_duo_never_retaliates_against_player_even_when_ma_takes_damage():
    duo, controller = make_duo(aggro_key="2", heal_key=None, support_self_heal_key=None)
    duo.last_resources = (1., 1.)
    duo.resource_generation = controller.pause_generation

    def capture():
        duo.last_target = TargetObservation(at=controller.now, kind="player", player_class="Hawkeye", clan="PvP")
        return (.4, None, False)

    controller.capture = capture
    duo.observe()
    keys = [event[1] for event in controller.events if isinstance(event[0], int)]
    assert "ESC" in keys
    assert set(keys) <= {"ESC", duo.config.next_target}


def test_player_class_and_clan_override_configured_monster_name_without_hp():
    config = profile()
    config.target_kind_names = {"Gremlin": "monster"}
    ocr = Mock()
    ocr.detect.return_value = [row("Gremlin", 5), row("Hawkeye", 25), row("PvP Clan", 45)]
    target = TargetReader(config).read(np.zeros((126, 423, 3), dtype=np.uint8), 1., ocr)
    assert target.kind == "player" and target.hp is None
    assert target.player_class == "Hawkeye" and target.clan == "PvP Clan"
