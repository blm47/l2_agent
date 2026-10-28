import pytest

from hid_pilot.bars import BarBox
from test_duo import make_duo, start_ma_attack
from test_summon import summon_profile, summon_scene


def summon_duo(hp=.4, mp=1., **settings):
    settings.setdefault("heal_key", None)
    settings.setdefault("support_party_heal_key", "F7")
    duo, controller = make_duo(**settings)
    profile = summon_profile()
    frame, _ = summon_scene(hp, mp)
    original_track = duo.track_damage

    def track(image, own_hp):
        # Базовые тесты дуо используют кортеж статов; самон читается настоящим CV из кадра.
        original = duo.profile
        duo.profile = profile
        try:
            original_track(frame, own_hp)
        finally:
            duo.profile = original

    duo.track_damage = track
    return duo, controller, frame


def test_only_wounded_summon_triggers_existing_party_heal_without_target_selection():
    duo, controller, _ = summon_duo()
    duo.observe()
    assert controller.events == [("window", 2), (2, "F7"), ("window", 1)]
    assert duo.previous_resources["summon_hp"] == .4
    assert not duo.support_lost
    assert any("самон HP 40.0%" in call.args[0] for call in duo.notify.call_args_list)
    duo.observe()
    assert controller.events.count((2, "F7")) == 1
    controller.now = duo.support_party_heal_ready
    duo.observe()
    assert controller.events.count((2, "F7")) == 2


@pytest.mark.parametrize("hp", [0., .6, 1.])
def test_dead_or_healthy_summon_does_not_trigger_heal_or_stop_farm(hp):
    duo, controller, _ = summon_duo(hp)
    duo.observe()
    assert controller.events == []
    assert not duo.support_lost


@pytest.mark.parametrize("blocked", ["missing_panel", "disabled", "buff", "paused", "stale", "support_dead"])
def test_unknown_disabled_or_unsafe_summon_healing_does_not_send_keys(blocked):
    duo, controller, frame = summon_duo()
    if blocked == "missing_panel":
        frame[100:150] = 20
    elif blocked == "disabled":
        duo.config.support_party_heal_key = None
    elif blocked == "buff":
        duo.buffing = True
    elif blocked == "support_dead":
        duo.support_lost = True
    duo.track_damage(None, 1.)
    if blocked == "paused":
        controller.pause_generation += 1
    elif blocked == "stale":
        controller.wait(2)
    assert not duo.support_maintenance(1., .9)
    assert controller.events == []


@pytest.mark.parametrize("target", [.5, 0., None])
def test_summon_heal_restores_assist_only_for_confirmed_living_ma_target(target):
    duo, controller, _ = summon_duo()
    start_ma_attack(duo, controller)
    controller.capture = lambda: (1., target if (2, "F7") in controller.events else .9, False)
    duo.observe()
    assert (2, "F7") in controller.events
    assert ((2, duo.config.support_key) in controller.events) is (target is not None and target > 0)


@pytest.mark.parametrize("resource,damage", [("hp", True), ("mp", False)])
def test_only_summon_hp_loss_triggers_party_defense(resource, damage):
    duo, controller, frame = summon_duo(1.)
    duo.track_damage(None, 1.)
    controller.wait(.2)
    frame[:] = summon_scene(**{resource: .4})[0]
    duo.track_damage(None, 1.)
    assert duo.party_damage_now is damage
    assert ("summon_hp" in duo.party_damage_sources) is damage


@pytest.mark.parametrize("gap,pause", [(2, False), (.2, True)])
def test_old_or_pre_pause_summon_health_does_not_invent_damage(gap, pause):
    duo, controller, frame = summon_duo(1.)
    duo.track_damage(None, 1.)
    controller.wait(gap)
    controller.pause_generation += int(pause)
    frame[:] = summon_scene(.4)[0]
    duo.track_damage(None, 1.)
    assert not duo.party_damage_now


def test_real_frame_reaches_statistics_and_group_heal_through_normal_observation():
    duo, controller, frame = summon_duo()
    duo.profile = summon_profile()
    for name, y, color in (("hp", 10, (60, 55, 155)), ("mp", 20, (195, 110, 55)),
                            ("target_hp", 30, (60, 55, 155))):
        frame[y:y + 8, 60:260] = color
        duo.profile.bars[name] = BarBox(x1=.15, y1=y / 200, x2=.65, y2=(y + 8) / 200)
    controller.capture = lambda: frame
    hp, mp, target = duo.observe()
    assert (hp, mp, target) == (1., 1., 1.)
    assert duo.display_data[0]["summon_hp"] == .4
    assert duo.display_data[0]["summon_mp"] == 1.
    assert (2, "F7") in controller.events
