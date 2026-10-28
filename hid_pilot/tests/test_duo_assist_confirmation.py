import pytest

from hid_pilot.target import TargetObservation
from test_duo import make_duo


def pending_assist(**settings):
    duo, controller = make_duo(heal_key=None, **settings)
    target = [1.]
    controller.capture = lambda: (1., target[0], False)
    duo.support_pending = True
    duo.observe()
    return duo, controller, target


def test_normal_assist_waits_for_attack_and_measurable_target_hp_drop():
    duo, controller, target = pending_assist()
    duo.combat_key(duo.config.attack, 100)
    assert (2, duo.config.support_key) not in controller.events
    target[0] = .999
    duo.observe()
    assert (2, duo.config.support_key) not in controller.events
    target[0] = .997
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1
    assert controller.events.index((1, duo.config.attack)) < controller.events.index((2, duo.config.support_key))


def test_wounded_target_and_damage_before_ma_attack_do_not_allow_assist():
    duo, controller, target = pending_assist()
    target[0] = .3
    duo.observe()
    assert (2, duo.config.support_key) not in controller.events
    duo.combat_key(duo.config.attack, 100)
    assert (2, duo.config.support_key) not in controller.events
    target[0] = .29
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1


def test_fast_assist_does_not_wait_for_hp_drop():
    duo, controller, _ = pending_assist(experimental_fast_farm=True)
    assert (2, duo.config.support_key) not in controller.events
    duo.combat_key(duo.config.attack, 100)
    assert controller.events.count((2, duo.config.support_key)) == 1
    assert not duo.support_attack_confirmed


@pytest.mark.parametrize("change", ["selection", "identity", "pause"])
def test_new_target_or_pause_requires_new_attack_confirmation(change):
    duo, controller, target = pending_assist()
    duo.last_target = TargetObservation(at=0, kind="monster", name="One", level=10,
                                       hp=1., confidence=.99)
    duo.combat_key(duo.config.attack, 100)
    target[0] = .9
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1
    duo.support_pending = True
    if change == "selection":
        duo.invalidate_target()
        duo.press(duo.config.next_target)
    elif change == "identity":
        duo.last_target = TargetObservation(at=controller.now, kind="monster", name="Other", level=10,
                                           hp=.8, confidence=.99)
    else:
        controller.pause_generation += 1
    target[0] = .8
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1
    duo.combat_key(duo.config.attack, 100)
    assert controller.events.count((2, duo.config.support_key)) == 1
    target[0] = .79
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 2


@pytest.mark.parametrize("target_hp", [None, 0.])
def test_unknown_or_dead_hp_does_not_count_as_attack_progress(target_hp):
    duo, controller, target = pending_assist()
    duo.combat_key(duo.config.attack, 100)
    target[0] = target_hp
    duo.observe()
    assert (2, duo.config.support_key) not in controller.events
    target[0] = .8
    duo.observe()
    assert (2, duo.config.support_key) not in controller.events
    target[0] = .79
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1


def test_attack_flag_without_attack_command_does_not_allow_assist():
    duo, controller, target = pending_assist()
    duo.attacking = True
    target[0] = .8
    duo.observe()
    assert (2, duo.config.support_key) not in controller.events
    duo.combat_key(duo.config.attack, 100)
    assert (2, duo.config.support_key) not in controller.events
    target[0] = .79
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1


def test_ma_buff_preserves_confirmation_for_the_same_target():
    duo, controller, target = pending_assist()
    duo.combat_key(duo.config.attack, 100)
    target[0] = .9
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1
    duo.casting_ma_buff = True
    duo.support_pending = True
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1
    duo.casting_ma_buff = False
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 2


def test_buff_button_matching_attack_does_not_count_as_ma_attack():
    duo, controller, target = pending_assist()
    duo.casting_ma_buff = True
    duo.combat_key(duo.config.attack, 100)
    duo.casting_ma_buff = False
    target[0] = .8
    duo.observe()
    assert (2, duo.config.support_key) not in controller.events
    duo.combat_key(duo.config.attack, 100)
    assert (2, duo.config.support_key) not in controller.events
    target[0] = .79
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1


def test_life_saving_vampiric_keeps_its_preparatory_assist(monkeypatch):
    from test_duo_self_heal import observed_duo

    duo, controller, _ = observed_duo(monkeypatch, .4, support_self_heal_key=None,
                                    support_vampiric_key="F6", heal_key=None)
    duo.observe()
    assert (1, duo.config.attack) not in controller.events
    assert not duo.support_attack_confirmed
    assert controller.events.index((2, duo.config.support_key)) < controller.events.index((2, "F6"))
