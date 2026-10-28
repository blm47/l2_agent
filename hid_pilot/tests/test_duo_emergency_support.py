import pytest

from hid_pilot.controller import Stopped
from hid_pilot.duo import SupportDied
from test_duo import FARMER
from test_duo_self_heal import observed_duo


def falling_support(monkeypatch, **settings):
    duo, controller, reader = observed_duo(
        monkeypatch, .9, support_self_heal_key=None, heal_key=None, **settings)
    duo.observe()
    controller.wait(.25)
    reader.return_value = .2
    return duo, controller, reader


def record_keys(controller):
    presses = []
    original = controller.key

    def key(name, duration, **kwargs):
        presses.append((name, duration, kwargs, controller.now, controller.role))
        original(name, duration, **kwargs)

    controller.key = key
    return presses


def test_fast_damage_sends_elixir_twice_in_one_visit_then_obeys_cooldown(monkeypatch):
    duo, controller, _ = falling_support(monkeypatch, support_hp_elixir_key="NUM2")
    duo.hp_elixir_clock = lambda: 0.
    presses = record_keys(controller)
    duo.observe()
    items = [item for item in presses if item[0] == "NUM2"]
    assert len(items) == 2
    assert all(item[:3] == ("NUM2", 150, {"exact": True}) for item in items)
    assert items[1][3] - items[0][3] == pytest.approx(.15)
    assert all(item[4] == duo.support for item in items)
    assert controller.events.count(("window", 2)) == 1
    assert controller.role == FARMER
    duo.observe()
    controller.wait(10)
    duo.observe()
    assert controller.events.count((2, "NUM2")) == 2


def test_fast_damage_repeats_vampiric_without_extra_assist_visit(monkeypatch):
    duo, controller, _ = falling_support(monkeypatch, support_vampiric_key="F6")
    presses = record_keys(controller)
    duo.attacking = True
    duo.support_pending = True
    duo.observe()
    casts = [item for item in presses if item[0] == "F6"]
    assert len(casts) == 2
    assert casts[1][3] - casts[0][3] == pytest.approx(.15)
    assert all(item[1:3] == (150, {"exact": True}) for item in casts)
    assert controller.events.count((2, duo.config.support_key)) == 1
    assert controller.events.count(("window", 2)) == 1
    assert duo.support_pending
    assert controller.role == FARMER
    duo.observe()
    assert controller.events.count((2, "F6")) == 2


@pytest.mark.parametrize("interruption", ["gap", "pause", "unknown", "healed"])
def test_damage_history_does_not_cross_gaps_pause_unknown_or_healing(monkeypatch, interruption):
    duo, controller, reader = falling_support(monkeypatch, support_hp_elixir_key="NUM2")
    if interruption == "gap":
        controller.wait(2)
    elif interruption == "pause":
        controller.pause_generation += 1
    elif interruption == "unknown":
        reader.return_value = None
        duo.observe()
        controller.wait(.25)
        reader.return_value = .2
    else:
        reader.return_value = .1
        duo.track_damage(None, 1.)
        controller.wait(.25)
        reader.return_value = .2
    duo.observe()
    assert controller.events.count((2, "NUM2")) == 1


def test_slow_hp_loss_does_not_repeat_elixir(monkeypatch):
    duo, controller, reader = observed_duo(
        monkeypatch, .4, support_self_heal_key=None, heal_key=None,
        support_hp_elixir_key="NUM2", support_hp_elixir_hp_threshold=.3)
    duo.observe()
    for _ in range(12):
        controller.wait(.5)
        reader.return_value -= .01
        duo.observe()
    assert controller.events.count((2, "NUM2")) == 1


@pytest.mark.parametrize("key", ["NUM2", "F6"])
@pytest.mark.parametrize("interruption", ["stop", "pause", "focus_pause", "death"])
def test_emergency_second_press_respects_stop_pause_and_death(monkeypatch, key, interruption):
    settings = {"support_hp_elixir_key": key} if key == "NUM2" else {"support_vampiric_key": key}
    duo, controller, _ = falling_support(monkeypatch, **settings)
    original = controller.key
    if interruption == "focus_pause":
        switch_window = controller.switch_window

        def switch(target):
            switch_window(target)
            if target == duo.support:
                controller.pause_generation += 1

        controller.switch_window = switch

    def press(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name != key:
            return
        if interruption == "stop":
            controller.checkpoint = lambda: (_ for _ in ()).throw(Stopped("STOP"))
        elif interruption == "pause":
            controller.pause_generation += 1
        elif interruption == "death":
            class DeadSupport:
                dead = True

                def observe(self, *_):
                    pass

            duo.support_life = DeadSupport()

    controller.key = press
    if interruption in {"pause", "focus_pause"}:
        duo.observe()
    else:
        with pytest.raises(Stopped if interruption == "stop" else SupportDied):
            duo.observe()
    assert controller.events.count((2, key)) == 1


def test_potion_is_checked_on_refresh_after_vampiric_before_assist(monkeypatch):
    duo, controller, reader = observed_duo(
        monkeypatch, .55, support_self_heal_key=None, heal_key=None,
        support_vampiric_key="F6", support_potion_key="F5", support_potion_hp_threshold=.25)
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name == "F6":
            reader.return_value = .2

    controller.key = key
    duo.attacking = True
    duo.observe()
    assert controller.events.index((2, "F6")) < controller.events.index((2, "F5"))
    assert controller.events.count((2, "F5")) == 1


def test_successful_elixir_refresh_prevents_unnecessary_potion(monkeypatch):
    duo, controller, reader = falling_support(
        monkeypatch, support_hp_elixir_key="NUM2", support_potion_key="F5")
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name == "NUM2":
            reader.return_value = .9

    controller.key = key
    duo.observe()
    assert controller.events.count((2, "NUM2")) == 2
    assert (2, "F5") not in controller.events


def test_low_support_log_explains_potion_thresholds_and_cooldowns(monkeypatch):
    duo, controller, _ = observed_duo(
        monkeypatch, .2, support_self_heal_key=None, heal_key=None,
        support_hp_elixir_key="NUM2", support_potion_key="F5")
    duo.hp_elixir_clock = lambda: 0.
    duo.support_hp_elixir_ready = 100.
    duo.support_potion_ready = 10.
    duo.observe()
    messages = [call.args[0] for call in duo.notify.call_args_list]
    assert any("HP 20.0%" in message and "100.0 с" in message and "10.0 с" in message
               for message in messages)
    assert controller.events == []
