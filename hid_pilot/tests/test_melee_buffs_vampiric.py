import pytest
from pydantic import ValidationError

from hid_pilot.farm_melee import MeleeConfig
from hid_pilot.target import TargetObservation
from test_melee_farm import harness
from test_duo import make_duo


@pytest.mark.parametrize("duo_mode", [False, True])
@pytest.mark.parametrize("mp,allowed", [(.09, False), (.0999, False), (.1, True), (.5, True)])
def test_ma_self_buff_requires_at_least_ten_percent_mp(duo_mode, mp, allowed):
    if duo_mode:
        farmer, controller = make_duo(buff1_key="F5", heal_key=None)
        farmer.next_buff = float("inf")
        original_read = farmer.profile.read
        farmer.profile.read = lambda image, name: mp if name == "mp" else original_read(image, name)
    else:
        farmer, controller, _, state = harness(buff1_key="F5")
        state[1] = mp
    farmer.cast_buffs()
    assert (farmer.buff_actions == 1) is allowed
    assert farmer.config.buff_min_mp == .1
    if not allowed:
        assert farmer.buff_ready[1] == 0.
        assert "недостаточно MP" in farmer.buff_deferred[1]


def test_deferred_low_mp_buff_retries_without_rebuff_timer_reset():
    farmer, controller, _, state = harness(buff1_key="F5")
    state[1] = .05
    farmer.cast_buffs()
    farmer.cast_buffs()
    controller.key.assert_not_called()
    messages = [call.args[0] for call in farmer.notify.call_args_list]
    assert sum("недостаточно MP" in message for message in messages) == 1
    state[1] = .1
    farmer.cast_buffs()
    controller.key.assert_called_once_with("F5", 100)
    assert farmer.buff_ready[1] > 0


def test_mp_is_checked_again_before_next_ma_buff():
    farmer, controller, _, state = harness(buff1_key="F5", buff2_key="F6")
    original_key = controller.key.side_effect

    def key(name, duration, **kwargs):
        original_key(name, duration, **kwargs)
        if name == "F5":
            state[1] = .09

    controller.key.side_effect = key
    farmer.cast_buffs()
    assert [call.args[0] for call in controller.key.call_args_list] == ["F5"]
    assert farmer.buff_ready[2] == 0
    state[1] = .1
    farmer.cast_buffs()
    assert [call.args[0] for call in controller.key.call_args_list] == ["F5", "F6"]


def test_three_buffs_have_independent_casts_and_rebuff_timers():
    farmer, controller, now, _ = harness(
        buff1_key="F5", buff1_cast_seconds=1, buff1_rebuff_minutes=1,
        buff2_key="F6", buff2_cast_seconds=2, buff2_rebuff_minutes=2,
        buff3_key="F7", buff3_cast_seconds=3, buff3_rebuff_minutes=3,
    )
    farmer.cast_buffs()
    assert [call.args for call in controller.key.call_args_list] == [
        ("F5", 100), ("F6", 100), ("F7", 100)]
    assert sum(call.args[0] for call in controller.wait.call_args_list) == pytest.approx(6)
    assert all(call.args[0] <= .2 for call in controller.wait.call_args_list)
    farmer.cast_buffs()
    assert controller.key.call_count == 3
    now[0] = 61
    farmer.cast_buffs()
    assert controller.key.call_count == 4
    assert controller.key.call_args.args == ("F5", 100)


def test_damage_during_buff_prevents_next_buff():
    farmer, controller, _, state = harness(buff1_key="F5", buff2_key="F6")
    old_wait = controller.wait.side_effect

    def wait(seconds):
        old_wait(seconds)
        state[0] = .4

    controller.wait.side_effect = wait
    farmer.cast_buffs()
    assert [call.args[0] for call in controller.key.call_args_list] == ["F5"]


def test_disabled_buffs_do_not_observe_or_send_input():
    farmer, controller, _, _ = harness()
    farmer.observe = lambda: pytest.fail("Отключённый бафф не требует наблюдения")
    farmer.cast_buffs()
    controller.key.assert_not_called()


def test_empty_buff_keys_report_disabled_once():
    farmer, _, _, _ = harness()
    farmer.cast_buffs()
    farmer.cast_buffs()
    messages = [call.args[0] for call in farmer.notify.call_args_list]
    disabled = [message for message in messages if "баффы МА отключены" in message]
    assert len(disabled) == 1
    assert "buff1_key" in disabled[0]


@pytest.mark.parametrize("damaged,hp,reason", [
    (True, .4, "низкое HP"), (False, .3, "низкое HP"),
])
def test_deferred_buff_reports_reason_and_retries_when_safe(damaged, hp, reason):
    farmer, controller, _, state = harness(buff1_key="F5")
    farmer.under_attack = damaged
    state[0] = hp
    farmer.cast_buffs()
    farmer.cast_buffs()
    controller.key.assert_not_called()
    messages = [call.args[0] for call in farmer.notify.call_args_list]
    assert sum(reason in message for message in messages) == 1
    farmer.under_attack = False
    state[0] = 1.
    farmer.cast_buffs()
    controller.key.assert_called_once_with("F5", 100)


@pytest.mark.parametrize("hp,target,expected", [
    (.49, .51, True), (.5, .9, False), (.1, .5, False),
    (.1, None, False), (.1, 0., False), (.9, 1., False),
])
def test_vampiric_requires_both_hp_conditions(hp, target, expected):
    farmer, controller, _, _ = harness(vampiric_key="F8")
    assert farmer.use_vampiric(hp, target) is expected
    assert controller.key.called is expected


@pytest.mark.parametrize("kind", ["player", "npc"])
def test_vampiric_does_not_attack_players_or_npcs(kind):
    farmer, controller, _, _ = harness(vampiric_key="F8")
    farmer.last_target = TargetObservation(at=0, kind=kind)
    assert not farmer.use_vampiric(.2, .9)
    controller.key.assert_called_once_with("ESC", 80)


def test_vampiric_uses_own_threshold_cast_and_cooldown():
    farmer, controller, now, _ = harness(
        vampiric_key="F8", vampiric_hp_threshold=.7, vampiric_cast_seconds=2,
        vampiric_cooldown=10, hp_threshold=.3,
    )
    assert farmer.use_vampiric(.6, .9)
    assert controller.wait.call_args.args == (2,)
    assert not farmer.use_vampiric(.6, .9)
    now[0] = farmer.vampiric_ready
    assert farmer.use_vampiric(.6, .9)
    assert farmer.config.hp_threshold == .3


def test_real_fight_uses_vampiric_then_independent_potion():
    farmer, controller, _, state = harness(
        vampiric_key="F8", hp_threshold=.4, vampiric_hp_threshold=.5,
        regen_enabled=False,
    )
    state[:] = [.3, 1., .9]
    original_key = controller.key.side_effect

    def key(name, duration, **kwargs):
        original_key(name, duration)
        if name == farmer.config.heal:
            state[2] = 0.

    controller.key.side_effect = key
    farmer.verify_target = lambda hp, mp, target, identity: (hp, mp, target, True)
    farmer.fight(.9)
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys.index("F8") < keys.index(farmer.config.heal)


@pytest.mark.parametrize("field,value", [
    ("buff1_key", "ALT+TAB"), ("vampiric_key", "BAD"),
    ("buff2_cast_seconds", -1), ("buff3_rebuff_minutes", 0),
    ("vampiric_hp_threshold", 1.1),
])
def test_invalid_skill_settings_are_rejected(field, value):
    with pytest.raises(ValidationError):
        MeleeConfig(rest="=", **{field: value})
