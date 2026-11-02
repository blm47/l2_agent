import logging

import pytest

from hid_pilot.bars import BarProfile
from hid_pilot.farm_melee import MeleeFarmer, TARGET_VALIDATION_READS
from hid_pilot.target import TargetObservation
from test_melee_farm import harness


def validation_farmer(monkeypatch):
    farmer, controller, now, _ = harness(aggro_key="2", damage_keys=("F7",),
                                        overhit_keys=("F8",), vampiric_key="F9")
    farmer.profile = BarProfile(width=100, height=100)
    controller.pause_generation = 0
    monkeypatch.setattr("hid_pilot.farm_melee.time.perf_counter", lambda: now[0])
    farmer.press(farmer.config.next_target)
    farmer.last_target = TargetObservation(at=now[0], kind="monster", hp=.9)
    return farmer, controller, now


def set_maxima(farmer, maximum=2000):
    farmer.observation_details = {"hp": "OCR 1000/1000", "target_hp": f"OCR 100/{maximum}"}


def test_known_maximum_allows_immediate_attack_and_logs_selection_latency(monkeypatch, caplog):
    farmer, controller, now = validation_farmer(monkeypatch)
    now[0] += .025
    set_maxima(farmer)
    with caplog.at_level(logging.INFO, logger="hid_pilot.farm"):
        assert farmer.combat_key(farmer.config.attack, 100)
        assert farmer.combat_key(farmer.config.attack, 100)
    controller.wait.assert_not_called()
    records = [record for record in caplog.records if hasattr(record, "target_selection_ms")]
    assert len(records) == 1
    assert records[0].target_selection_ms == pytest.approx(125)
    assert records[0].target_validation_reads == 0
    assert records[0].target_validation_result == "разрешена"


@pytest.mark.parametrize("key", ["R", "2", "F7", "F8", "F9"])
def test_unknown_maximum_never_sends_offensive_key_before_boss_rejection(monkeypatch, caplog, key):
    farmer, controller, _ = validation_farmer(monkeypatch)
    farmer.observation_details = {"hp": "OCR 1000/1000", "target_hp": "полоса"}

    def observe(self):
        assert not self.combat_key(key, 100)
        set_maxima(self, 10000)
        return 1., 1., .9

    monkeypatch.setattr(MeleeFarmer, "observe", observe)
    with caplog.at_level(logging.INFO, logger="hid_pilot.farm"):
        assert not farmer.combat_key(key, 100)
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target, "ESC"]
    records = [record for record in caplog.records if hasattr(record, "target_selection_ms")]
    assert len(records) == 1 and records[0].target_validation_result == "запрещена"


def test_retry_attacks_only_after_valid_numbers_arrive(monkeypatch, caplog):
    farmer, controller, _ = validation_farmer(monkeypatch)
    attempts = []

    def observe(self):
        attempts.append(1)
        if len(attempts) == 2:
            set_maxima(self)
        return 1., 1., .9

    monkeypatch.setattr(MeleeFarmer, "observe", observe)
    with caplog.at_level(logging.INFO, logger="hid_pilot.farm"):
        assert farmer.combat_key(farmer.config.attack, 100)
    assert len(attempts) == 2
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target, farmer.config.attack]
    assert [call.args[0] for call in controller.wait.call_args_list] == [.01, .01]
    record = next(record for record in caplog.records if hasattr(record, "target_selection_ms"))
    assert record.target_selection_ms == pytest.approx(120)
    assert record.target_validation_reads == 2


def test_unknown_maximum_has_bounded_retries_and_skips_target(monkeypatch, caplog):
    farmer, controller, _ = validation_farmer(monkeypatch)
    monkeypatch.setattr(MeleeFarmer, "observe", lambda self: (1., 1., .9))
    with caplog.at_level(logging.INFO, logger="hid_pilot.farm"):
        assert not farmer.combat_key(farmer.config.attack, 100)
    assert controller.wait.call_count == TARGET_VALIDATION_READS
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target, "ESC"]
    assert farmer.unsafe_target_rejected
    assert any(getattr(record, "target_validation_result", "").startswith("пропуск") for record in caplog.records)


def test_pause_during_validation_cancels_attack(monkeypatch):
    farmer, controller, _ = validation_farmer(monkeypatch)

    def observe(self):
        controller.pause_generation += 1
        set_maxima(self)
        return 1., 1., .9

    monkeypatch.setattr(MeleeFarmer, "observe", observe)
    assert not farmer.combat_key(farmer.config.attack, 100)
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target]


def test_next_target_invalidates_previous_permission(monkeypatch):
    farmer, controller, _ = validation_farmer(monkeypatch)
    set_maxima(farmer)
    assert farmer.combat_key(farmer.config.attack, 100)
    farmer.press(farmer.config.next_target)
    farmer.observation_details.clear()
    monkeypatch.setattr(MeleeFarmer, "observe", lambda self: (1., 1., .9))
    assert not farmer.combat_key(farmer.config.attack, 100)
    assert [call.args[0] for call in controller.key.call_args_list].count(farmer.config.attack) == 1


def test_validation_budget_stops_retries_after_slow_ocr(monkeypatch):
    farmer, controller, now = validation_farmer(monkeypatch)
    reads = []

    def observe(self):
        reads.append(1)
        now[0] += .6
        return 1., 1., .9

    monkeypatch.setattr(MeleeFarmer, "observe", observe)
    assert not farmer.combat_key(farmer.config.attack, 100)
    assert len(reads) == 1
    assert controller.key.call_args.args[0] == "ESC"


def test_pause_invalidates_successful_validation(monkeypatch):
    farmer, controller, _ = validation_farmer(monkeypatch)
    set_maxima(farmer)
    assert farmer.combat_key(farmer.config.attack, 100)
    controller.pause_generation += 1
    farmer.observation_details.clear()
    monkeypatch.setattr(MeleeFarmer, "observe", lambda self: (1., 1., .9))
    assert not farmer.combat_key(farmer.config.attack, 100)


def test_fast_switch_does_not_attack_boss_after_initial_bar_only_reading(monkeypatch):
    farmer, controller, _ = validation_farmer(monkeypatch)
    controller.key.reset_mock()
    reads = []

    def observe(self):
        reads.append(1)
        self.last_target = TargetObservation(at=self.clock(), kind="monster", hp=.9)
        if len(reads) > 1:
            set_maxima(self, 10000)
        return 1., 1., .9

    monkeypatch.setattr(MeleeFarmer, "observe", observe)
    farmer.switch_fast_target()
    assert not farmer.fast_target_ready
    assert [call.args[0] for call in controller.key.call_args_list] == [farmer.config.next_target, "ESC"]


def test_support_vampiric_assist_is_blocked_until_target_validated(monkeypatch):
    from test_duo import make_duo

    duo, controller = make_duo(support_vampiric_key="F7")
    duo.profile = BarProfile(width=100, height=100)
    duo.last_target = TargetObservation(at=0, kind="monster", hp=.9)
    monkeypatch.setattr(MeleeFarmer, "observe", lambda self: (1., 1., .9))
    assert not duo.visit_vampiric()
    assert controller.events == [(1, "ESC")]


def test_named_selection_timing_includes_target_command_and_existing_wait(monkeypatch, caplog):
    farmer, controller, now = validation_farmer(monkeypatch)
    farmer.known_mobs = {"Gremlin": 1}

    def select(name):
        now[0] += .2
        farmer.last_target = TargetObservation(at=now[0], kind="monster", hp=.9)
        set_maxima(farmer)

    controller.target_by_name.side_effect = select
    assert farmer.search_known_target() == 1.
    with caplog.at_level(logging.INFO, logger="hid_pilot.farm"):
        assert farmer.combat_key(farmer.config.attack, 100)
    record = next(record for record in caplog.records if hasattr(record, "target_selection_ms"))
    assert record.target_selection_ms == pytest.approx(900)
    assert "источник=/target" in record.getMessage()
