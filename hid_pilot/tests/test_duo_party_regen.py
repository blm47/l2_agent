import pytest

from hid_pilot.controller import Stopped
from test_duo import FARMER, make_duo, start_ma_attack
from test_duo_self_heal import observed_duo
from test_duo_summon import summon_duo
from test_summon import summon_scene


def test_f9_allows_initial_cast_and_three_retries_then_waits_for_last_regen():
    duo, controller, _ = summon_duo(.06, support_party_heal_key="F9", support_party_heal_cooldown=2)
    presses = []
    original = controller.key

    def key(name, duration, **kwargs):
        presses.append((name, duration, kwargs, controller.now))
        original(name, duration, **kwargs)

    controller.key = key
    duo.observe()
    for attempt in range(2, 5):
        assert duo.support_party_heal_ready < duo.support_party_regen_until
        controller.now = duo.support_party_heal_ready - .01
        duo.observe()
        assert controller.events.count((2, "F9")) == attempt - 1
        controller.now = duo.support_party_heal_ready
        duo.observe()
        assert controller.events.count((2, "F9")) == attempt
    assert duo.support_party_heal_ready == duo.support_party_regen_until
    for _ in range(10):
        controller.wait(.2)
        duo.observe()
    assert controller.events.count((2, "F9")) == 4
    assert duo.support_party_heal_attempts == 4
    assert all(duration == 300 and kwargs == {"exact": True}
               for name, duration, kwargs, _ in presses if name == "F9")
    controller.now = duo.support_party_regen_until
    duo.observe()
    assert controller.events.count((2, "F9")) == 5
    assert duo.support_party_heal_attempts == 1


@pytest.mark.parametrize("hp", [0., .8, 1., None])
def test_f9_retry_requires_fresh_living_wounded_party(hp):
    duo, controller, frame = summon_duo(.06, support_party_heal_key="F9")
    duo.observe()
    controller.now = duo.support_party_heal_ready
    if hp is None:
        frame[100:150] = 20
    else:
        frame[:] = summon_scene(hp)[0]
    duo.observe()
    assert controller.events.count((2, "F9")) == 1
    assert duo.support_party_heal_attempts == 1


def test_pause_and_old_frame_cannot_send_f9_retry_or_reset_attempt_budget():
    duo, controller, _ = summon_duo(.06, support_party_heal_key="F9")
    duo.observe()
    controller.now = duo.support_party_heal_ready
    assert not duo.support_maintenance(1., .9)
    controller.pause_generation += 1
    assert not duo.support_maintenance(1., .9)
    assert controller.events.count((2, "F9")) == 1
    assert duo.support_party_heal_attempts == 1


def test_slow_regen_increase_is_not_treated_as_proof_that_cast_cannot_be_retried():
    duo, controller, frame = summon_duo(.06, support_party_heal_key="F9")
    duo.observe()
    frame[:] = summon_scene(.2)[0]
    controller.now = duo.support_party_heal_ready
    duo.observe()
    assert controller.events.count((2, "F9")) == 2


def test_party_regen_deadline_starts_at_press_not_after_slow_return():
    duo, controller, _ = summon_duo(.2, support_party_heal_key="F9")
    switch = controller.switch_window

    def slow_return(target):
        switch(target)
        if target == FARMER:
            controller.wait(3)

    controller.switch_window = slow_return
    duo.observe()
    assert duo.support_party_regen_until == pytest.approx(16.)
    assert duo.support_party_heal_ready == pytest.approx(6.)


@pytest.mark.parametrize("role", ["farmer_hp", "support_hp", "summon_hp", "party_3_hp"])
def test_critical_party_interrupts_support_macro_and_schedules_full_retry(role):
    duo, controller = make_duo(heal_key=None)
    duo.buffing = True
    duo.support_buff_until = 30
    duo.support_buff_stage = 2
    duo.next_buff = 500
    duo.previous_resources = {role: .06}
    duo.resources_at = controller.now
    assert duo.update_support_buff()
    assert (2, "ESC") in controller.events
    assert not duo.buffing
    assert duo.support_buff_stage == 0
    assert duo.next_buff <= controller.now


@pytest.mark.parametrize("state", ["unknown", "stale", "pause", "dead", "healthy"])
def test_buff_cancellation_does_not_invent_critical_party_hp(state):
    duo, controller = make_duo(heal_key=None)
    duo.buffing = True
    duo.support_buff_until = 30
    duo.previous_resources = {"summon_hp": .06}
    duo.resources_at = controller.now
    if state == "unknown":
        duo.previous_resources["summon_hp"] = None
    elif state == "stale":
        controller.wait(2)
    elif state == "pause":
        controller.pause_generation += 1
    else:
        duo.previous_resources["summon_hp"] = 0 if state == "dead" else .8
    assert not duo.update_support_buff()
    assert controller.events == []


def test_summon_rescue_precedes_assist_and_buff_restarts_after_recovery():
    duo, controller, frame = summon_duo(.06, support_party_heal_key="F9")
    duo.buffing = True
    duo.support_buff_until = 30
    duo.support_buff_stage = 1
    duo.support_pending = True
    duo.observe()
    assert controller.events.index((2, "ESC")) < controller.events.index((2, "F9"))
    assert (2, duo.config.support_key) not in controller.events
    duo.buff()
    assert (2, duo.config.buff_key) not in controller.events
    frame[:] = summon_scene(1.)[0]
    controller.wait(.2)
    duo.buff()
    assert (2, duo.config.buff_key) in controller.events
    assert duo.next_buff > controller.now


def test_critical_summon_defers_repeated_assist_until_recovered():
    duo, controller, frame = summon_duo(.06, support_party_heal_key="F9")
    start_ma_attack(duo, controller)
    duo.support_pending = True
    duo.observe()
    assert (2, "F9") in controller.events
    assert (2, duo.config.support_key) not in controller.events
    assert duo.support_pending
    frame[:] = summon_scene(.9)[0]
    controller.capture = lambda: (1., .8, False)
    duo.observe()
    assert (2, duo.config.support_key) in controller.events


def test_elixir_keeps_twelve_percent_threshold_during_support_cast():
    duo, controller = make_duo(heal_key=None, hp_elixir_key="NUM2", hp_elixir_hp_threshold=.12)
    hp = [.121]
    controller.capture = lambda: (hp[0], .9, False)
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name == "NUM2":
            hp[0] = .7

    controller.key = key
    duo.wait_with_emergency_checks(0)
    assert (1, "NUM2") not in controller.events
    wait = controller.wait

    def fall(seconds):
        wait(seconds)
        if controller.now <= .2:
            hp[0] = .11

    controller.wait = fall
    duo.wait_with_emergency_checks(1.3)
    assert controller.events.count((1, "NUM2")) == 1
    assert hp[0] == .7


def test_own_vampiric_wait_services_elixir_before_cast_finishes():
    duo, controller = make_duo(heal_key=None, hp_elixir_key="NUM2", hp_elixir_hp_threshold=.12)
    controller.capture = lambda: (.11 if controller.now >= .2 else .3, .9, False)
    pressed = []
    original = controller.key

    def key(name, duration, **kwargs):
        pressed.append((name, controller.now))
        original(name, duration, **kwargs)

    controller.key = key
    duo.wait_for_vampiric(2.7)
    assert next(at for name, at in pressed if name == "NUM2") == pytest.approx(.2)
    assert (1, "ESC") not in controller.events


def test_own_vampiric_yields_to_ready_party_rescue_for_critical_summon():
    duo, controller, _ = summon_duo(.06, support_party_heal_key="F9")
    duo.wait_for_vampiric(2.7)
    assert controller.now < 2.7
    assert (1, "ESC") in controller.events
    duo.observe()
    assert (2, "F9") in controller.events


def test_emergency_wait_handles_support_consumable_on_fresh_frame(monkeypatch):
    duo, controller, reader = observed_duo(
        monkeypatch, .8, heal_key=None, support_self_heal_key=None,
        support_hp_elixir_key="NUM2", support_hp_elixir_hp_threshold=.35)
    original = controller.wait

    def fall(seconds):
        original(seconds)
        reader.return_value = .2

    controller.wait = fall
    duo.wait_with_emergency_checks(1.3)
    assert (2, "NUM2") in controller.events
    assert controller.role == FARMER


def test_emergency_wait_obeys_stop_before_sending_any_key():
    duo, controller = make_duo(hp_elixir_key="NUM2")
    controller.capture = lambda: (.1, .9, False)

    def stopped():
        raise Stopped("STOP")

    controller.checkpoint = stopped
    with pytest.raises(Stopped):
        duo.wait_with_emergency_checks(2.7)
    assert controller.events == []


def test_ma_buff_wait_interrupts_for_critical_summon_and_keeps_buff_due():
    duo, controller, frame = summon_duo(1., support_party_heal_key="F9", buff1_key="NUM9")
    original = controller.wait

    def fall(seconds):
        original(seconds)
        frame[:] = summon_scene(.06)[0]

    controller.wait = fall
    duo.casting_ma_buff = True
    assert not duo.wait_for_buff(3)
    assert (1, "ESC") in controller.events
    assert duo.buff_ready[1] <= controller.now
