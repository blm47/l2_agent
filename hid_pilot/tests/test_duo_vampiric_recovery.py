import pytest

from hid_pilot.duo import SupportDied
from hid_pilot.target import TargetObservation
from test_duo import start_ma_attack
from test_duo_self_heal import observed_duo


def recovering_support(monkeypatch):
    duo, controller, reader = observed_duo(
        monkeypatch, .52, heal_key=None, support_self_heal_key=None,
        support_vampiric_key="F2", support_vampiric_hp_threshold=.7,
        support_vampiric_cast_seconds=1.3, support_vampiric_cooldown=1.5)
    start_ma_attack(duo, controller)
    duo.support_pending = True
    return duo, controller, reader


def test_replay_regression_retries_without_healing_even_below_thirty_percent(monkeypatch):
    duo, controller, _ = recovering_support(monkeypatch)
    target_hp = [.60]
    controller.capture = lambda: (1., target_hp[0], False)
    # Без прироста HP нельзя считать прерванные касты успешным восстановлением.
    targets = [.60, .41, .306, .245, .177, .104, .052, .007]
    for attempts, hp in enumerate(targets, 1):
        target_hp[0] = hp
        if attempts > 1:
            controller.now = duo.support_vampiric_ready
        duo.observe()
        assert controller.events.count((2, "F2")) == attempts
        # Ассист нужен только внутри подготовки вампирика, отдельных визитов между кастами нет.
        assert controller.events.count((2, duo.config.support_key)) == attempts
        assert duo.support_vampiric_recovering
        assert duo.support_vampiric_attempts == attempts
    assert (1, duo.config.next_target) not in controller.events
    assert (1, "ESC") not in controller.events
    assert any("порог 70% не достигнут" in call.args[0]
               for call in duo.notify.call_args_list)


def test_recovery_keeps_cooldown_and_resumes_assist_only_after_hp_recovers(monkeypatch):
    duo, controller, reader = recovering_support(monkeypatch)
    controller.capture = lambda: (1., .24, False)
    duo.observe()
    ready = duo.support_vampiric_ready
    controller.now = ready - .01
    duo.observe()
    assert controller.events.count((2, "F2")) == 1
    assert controller.events.count((2, duo.config.support_key)) == 1
    assert duo.support_pending
    # Даже прирост HP недостаточен, пока не достигнут заданный порог восстановления.
    reader.return_value = .64
    duo.observe()
    assert duo.support_vampiric_recovering
    assert controller.events.count((2, duo.config.support_key)) == 1
    reader.return_value = .7
    duo.observe()
    assert not duo.support_vampiric_recovering
    assert controller.events.count((2, "F2")) == 1
    assert controller.events.count((2, duo.config.support_key)) == 2


def test_recovery_explicitly_requeues_assist_and_restarts_bounded_retry(monkeypatch):
    duo, controller, reader = recovering_support(monkeypatch)
    controller.capture = lambda: (1., .24, False)
    duo.observe()
    # Очередь могла сброситься при завершении предыдущего боя; старый лимит уже исчерпан.
    duo.support_pending = False
    duo.support_assist_attempts = 2
    duo.support_assist_retry_at = float("inf")
    duo.support_rescue_until = controller.now + 10
    reader.return_value = .7
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 2
    assert not duo.support_pending
    assert duo.support_assist_attempts == 1
    controller.wait(duo.config.support_assist_retry_seconds)
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 3
    controller.wait(10)
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 3


@pytest.mark.parametrize("attacking,target_hp", [(True, None), (True, 0.), (False, .24)])
def test_recovered_support_waits_for_living_target_and_active_fight(monkeypatch, attacking, target_hp):
    duo, controller, reader = recovering_support(monkeypatch)
    target = [.24]
    controller.capture = lambda: (1., target[0], False)
    duo.observe()
    target[0] = target_hp
    duo.attacking = attacking
    reader.return_value = .7
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1
    assert duo.support_pending
    target[0] = .23
    start_ma_attack(duo, controller)
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 2


@pytest.mark.parametrize("target_hp", [None, 0.])
def test_recovery_does_not_cast_on_missing_or_dead_target(monkeypatch, target_hp):
    duo, controller, _ = recovering_support(monkeypatch)
    target = [.24]
    controller.capture = lambda: (1., target[0], False)
    duo.observe()
    target[0] = target_hp
    controller.now = duo.support_vampiric_ready
    duo.observe()
    assert controller.events.count((2, "F2")) == 1
    assert (1, duo.config.next_target) not in controller.events


@pytest.mark.parametrize("kind,dead", [("player", False), ("npc", False), ("monster", True)])
def test_recovery_preserves_target_safety(monkeypatch, kind, dead):
    duo, controller, _ = recovering_support(monkeypatch)
    controller.capture = lambda: (1., .24, False)
    duo.observe()
    controller.now = duo.support_vampiric_ready
    duo.last_target = TargetObservation(at=controller.now, kind=kind, hp=.01, dead=dead,
                                       death_marker=dead, confidence=.99)
    duo.observe()
    assert controller.events.count((2, "F2")) == 1


@pytest.mark.parametrize("support_hp", [None, 0.])
def test_unknown_or_dead_support_does_not_allow_another_recovery_cast(monkeypatch, support_hp):
    duo, controller, reader = recovering_support(monkeypatch)
    controller.capture = lambda: (1., .24, False)
    duo.observe()
    reader.return_value = support_hp
    controller.now = duo.support_vampiric_ready
    if support_hp == 0:
        with pytest.raises(SupportDied):
            duo.observe()
        assert duo.support_lost
    else:
        duo.observe()
    assert controller.events.count((2, "F2")) == 1
    assert not duo.support_vampiric_recovering
