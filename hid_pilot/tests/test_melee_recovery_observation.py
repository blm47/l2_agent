from unittest.mock import Mock

import pytest

from test_melee_farm import harness


def test_own_hp_mp_can_be_read_on_thirtieth_attempt():
    farmer, controller, _, _ = harness(observation_movement=False)
    del farmer.observe
    farmer._observe = Mock(side_effect=[(None, None, None)] * 29 + [(.8, .6, None)])
    farmer.report = Mock()
    assert farmer.observe() == (.8, .6, None)
    assert farmer._observe.call_count == 30
    assert "29/30" in farmer.report.call_args_list[-1].args[0]
    controller.key.assert_not_called()


def test_own_hp_mp_stops_only_after_thirty_failed_attempts():
    farmer, _, _, _ = harness(observation_movement=False)
    del farmer.observe
    farmer._observe = Mock(return_value=(None, None, None))
    farmer.report = Mock()
    with pytest.raises(RuntimeError, match="HP/MP не распознаны"):
        farmer.observe()
    assert farmer._observe.call_count == 30
    assert "30/30" in farmer.report.call_args_list[-1].args[0]


def recovering(monkeypatch):
    farmer, controller, now, state = harness(hp_rest_threshold=.55)
    state[:] = [.5, .8, 1.]
    farmer.under_attack = True
    farmer.quiet_since = 0
    farmer.report = Mock()
    wait = controller.wait.side_effect

    def recover_hp(seconds):
        wait(seconds)
        if farmer.sitting:
            state[0] = .9

    controller.wait.side_effect = recover_hp
    return farmer, controller, now, state


def test_stale_under_attack_flag_does_not_send_half_health_character_to_new_fight(monkeypatch):
    farmer, controller, _, _ = recovering(monkeypatch)
    farmer.fight = Mock()
    farmer.recover()
    farmer.fight.assert_not_called()
    assert [call.args[0] for call in controller.key.call_args_list] == ["=", "-"]
    messages = [call.args[0] for call in farmer.report.call_args_list]
    assert any("HP 50.0%, MP 80.0%" in text and "55%" in text for text in messages)
    assert any("Восстановление завершено: HP 90.0%" in text for text in messages)


def test_damage_during_finished_fight_does_not_chain_another_aggressor_search(monkeypatch):
    farmer, _, now, _ = recovering(monkeypatch)
    farmer.last_resources = (.51, None)

    def fight():
        now[0] += 1
        farmer.under_attack = True

    farmer.fight = Mock(side_effect=fight)
    farmer.recover()
    farmer.fight.assert_called_once_with()
    messages = [call.args[0] for call in farmer.report.call_args_list]
    assert any("HP 51.0% → 50.0%" in text and "MP 80.0%" in text for text in messages)
    assert any("отдых; HP 50%" in text for text in messages)


def test_new_cp_damage_still_interrupts_rest_with_unchanged_hp(monkeypatch):
    farmer, _, now, _ = recovering(monkeypatch)
    farmer.last_resources = (.5, 1.)
    farmer.own_cp = .9
    farmer.fight = Mock(side_effect=lambda: now.__setitem__(0, now[0] + 1))
    farmer.recover()
    farmer.fight.assert_called_once()
    assert any("CP 100.0% → 90.0%" in call.args[0] for call in farmer.report.call_args_list)


def test_hp_change_across_long_observation_gap_does_not_prove_new_aggro(monkeypatch):
    farmer, _, now, _ = recovering(monkeypatch)
    farmer.last_resources = (.6, 1.)
    farmer.last_observation_at = 0
    now[0] = 5
    farmer.fight = Mock()
    farmer.recover()
    farmer.fight.assert_not_called()


def test_rest_status_counts_seconds_after_sitting_and_updates_resources():
    farmer, controller, now, state = harness()
    state[:] = [.94, .08, None]
    farmer.report = Mock()
    wait = controller.wait.side_effect
    started = []

    def recover_mana(seconds):
        if farmer.sitting and not started:
            started.append(now[0])
        wait(seconds)
        if started:
            state[1] = .09 if now[0] - started[0] < 2.4 else 1.

    controller.wait.side_effect = recover_mana
    farmer.recover()
    messages = [call.args[0] for call in farmer.report.call_args_list]
    rest_messages = [text for text in messages if "с начала отдыха" in text]
    assert len(rest_messages) == 3
    assert "HP 94%, MP 8%; с начала отдыха 0 с" in rest_messages[0]
    assert "MP 9%; с начала отдыха 1 с" in rest_messages[1]
    assert "с начала отдыха 2 с" in rest_messages[2]
    assert started[0] >= farmer.config.quiet_seconds
