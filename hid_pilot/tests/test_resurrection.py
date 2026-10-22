from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.resurrection import OfferChanged, ResurrectionOffer, find_offer, wait_for_resurrection


def row(text, x, y, width=80, score=.99):
    return ([[x, y], [x + width, y], [x + width, y + 14], [x, y + 14]], text, score)


def rows():
    return [row("Lucano is making an attempt at resurrection.", 120, 192, 250),
            row("Do you want to continue with this", 120, 208, 240),
            row("resurrection?", 120, 224, 90), row("Accept", 160, 298, 50),
            row("Decline", 250, 298, 50), row("To Village", 190, 80)]


def setup_ocr(monkeypatch, results):
    import threading
    reader = Mock()
    reader.lock = threading.RLock()
    reader.engine.return_value = (results, None)
    monkeypatch.setattr("hid_pilot.resurrection.shared_numbers", lambda: reader)
    return reader


def test_offer_locates_accept_from_ocr_model(monkeypatch):
    setup_ocr(monkeypatch, rows())
    offer = find_offer(np.zeros((432, 592, 3), np.uint8))
    assert offer.button == (160, 298, 210, 312)


@pytest.mark.parametrize("change", ["accept_only", "no_prompt", "no_decline", "low_score", "far_prompt", "ambiguous"])
def test_unrelated_or_uncertain_accept_never_authorizes_click(monkeypatch, change):
    data = rows()
    if change == "accept_only":
        data = data[3:4]
    elif change == "no_prompt":
        data = data[3:]
    elif change == "no_decline":
        data.pop(4)
    elif change == "low_score":
        data[3] = row("Accept", 160, 298, 50, .7)
    elif change == "far_prompt":
        data[0] = row("resurrection", 400, 20)
        data[2] = row("resurrection", 400, 40)
    else:
        data.append(row("Accept", 164, 298, 50))
    setup_ocr(monkeypatch, data)
    assert find_offer(np.zeros((432, 592, 3), np.uint8)) is None


def waiting_harness(monkeypatch, health):
    now = [0.]
    controller, profile, notify = Mock(), Mock(), Mock()
    controller.pause_generation = 0
    controller.capture.return_value = np.zeros((432, 592, 3), np.uint8)
    controller.desktop.capture.side_effect = lambda: controller.capture.return_value
    controller.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
    profile.read.side_effect = health
    offer = ResurrectionOffer(button=(160, 298, 210, 312), message=(120, 192, 370, 206))
    detector = Mock(return_value=offer)
    monkeypatch.setattr("hid_pilot.resurrection.find_offer", detector)
    return controller, profile, notify, now, detector


def test_click_once_then_wait_for_positive_hp_even_if_mp_unknown(monkeypatch):
    controller, profile, notify, now, _ = waiting_harness(monkeypatch, [0, None, 0, .3])
    controller.click.side_effect = lambda *args, **kwargs: kwargs["verify"]()
    wait_for_resurrection(controller, profile, 10, notify, lambda: now[0])
    controller.click.assert_called_once()
    button, x, y = controller.click.call_args.args
    assert button == "left" and x == pytest.approx(185 / 592) and y == pytest.approx(305 / 432)
    controller.key.assert_not_called()
    notify.death.assert_called_once_with()
    notify.revived.assert_called_once_with()


def test_no_dialog_no_input_and_bounded_wait(monkeypatch):
    controller, profile, notify, now, detector = waiting_harness(monkeypatch, [0] * 10)
    detector.return_value = None
    with pytest.raises(RuntimeError, match="время сценария"):
        wait_for_resurrection(controller, profile, 2, notify, lambda: now[0])
    controller.click.assert_not_called()
    controller.key.assert_not_called()


@pytest.mark.parametrize("change", ["dialog", "resize", "pause", "deadline", "focus"])
def test_changes_during_mouse_approach_cancel_click(monkeypatch, change):
    controller, profile, notify, now, _ = waiting_harness(monkeypatch, [0, .4])
    def click(*args, **kwargs):
        if change == "dialog":
            controller.capture.return_value[:] = 255
        elif change == "resize":
            controller.capture.return_value = np.zeros((400, 500, 3), np.uint8)
        elif change == "pause":
            controller.pause_generation += 1
        elif change == "focus":
            from hid_pilot.desktop import FocusLost
            controller.desktop.capture.side_effect = FocusLost("Автопауза")
        else:
            now[0] = 10
        with pytest.raises(OfferChanged):
            kwargs["verify"]()
        raise OfferChanged()
    controller.click.side_effect = click
    if change == "deadline":
        with pytest.raises(RuntimeError):
            wait_for_resurrection(controller, profile, 5, notify, lambda: now[0])
    else:
        wait_for_resurrection(controller, profile, 5, notify, lambda: now[0])


def test_hard_stop_propagates_without_input(monkeypatch):
    controller, profile, notify, now, _ = waiting_harness(monkeypatch, [0])
    controller.checkpoint.side_effect = RuntimeError("hard-stop")
    with pytest.raises(RuntimeError, match="hard-stop"):
        wait_for_resurrection(controller, profile, 10, notify, lambda: now[0])
    controller.click.assert_not_called()


def test_ocr_result_after_pause_is_discarded(monkeypatch):
    controller, profile, notify, now, detector = waiting_harness(monkeypatch, [0, .4])
    offer = detector.return_value
    def detect(image):
        controller.pause_generation += 1
        return offer
    detector.side_effect = detect
    wait_for_resurrection(controller, profile, 5, notify, lambda: now[0])
    controller.click.assert_not_called()


def test_brief_ocr_loss_does_not_repeat_click(monkeypatch):
    controller, profile, notify, now, detector = waiting_harness(monkeypatch, [0, 0, 0, .4])
    offer = detector.return_value
    detector.side_effect = [offer, None, offer]
    wait_for_resurrection(controller, profile, 5, notify, lambda: now[0])
    controller.click.assert_called_once()


def test_healer_recovery_preserves_cooldowns_and_clears_predeath_state(monkeypatch, tmp_path):
    from test_healer_runtime import harness
    healer, controller, _, _, now = harness(monkeypatch, tmp_path)
    monkeypatch.setattr("hid_pilot.healer_runtime.wait_for_resurrection", Mock())
    healer.policy.busy_until = 100
    healer.active_cast = (Mock(), 100, False)
    healer.await_resurrection(20)
    assert healer.policy.ready_at["balance"] == 100
    assert healer.policy.busy_until == now[0]
    assert healer.policy.previous is None and not healer.policy.history
    assert healer.active_cast is None and healer.policy.follow_needed
    controller.key.assert_not_called()


def test_ee_death_enters_wait_before_mana_loot_or_skills(monkeypatch, tmp_path):
    from test_farm_ee import runtime, snapshot
    farmer, now = runtime(tmp_path, monkeypatch, snapshot().model_copy(update={"hp": 0, "mp": None}))
    farmer.await_resurrection = Mock(side_effect=lambda end: now.__setitem__(0, end))
    farmer.try_auto_loot = Mock()
    farmer.execute_ee = Mock()
    farmer.run(deadline=20)
    farmer.await_resurrection.assert_called_once_with(20)
    farmer.try_auto_loot.assert_not_called()
    farmer.execute_ee.assert_not_called()


def test_farmer_reobserves_after_resurrection_without_retry_movement(monkeypatch):
    from hid_pilot.farm import Farmer, FarmConfig
    controller, profile = Mock(), Mock()
    profile.read.side_effect = [0, None, 1, .3, .2, None]
    waiting = Mock()
    monkeypatch.setattr("hid_pilot.farm.wait_for_resurrection", waiting)
    farmer = Farmer(controller, profile, FarmConfig(rest="S"), Mock(), lambda: 0.)
    assert farmer.observe() == (.3, .2, None)
    waiting.assert_called_once()
    controller.key.assert_not_called()
