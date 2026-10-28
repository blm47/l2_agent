import pytest

from hid_pilot.target import TargetObservation
from test_duo import make_duo, start_ma_attack


def assisting_duo(**settings):
    duo, controller = make_duo(heal_key=None, **settings)
    controller.capture = lambda: (1., .9, False)
    start_ma_attack(duo, controller)
    duo.support_pending = True
    return duo, controller


def test_assist_has_one_bounded_retry_for_same_living_target():
    duo, controller = assisting_duo()
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1
    controller.wait(duo.config.support_assist_retry_seconds - .01)
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1
    controller.wait(.01)
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 2
    controller.wait(30)
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 2


@pytest.mark.parametrize("target", [None, 0])
def test_assist_retry_is_not_sent_for_unknown_or_dead_target(target):
    duo, controller = assisting_duo()
    duo.observe()
    controller.wait(10)
    controller.capture = lambda: (1., target, False)
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1


def test_assist_retry_is_not_sent_after_confirmed_target_change():
    duo, controller = assisting_duo()
    duo.last_target = TargetObservation(at=0, kind="monster", name="One", level=10, hp=.9, confidence=.99)
    duo.observe()
    controller.wait(10)
    duo.last_target = TargetObservation(at=10, kind="monster", name="Other", level=10, hp=.9, confidence=.99)
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1


def test_assist_retry_rejects_player_target_and_does_not_bypass_safety():
    duo, controller = assisting_duo()
    duo.observe()
    controller.wait(10)
    duo.last_target = TargetObservation(at=10, kind="player", player_class="Elven Elder",
                                         hp=.9, confidence=.99)
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 1


def test_assist_retry_can_be_disabled_and_does_not_cross_pause():
    for settings, pause in [({"support_assist_retries": 0}, False), ({}, True)]:
        duo, controller = assisting_duo(**settings)
        duo.observe()
        controller.wait(10)
        controller.pause_generation += int(pause)
        duo.observe()
        assert controller.events.count((2, duo.config.support_key)) == 1


def test_support_buff_completion_rearms_assist_for_current_fight():
    duo, controller = assisting_duo()
    duo.support_pending = False
    duo.buffing = True
    duo.support_buff_until = 0
    duo.support_buff_stage = 2
    duo.observe()
    assert not duo.buffing
    assert controller.events.count((2, duo.config.support_key)) == 1


@pytest.mark.parametrize("two_stages", [False, True])
def test_full_buff_resumes_assist_only_after_last_stage(two_stages):
    duo, controller = assisting_duo(support_buff2_key="F8" if two_stages else None,
                                    support_buff2_seconds=1)
    duo.observe()
    controller.wait(duo.config.support_assist_retry_seconds)
    duo.observe()
    assert duo.support_assist_attempts == 2

    duo.buff()
    assert duo.buffing
    assert controller.events.count((2, duo.config.support_key)) == 2
    controller.now = duo.support_buff_until
    duo.observe()
    if two_stages:
        assert duo.buffing
        assert (2, "F8") in controller.events
        assert controller.events.count((2, duo.config.support_key)) == 2
        controller.now = duo.support_buff_until
        duo.observe()

    assert not duo.buffing
    assert controller.events.count((2, duo.config.support_key)) == 3
    assert not duo.support_pending
    assert not duo.following
    controller.wait(duo.config.support_assist_retry_seconds)
    duo.observe()
    assert controller.events.count((2, duo.config.support_key)) == 4


@pytest.mark.parametrize("attacking,target", [(False, .9), (True, None), (True, 0)])
def test_buff_completion_waits_for_active_fight_with_living_target(attacking, target):
    duo, controller = assisting_duo()
    duo.attacking = attacking
    controller.capture = lambda: (1., target, False)
    duo.buffing = True
    duo.support_buff_until = 0
    duo.support_buff_stage = 1
    duo.observe()
    assert not duo.buffing
    assert (2, duo.config.support_key) not in controller.events
    assert duo.support_pending


def test_assist_returns_fresh_target_hp_instead_of_pre_visit_value():
    duo, controller = assisting_duo()
    target = [.9]
    original = controller.key

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name == duo.config.support_key:
            target[0] = 0.

    controller.key = key
    controller.capture = lambda: (1., target[0], False)
    assert duo.observe()[2] == 0
    assert controller.events.count((2, duo.config.support_key)) == 1
