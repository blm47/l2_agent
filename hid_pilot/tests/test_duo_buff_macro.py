import pytest

from test_duo_self_heal import observed_duo


def buff_team(monkeypatch, mp=.8, **settings):
    settings.setdefault("support_self_heal_key", None)
    duo, controller, reader = observed_duo(monkeypatch, 1., **settings)
    resources = {"hp": 1., "mp": mp}
    reader.side_effect = lambda image, kind: resources[kind]
    duo.config = duo.config.model_copy(update={"buff_seconds": 15})
    return duo, controller, resources


@pytest.mark.parametrize("mp,can_buff", [(None, False), (.59, False), (.6, True), (.8, True)])
def test_full_buff_requires_known_sufficient_party_mp(monkeypatch, mp, can_buff):
    duo, controller, _ = buff_team(monkeypatch, mp)
    duo.buff()
    assert ((2, duo.config.buff_key) in controller.events) is can_buff


def test_ma_defends_but_does_not_interrupt_or_heal_support_during_macro(monkeypatch):
    duo, controller, _ = buff_team(monkeypatch, aggro_key="2", heal_key="F9",
                                    support_party_heal_key="F2")
    controller.capture = lambda: (.4 if controller.now >= .5 else 1., .9, False)
    duo.buff()
    assert duo.buffing
    assert (1, "2") in controller.events
    assert (1, duo.config.attack) in controller.events
    assert (2, "ESC") not in controller.events
    assert (2, "F9") not in controller.events
    assert (2, "F2") not in controller.events
    duo.on_target_missing()
    assert (2, duo.config.follow_key) not in controller.events
    controller.wait(16)
    duo.observe()
    assert not duo.buffing
    assert duo.next_buff > controller.now
    assert controller.events.count((2, duo.config.buff_key)) == 1


@pytest.mark.parametrize("support_hp,interrupted", [(.35, False), (.349, True), (.3, True)])
def test_support_hp_below_thirty_five_percent_cancels_macro(monkeypatch, support_hp, interrupted):
    duo, controller, resources = buff_team(monkeypatch, support_party_heal_key="F2", heal_key=None)
    old_wait = controller.wait

    def wait(seconds):
        old_wait(seconds)
        if controller.now >= 1.5:
            resources["hp"] = support_hp

    controller.wait = wait
    duo.buff()
    assert ((2, "ESC") in controller.events) is interrupted
    if interrupted:
        assert (2, "F2") in controller.events
        assert duo.next_buff == 0
        assert not duo.buffing
    else:
        assert duo.buffing


def test_mp_cost_during_active_buff_does_not_cancel_started_macro(monkeypatch):
    duo, controller, resources = buff_team(monkeypatch)
    old_wait = controller.wait

    def wait(seconds):
        old_wait(seconds)
        resources["mp"] = .1

    controller.wait = wait
    duo.buff()
    assert controller.events.count((2, duo.config.buff_key)) == 1
    assert (2, "ESC") not in controller.events
    assert duo.next_buff > controller.now


def test_full_buff_waits_for_game_focus_and_before_returning_to_ma(monkeypatch):
    duo, controller, _ = buff_team(monkeypatch)
    timeline = []
    switch = controller.switch_window
    key = controller.key

    def switch_window(target):
        timeline.append(("window", target.hwnd, controller.now))
        switch(target)

    def press(name, duration, **kwargs):
        timeline.append(("key", name, controller.now, duration, kwargs))
        key(name, duration, **kwargs)

    controller.switch_window = switch_window
    controller.key = press
    duo.buff()
    enter, press, leave = timeline[:3]
    assert enter[:2] == ("window", 2)
    assert press[:2] == ("key", duo.config.buff_key)
    assert press[2] - enter[2] == pytest.approx(.5)
    assert press[3:] == (150, {"exact": True})
    assert leave[:2] == ("window", 1)
    assert leave[2] - press[2] == pytest.approx(.5)
    assert duo.support_buff_started == leave[2]


def test_stop_during_buff_focus_delay_does_not_press_key_or_start_timer(monkeypatch):
    from hid_pilot.controller import Stopped

    duo, controller, _ = buff_team(monkeypatch)

    def stop(seconds):
        assert controller.role == duo.support
        assert seconds == .5
        raise Stopped("Сценарий остановлен")

    controller.wait = stop
    with pytest.raises(Stopped):
        duo.buff()
    assert (2, duo.config.buff_key) not in controller.events
    assert not duo.buffing
    assert duo.support_buff_until is None
    assert controller.role == duo.support


def test_two_support_buffs_wait_ten_seconds_each_before_follow(monkeypatch):
    duo, controller, _ = buff_team(monkeypatch, support_buff2_key="F12", follow_key="0")
    duo.config = duo.config.model_copy(update={"buff_seconds": 10})
    timeline = []
    key = controller.key

    def press(name, duration, **kwargs):
        timeline.append((controller.role.hwnd, name, controller.now))
        key(name, duration, **kwargs)

    controller.key = press
    duo.buff()
    first = next(t for window, name, t in timeline if window == 2 and name == duo.config.buff_key)
    second = next(t for window, name, t in timeline if window == 2 and name == "F12")
    follow = next(t for window, name, t in timeline if window == 2 and name == "0")
    assert second - first >= 10
    assert follow - second >= 10
    assert controller.events.count((2, "F12")) == 1
    assert not duo.buffing


def test_emergency_at_second_buff_deadline_prevents_second_key(monkeypatch):
    duo, controller, resources = buff_team(monkeypatch, support_buff2_key="F12")
    controller.capture = lambda: (.4 if controller.now >= .5 else 1., .9, False)
    duo.buff()
    assert duo.buffing
    controller.now = duo.support_buff_until
    resources["hp"] = .34
    duo.observe()
    assert (2, "F12") not in controller.events
    assert not duo.buffing


@pytest.mark.parametrize("stage", [1, 2])
def test_potion_during_buff_preserves_macro_timer_and_cooldown(monkeypatch, stage):
    duo, controller, resources = buff_team(
        monkeypatch, support_buff2_key="F12", support_potion_key="F5",
        support_potion_hp_threshold=.5, support_potion_cooldown=12,
        support_party_heal_key="F7", support_vampiric_key="F6", heal_key="F9")
    resources["hp"] = .4
    duo.buffing = True
    duo.support_buff_stage = stage
    duo.support_buff_started = 0.
    duo.support_buff_until = 10.
    duo.observe()
    assert controller.events == [("window", 2), (2, "F5"), ("window", 1)]
    assert duo.buffing
    assert duo.support_buff_stage == stage
    assert duo.support_buff_until == 10.
    assert duo.next_buff == 0.
    duo.observe()
    assert controller.events.count((2, "F5")) == 1
    controller.now = duo.support_buff_until
    duo.observe()
    if stage == 1:
        assert (2, "F12") in controller.events
        assert duo.buffing
        assert duo.support_buff_stage == 2
    else:
        assert not duo.buffing
        assert duo.next_buff > controller.now


def test_hp_drop_after_buff_potion_cancels_macro_without_waiting_next_tick(monkeypatch):
    duo, controller, resources = buff_team(
        monkeypatch, support_potion_key="F5", support_potion_hp_threshold=.5)
    resources["hp"] = .4
    duo.buffing = True
    duo.support_buff_until = 10.
    original_key = controller.key

    def press(name, duration, **kwargs):
        original_key(name, duration, **kwargs)
        if name == "F5":
            resources["hp"] = .34

    controller.key = press
    duo.observe()
    assert controller.events.index((2, "F5")) < controller.events.index((2, "ESC"))
    assert not duo.buffing
    assert duo.next_buff == 0.
