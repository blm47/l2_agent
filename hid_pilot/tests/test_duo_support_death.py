from unittest.mock import Mock

import numpy as np
import pytest

from hid_pilot.duo import SupportDied
from hid_pilot.support_life import SupportLifeObserver
from test_duo import FARMER, SUPPORT, make_duo


def losing_support(target=.9, **settings):
    cycles = settings.pop("cycles", 1)
    duo, controller = make_duo(**settings)
    duo.config = duo.config.model_copy(update={"cycles": cycles})
    hp = [target]
    controller.capture = lambda: (1., hp[0], False) if controller.role == FARMER else np.zeros((220, 700, 3), np.uint8)
    death = Mock()
    duo.support_life = SupportLifeObserver(death, Mock(), reader=lambda image: 0,
                                            clock=lambda: 0, button_reader=lambda image: False)
    return duo, controller, hp, death


@pytest.mark.parametrize("fast", [False, True])
def test_support_death_finishes_selected_target_without_choosing_another(fast):
    duo, controller, hp, death = losing_support(.05 if fast else .9,
                                                experimental_fast_farm=fast, cycles=3)
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if controller.role == FARMER and name == duo.config.attack:
            hp[0] = max(0., hp[0] - .3)

    controller.key = key
    with pytest.raises(SupportDied, match="новые цели запрещены"):
        duo.run()
    assert hp[0] == 0
    assert duo.training_kills == 1
    assert duo.support_lost
    death.assert_called_once_with()
    assert (FARMER.hwnd, duo.config.next_target) not in controller.events
    assert not any(role == SUPPORT.hwnd for role, _ in controller.events)
    assert controller.role == FARMER


def test_support_death_without_selected_target_stops_without_attack_or_search():
    duo, controller, hp, death = losing_support(None)
    with pytest.raises(SupportDied):
        duo.run()
    assert controller.role == FARMER
    assert not any(isinstance(role, int) for role, _ in controller.events)
    death.assert_called_once_with()


def test_support_death_during_combat_preserves_only_the_current_target():
    duo, controller, hp, death = losing_support(.9, buff_delay_minutes=100,
                                                experimental_fast_farm=True, cycles=3)
    detected_at = []
    death.side_effect = lambda: detected_at.append(len(controller.events))
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if controller.role == FARMER and name == duo.config.attack:
            hp[0] = max(0., hp[0] - .2)

    controller.key = key
    with pytest.raises(SupportDied):
        duo.run()
    assert hp[0] == 0
    assert duo.training_kills == 1
    assert len(detected_at) == 1
    after_death = controller.events[detected_at[0]:]
    assert (FARMER.hwnd, duo.config.next_target) not in after_death
    assert not any(role == SUPPORT.hwnd for role, _ in after_death)


def test_unreachable_current_target_stops_without_search_or_movement():
    duo, controller, hp, death = losing_support(.9, approach_delay=.4)
    with pytest.raises(SupportDied):
        duo.run()
    keys = [key for role, key in controller.events if role == FARMER.hwnd]
    assert keys
    assert set(keys) <= {duo.config.attack, "ESC"}
    assert duo.training_kills == 0


def test_vampiric_visit_checks_death_before_sending_any_support_macro():
    duo, controller, hp, death = losing_support()
    with pytest.raises(SupportDied):
        duo.visit_vampiric()
    assert duo.support_lost
    assert controller.events == [("window", SUPPORT.hwnd), ("window", FARMER.hwnd)]


def test_new_target_and_escape_are_forbidden_after_support_death():
    duo, controller = make_duo()
    duo.support_lost = True
    for operation in [duo.acquire, lambda: duo.search_known_target(),
                      lambda: duo.escape_from_unreachable("Mob")]:
        # Для проверки /target в пуле есть подходящее имя.
        duo.known_mobs["Mob"] = None
        with pytest.raises(SupportDied):
            operation()
    assert controller.events == []


def test_farmer_death_during_finishing_does_not_enter_resurrection_wait(monkeypatch):
    duo, controller = make_duo()
    duo.support_lost = True
    controller.capture = lambda: (0., .9, False)
    resurrection = Mock()
    monkeypatch.setattr("hid_pilot.farm.wait_for_resurrection", resurrection)
    with pytest.raises(SupportDied, match="МА тоже погиб"):
        duo.observe()
    resurrection.assert_not_called()
    duo.notify.death.assert_called_once_with()
