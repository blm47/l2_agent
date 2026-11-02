from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hid_pilot.controller import Stopped
from hid_pilot.desktop import FocusLost
from hid_pilot.duo import SupportDied
from test_duo import FARMER
from test_duo_emergency_support import record_keys
from test_duo_lifeimpact import lifeimpact_duo
from test_duo_self_heal import observed_duo


@pytest.mark.parametrize("skill", ["heal", "self_heal", "chant", "vampiric", "preserv", "lifeimpact"])
def test_every_support_healing_skill_sends_exactly_one_pair(monkeypatch, skill):
    settings = {"heal_key": None, "support_self_heal_key": None}
    hp, support_hp = 1., 1.
    key = "F7"
    if skill == "heal":
        settings["heal_key"] = key
        hp = .5
    elif skill == "self_heal":
        settings["support_self_heal_key"] = key
        support_hp = .5
    elif skill == "chant":
        settings["support_party_heal_key"] = key
        hp = .5
    elif skill == "vampiric":
        settings["support_vampiric_key"] = key
        support_hp = .5
    elif skill == "preserv":
        settings["support_preserv_key"] = key
        hp = .4
    if skill == "lifeimpact":
        duo, controller = lifeimpact_duo(monkeypatch)
        key = "9"
        action = lambda: duo.use_support_lifeimpact(.5)
    else:
        duo, controller, _ = observed_duo(monkeypatch, support_hp, **settings)
        controller.capture = lambda: (hp, .9, False)
        action = duo.observe
    presses = record_keys(controller)
    action()
    casts = [item for item in presses if item[0] == key]
    assert len(casts) == 2
    assert all(item[1:3] == (80, {"exact": True}) for item in casts)
    assert all(item[4] == duo.support for item in casts)
    assert casts[1][3] - casts[0][3] == pytest.approx(.085)
    assert controller.events.count(("window", 2)) == 1
    assert controller.role == FARMER


@pytest.mark.parametrize("skill", ["heal", "chant", "preserv", "lifeimpact"])
@pytest.mark.parametrize("interruption", ["stop", "focus", "pause", "death"])
def test_second_press_is_cancelled_on_safety_interruption(monkeypatch, skill, interruption):
    duo, controller = lifeimpact_duo(monkeypatch)
    key = "9" if skill == "lifeimpact" else "F7"
    if skill == "lifeimpact":
        action = lambda: duo.use_support_lifeimpact(.5)
    elif skill == "preserv":
        duo.config.support_preserv_key = key
        action = lambda: duo.use_support_preserv(.4)
    elif skill == "chant":
        duo.config.support_party_heal_key = key
        duo.config.support_lifeimpact_key = None
        action = lambda: duo.support_maintenance(.5, .9)
    else:
        action = lambda: duo.visit(key, .1, double_press=True)
    press = controller.key

    def interrupting_press(name, duration, **kwargs):
        press(name, duration, **kwargs)
        if name != key:
            return
        if interruption == "pause":
            controller.pause_generation += 1
        elif interruption == "death":
            duo.support_life = SimpleNamespace(dead=True, observe=Mock())
        else:
            def failed_checkpoint():
                raise (Stopped("STOP") if interruption == "stop" else FocusLost("Фокус потерян"))
            controller.checkpoint = failed_checkpoint

    controller.key = interrupting_press
    if interruption == "pause":
        action()
    else:
        error = {"stop": Stopped, "focus": FocusLost, "death": SupportDied}[interruption]
        with pytest.raises(error):
            action()
    assert controller.events.count((2, key)) == 1


def test_press_spacing_is_measured_after_releasing_first_key(monkeypatch):
    duo, controller = lifeimpact_duo(monkeypatch)
    times = []
    press = controller.key

    def timed_press(key, duration, **kwargs):
        times.append(controller.now)
        press(key, duration, **kwargs)
        controller.now += duration / 1000

    controller.key = timed_press
    assert duo.use_support_lifeimpact(.5)
    assert times[1] - (times[0] + .080) == pytest.approx(.085)
    assert controller.now == pytest.approx(21.08)
