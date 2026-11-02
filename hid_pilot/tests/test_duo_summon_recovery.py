from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from hid_pilot.controller import Stopped
from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from hid_pilot.farm_melee import MeleeFarmer
from test_duo import make_duo
from test_duo_summon import summon_duo
from test_summon import summon_scene


def recovery_duo(hp=None, **settings):
    settings.setdefault("summon_key", "NUM6")
    settings.setdefault("support_party_heal_key", None)
    settings.setdefault("buff1_key", "F8")
    settings.setdefault("buff1_cast_seconds", 1)
    duo, controller, frame = summon_duo(hp or 0., **settings)
    if hp is None:
        frame[:] = 20
    duo.party_profile = object()
    support = {"support_hp": 1., "support_mp": 1.}
    duo.support_resources = lambda image: support.copy()
    controller.capture = lambda: (1., None, False)
    sent = []
    original_key = controller.key

    def key(name, duration, **kwargs):
        sent.append((controller.now, controller.role.hwnd, name))
        original_key(name, duration, **kwargs)

    controller.key = key
    return duo, controller, frame, support, sent


def appear_after_cast(duo, controller, frame, sent, attempt=1):
    def capture():
        starts = [at for at, role, key in sent if role == 1 and key == duo.config.summon_key]
        if len(starts) >= attempt and controller.now >= starts[-1] + duo.config.summon_cast_seconds:
            frame[:] = summon_scene()[0]
        return 1., None, False

    controller.capture = capture


def test_defaults_roundtrip_and_disable():
    config = DuoConfig()
    assert (config.summon_key, config.summon_cast_seconds, config.summon_cooldown) == ("NUM6", 20, 5)
    assert parse_duo_settings(format_duo_settings(config)) == config
    assert parse_duo_settings('set summon_key ""').summon_key is None
    with pytest.raises(ValidationError):
        DuoConfig(summon_key="BAD")
    with pytest.raises(ValidationError):
        DuoConfig(summon_cast_seconds=0)


def test_startup_checks_then_casts_then_rebuffs_both_roles_before_search():
    duo, controller, frame, _, sent = recovery_duo(buff_delay_minutes=10)
    appear_after_cast(duo, controller, frame, sent)
    duo.service_summon()
    summon = next(at for at, role, key in sent if key == "NUM6")
    support_buff = next(at for at, role, key in sent if role == 2 and key == "F1")
    own_buff = next(at for at, role, key in sent if role == 1 and key == "F8")
    assert summon >= duo.config.quiet_seconds
    assert support_buff >= summon + 20
    assert own_buff >= summon + 20
    assert not duo.summon_recovery.pending and not duo.summon_recovery.rebuff
    assert not duo.buffs_due()
    assert (1, duo.config.next_target) not in controller.events
    assert (1, duo.config.attack) not in controller.events


def test_living_panther_at_start_needs_no_cast_or_forced_rebuff():
    duo, controller, _, _, _ = recovery_duo(1.)
    duo.service_summon()
    assert duo.summon_recovery.checked
    assert controller.events == []


def test_ten_failed_casts_stop_without_extra_five_second_delays():
    duo, controller, _, _, sent = recovery_duo()
    with pytest.raises(RuntimeError, match="10 попыток"):
        duo.service_summon()
    times = [at for at, role, key in sent if key == "NUM6"]
    assert len(times) == 10
    assert [later - earlier for earlier, later in zip(times, times[1:])] == pytest.approx([20.] * 9)
    assert controller.now >= times[-1] + 20
    assert (2, "F1") not in controller.events
    assert (1, duo.config.attack) not in controller.events
    assert any("10/10; HP неизвестно, MP неизвестно" in call.args[0]
               for call in duo.notify.call_args_list)


def test_retry_succeeds_and_cooldown_longer_than_cast_is_respected():
    duo, controller, frame, _, sent = recovery_duo(summon_cast_seconds=1, summon_cooldown=5)
    appear_after_cast(duo, controller, frame, sent, attempt=2)
    duo.service_summon()
    times = [at for at, role, key in sent if key == "NUM6"]
    assert len(times) == 2
    assert 5 <= times[1] - times[0] < 5.3


def test_death_mid_fight_disables_fast_switch_until_recovery():
    duo, controller, frame, _, _ = recovery_duo(1., experimental_fast_farm=True)
    duo.track_damage(None, 1.)
    duo.attacking = True
    controller.wait(.2)
    frame[:] = summon_scene(0.)[0]
    duo.track_damage(None, 1.)
    assert duo.summon_recovery.pending
    assert not duo.allow_fast_target_switch()
    assert (1, "NUM6") not in controller.events
    duo.summon_recovery.pending = False
    assert duo.allow_fast_target_switch()


def test_damage_during_cast_cancels_before_defensive_attack():
    duo, controller, _, support, sent = recovery_duo()
    for _ in range(16):
        duo.observe()
        controller.wait(.2)
    start = controller.now

    def capture():
        if controller.now >= start + .6:
            support["support_hp"] = .8
        return 1., .8, False

    controller.capture = capture
    duo.cast_summon()
    assert controller.now < start + 20
    keys = [(role, key) for _, role, key in sent]
    assert keys.index((1, "NUM6")) < keys.index((1, "ESC")) < keys.index((1, duo.config.attack))
    assert not duo.summon_casting


@pytest.mark.parametrize("interrupt", ["stop", "pause", "unknown"])
def test_cast_wait_observes_stop_pause_and_missing_party_hp(interrupt):
    duo, controller, _, support, _ = recovery_duo()
    for _ in range(16):
        duo.observe()
        controller.wait(.2)
    start = controller.now
    original_wait = controller.wait

    def wait(seconds):
        original_wait(seconds)
        if controller.now >= start + .4:
            if interrupt == "stop":
                raise Stopped()
            if interrupt == "pause":
                controller.pause_generation += 1
            else:
                support["support_hp"] = None

    controller.wait = wait
    if interrupt == "stop":
        with pytest.raises(Stopped):
            duo.cast_summon()
    else:
        duo.cast_summon()
        assert (1, "ESC") in controller.events
    assert controller.now < start + 20
    assert not duo.summon_casting
    assert (2, "F1") not in controller.events


def test_start_without_panel_fails_before_input():
    duo, controller = make_duo(summon_key="NUM6")
    with pytest.raises(RuntimeError, match="панель самона"):
        duo.run()
    assert controller.events == []


def test_search_hook_finishes_recovery_before_next_target(monkeypatch):
    duo, controller, frame, _, sent = recovery_duo()
    appear_after_cast(duo, controller, frame, sent)
    monkeypatch.setattr(MeleeFarmer, "before_target_selection", Mock())
    duo.before_target_selection()
    assert not duo.summon_recovery.pending
    assert not duo.buffs_due()
    assert sum(key == "NUM6" for _, _, key in sent) == 1


def test_fast_fight_finishes_current_mob_after_panther_death_without_switching():
    duo, controller, frame, _, _ = recovery_duo(1., experimental_fast_farm=True)
    duo.observe()
    targets = iter((.05, .02, 0., 0., 0., 0.))

    def capture():
        frame[:] = summon_scene(0.)[0]
        return 1., next(targets, 0.), False

    controller.capture = capture
    duo.fight(.8)
    assert duo.summon_recovery.pending
    assert (1, duo.config.attack) in controller.events
    assert (1, duo.config.next_target) not in controller.events
    assert (1, "NUM6") not in controller.events


def test_support_damage_during_quiet_wait_defends_and_restarts_interval():
    duo, controller, frame, support, sent = recovery_duo()
    fight_ended = []
    target = [None]
    appear_after_cast(duo, controller, frame, sent)
    original_capture = controller.capture

    def capture():
        original_capture()
        if controller.now >= 1 and not fight_ended:
            support["support_hp"] = .8
            target[0] = .7
        return 1., target[0], False

    def fight(hp):
        assert hp == .7
        assert (1, duo.config.attack) in controller.events
        assert (1, "NUM6") not in controller.events
        controller.wait(.3)
        fight_ended.append(controller.now)
        duo.attacking = False
        target[0] = None

    controller.capture = capture
    duo.fight = fight
    duo.service_summon()
    first_cast = next(at for at, _, key in sent if key == "NUM6")
    assert fight_ended and first_cast >= fight_ended[0] + duo.config.quiet_seconds


def test_small_hp_noise_does_not_block_cast_with_existing_damage_threshold():
    duo, controller, frame, support, sent = recovery_duo()
    appear_after_cast(duo, controller, frame, sent)
    capture = controller.capture

    def noisy_capture():
        _, _, target = capture()
        support["support_hp"] = .999 if int(controller.now * 5) % 2 else 1.
        return support["support_hp"], target, False

    controller.capture = noisy_capture
    duo.service_summon()
    assert sum(key == "NUM6" for _, _, key in sent) == 1
    assert (1, duo.config.attack) not in controller.events


@pytest.mark.parametrize("mp", [None, .08, .09, .1, .59])
def test_low_support_mana_releases_farm_and_rebuffs_in_order(mp):
    duo, controller, frame, support, sent = recovery_duo(
        buff1_key="F8", buff2_key="F7", buff2_cast_seconds=1,
        support_mass_buff_enabled=True, experimental_fast_farm=True)
    appear_after_cast(duo, controller, frame, sent)
    support["support_mp"] = mp
    duo.service_summon()
    assert controller.now < 30
    assert not duo.summon_recovery.pending and not duo.summon_recovery.rebuff
    assert duo.allow_fast_target_switch()
    assert duo.post_summon_mass_buff
    assert (2, "F1") not in controller.events

    # Недостаточное или неизвестное MP не сбрасывает очередь и не мешает селфам МА.
    support["support_mp"] = .08
    duo.cast_buffs()
    duo.cast_buffs()
    assert (1, "F8") in controller.events and (1, "F7") in controller.events
    assert duo.mass_buff_index == 0
    keys = ["NUM3", "NUM4", "NUM5", "NUM6", "NUM7", "NUM8", "NUM9", "NUM0",
            "NUMDIVIDE", "NUMMULTIPLY"]
    for index, key in enumerate(keys):
        support["support_mp"] = .09
        duo.cast_buffs()
        assert controller.events.count((2, key)) == 2
        controller.wait(duo.config.support_mass_buff_cast_seconds)
        support["support_mp"] = None if index % 2 else .08
        before = list(controller.events)
        duo.cast_buffs()
        assert controller.events == before
        if index < len(keys) - 1:
            assert duo.mass_buff_index == index + 1
            assert duo.next_buff < controller.now
    assert not duo.post_summon_mass_buff
    assert not duo.buffs_due()


def test_critical_party_health_waits_for_recovery_without_wasting_attempts():
    duo, controller, frame, support, sent = recovery_duo()
    appear_after_cast(duo, controller, frame, sent)
    capture = controller.capture

    def wounded_capture():
        support["support_hp"] = .3 if controller.now < 6 else 1.
        return capture()

    controller.capture = wounded_capture
    duo.service_summon()
    casts = [at for at, _, key in sent if key == "NUM6"]
    assert len(casts) == 1 and casts[0] >= 6


def test_deferred_rebuff_keeps_fighting_without_support_mana():
    duo, controller, frame, support, sent = recovery_duo(support_mass_buff_enabled=True)
    support["support_mp"] = .08
    appear_after_cast(duo, controller, frame, sent)
    duo.service_summon()
    started = controller.now
    controller.capture = lambda: (1., .8 if controller.now < started + 3 else 0., False)
    duo.fight(.8)
    assert (1, duo.config.attack) in controller.events
    assert not any(role == 2 and key.startswith("NUM") for _, role, key in sent)
    assert duo.post_summon_mass_buff
    assert duo.mass_buff_index == 0


def test_pause_restarts_deferred_rebuff_at_single_buff_mana_threshold():
    duo, controller, frame, support, sent = recovery_duo(support_mass_buff_enabled=True)
    support["support_mp"] = .09
    appear_after_cast(duo, controller, frame, sent)
    duo.service_summon()
    duo.cast_buffs()
    assert duo.mass_buff_index == 1
    controller.pause_generation += 1
    controller.wait(duo.config.support_mass_buff_cast_seconds)
    duo.cast_buffs()
    assert controller.events.count((2, "NUM3")) == 4
    assert duo.mass_buff_index == 1
    assert duo.post_summon_mass_buff


def test_preexisting_support_macro_does_not_replace_new_panther_rebuff():
    duo, controller, _, _, sent = recovery_duo(1.)
    duo.summon_recovery.pending = True
    duo.buffing = True
    duo.support_buff_until = 100.
    duo.support_buff_started = -5.
    duo.support_buff_stage = 1
    duo.service_summon()
    keys = [(role, key) for _, role, key in sent]
    assert keys.index((2, "ESC")) < keys.index((2, "F1"))
    assert (1, "F8") in keys
    assert (1, "NUM6") not in keys


def test_second_death_during_rebuff_cannot_release_search(monkeypatch):
    duo, controller, frame, _, sent = recovery_duo(1.)
    duo.summon_recovery.pending = True
    buff_calls = []

    def interrupted_buffs():
        buff_calls.append(controller.now)
        frame[:] = summon_scene(0.)[0]
        duo.track_damage(None, 1.)
        duo.next_buff = float("inf")
        duo.buff_ready = {slot: float("inf") for slot in duo.buff_ready}

    monkeypatch.setattr(duo, "cast_buffs", interrupted_buffs)
    with pytest.raises(RuntimeError, match="10 попыток"):
        duo.service_summon()
    assert len(buff_calls) == 1
    assert sum(key == "NUM6" for _, _, key in sent) == 10
    assert duo.summon_recovery.pending


def test_pause_during_cast_observation_cannot_confirm_success():
    duo, controller, frame, _, _ = recovery_duo()
    for _ in range(16):
        duo.observe()
        controller.wait(.2)
    original = duo.observe

    def pause_observation():
        controller.pause_generation += 1
        frame[:] = summon_scene()[0]
        return original()

    duo.observe = pause_observation
    duo.cast_summon()
    assert (1, "ESC") in controller.events
    assert duo.summon_recovery.pending
    assert (2, "F1") not in controller.events


def test_missing_panther_after_support_visit_casts_and_rebuffs_before_search():
    duo, controller, frame, _, sent = recovery_duo(1., experimental_fast_farm=True)
    duo.observe()
    # Пантера исчезла за время визита к саппорту: нуля и последнего урона не было.
    controller.wait(3.)
    frame[:] = 20
    for _ in range(12):
        duo.observe()
        controller.wait(.2)
    assert duo.summon_recovery.pending
    assert not duo.allow_fast_target_switch()
    appear_after_cast(duo, controller, frame, sent)
    duo.service_summon()
    summon = next(at for at, role, key in sent if role == 1 and key == "NUM6")
    assert next(at for at, role, key in sent if role == 2 and key == "F1") >= summon + 20
    assert not duo.summon_recovery.pending and not duo.summon_recovery.rebuff
    assert not duo.buffs_due()
    assert (1, duo.config.next_target) not in controller.events


def test_new_panther_restarts_full_rebuff_after_partial_mass_buff_series():
    duo, controller, frame, _, sent = recovery_duo(support_mass_buff_enabled=True)
    duo.mass_buff_active = True
    duo.mass_buff_index = 7
    duo.mass_buff_ready = 100.
    appear_after_cast(duo, controller, frame, sent)
    duo.service_summon()
    assert (2, "F1") in controller.events
    assert (2, "NUM0") not in controller.events
    assert duo.mass_buff_index == 0 and not duo.mass_buff_active
    assert not duo.summon_recovery.rebuff
    assert not duo.buffs_due()


def test_disappeared_party_row_allows_cast_and_full_rebuff():
    duo, controller, frame, support, sent = recovery_duo()
    support.update(party_3_hp=.8, party_3_cp=.5)
    duo.observe()
    del support["party_3_hp"]
    del support["party_3_cp"]
    appear_after_cast(duo, controller, frame, sent)
    duo.service_summon()
    assert sum(key == "NUM6" for _, _, key in sent) == 1
    assert (2, "F1") in controller.events
    assert not duo.summon_recovery.pending and not duo.summon_recovery.rebuff


def test_unreadable_support_stops_with_reason_instead_of_waiting_for_hours():
    duo, controller, _, support, sent = recovery_duo()
    support["support_hp"] = None
    with pytest.raises(RuntimeError, match="180 с.*support_hp"):
        duo.service_summon()
    assert 180 <= controller.now < 181
    assert not any(key == "NUM6" for _, _, key in sent)
    assert (1, duo.config.next_target) not in controller.events
    assert not duo.summon_servicing
    assert any("support_hp" in call.args[0] and "Ожидание" in call.args[0]
               for call in duo.notify.call_args_list)


def test_rebuff_with_persistently_low_mana_also_has_stall_limit():
    duo, controller, frame, support, sent = recovery_duo()
    support["support_mp"] = .1
    appear_after_cast(duo, controller, frame, sent)
    with pytest.raises(RuntimeError, match="без прогресса.*MP саппорта"):
        duo.service_summon()
    assert controller.now < 230
    assert sum(key == "NUM6" for _, _, key in sent) == 1
    assert duo.summon_recovery.rebuff
    assert (2, "F1") not in controller.events


def test_summon_wait_runs_existing_no_kill_escape_before_cast(monkeypatch):
    duo, controller, frame, _, sent = recovery_duo()
    duo.kill_watch_at = -121.
    duo.kill_watch_attempts = 2
    escapes = []

    def escape(name, *, blocked_names=None):
        assert duo.summon_defending
        assert not any(key == "NUM6" for _, _, key in sent)
        escapes.append(controller.now)
        controller.key("W", 1500)
        controller.wait(1.5)
        return None

    monkeypatch.setattr(duo, "escape_from_unreachable", escape)
    appear_after_cast(duo, controller, frame, sent)
    duo.service_summon()
    assert len(escapes) == 1
    summon = next(at for at, role, key in sent if role == 1 and key == "NUM6")
    assert summon >= escapes[0] + 1.5 + duo.config.quiet_seconds
    assert not duo.summon_defending and not duo.summon_servicing


def test_long_configured_buff_macro_is_not_a_stall():
    duo, controller, frame, _, sent = recovery_duo()
    duo.config.buff_seconds = 200
    appear_after_cast(duo, controller, frame, sent)
    duo.service_summon()
    assert controller.now > 200
    assert not duo.summon_recovery.rebuff and not duo.buffs_due()


@pytest.mark.parametrize("mp,allowed", [(None, False), (0., False), (.099, False),
                                        (.1, True), (.11, True)])
def test_each_cast_requires_at_least_ten_percent_fresh_ma_mp(mp, allowed):
    duo, controller, frame, _, sent = recovery_duo(summon_cast_seconds=1)
    for _ in range(16):
        duo.observe()
        controller.wait(.2)
    duo.last_mp = mp
    appear_after_cast(duo, controller, frame, sent)
    duo.cast_summon()
    assert sum(key == "NUM6" for _, _, key in sent) == int(allowed)
    assert duo.summon_recovery.attempts == int(allowed)


@pytest.mark.parametrize("stale", ["time", "pause"])
def test_old_mp_does_not_authorize_summon(stale):
    duo, controller, _, _, sent = recovery_duo()
    for _ in range(16):
        duo.observe()
        controller.wait(.2)
    if stale == "time":
        controller.wait(2.)
    else:
        controller.pause_generation += 1
    duo.cast_summon()
    assert not any(key == "NUM6" for _, _, key in sent)
    assert duo.summon_recovery.attempts == 0


def test_second_attempt_waits_for_mp_after_first_cast_spends_it():
    duo, controller, frame, _, sent = recovery_duo(summon_cast_seconds=1)
    appear_after_cast(duo, controller, frame, sent, attempt=2)
    read = duo.profile.read

    def read_mp(image, name):
        if name == "mp":
            casts = sum(key == "NUM6" for _, _, key in sent)
            return .09 if casts == 1 and controller.now < 12 else 1.
        return read(image, name)

    duo.profile.read = read_mp
    duo.service_summon()
    starts = [at for at, _, key in sent if key == "NUM6"]
    assert len(starts) == 2
    assert starts[1] >= 12
    assert starts[1] - starts[0] >= duo.config.summon_cooldown


def test_known_low_mp_can_regenerate_longer_than_stall_timeout_without_attempts():
    duo, controller, frame, _, sent = recovery_duo(summon_cast_seconds=1)
    appear_after_cast(duo, controller, frame, sent)
    read = duo.profile.read
    def read_mp(image, name):
        if name == "mp":
            return .09 if controller.now < 190 else 1.
        return read(image, name)

    duo.profile.read = read_mp
    duo.service_summon()
    starts = [at for at, _, key in sent if key == "NUM6"]
    assert len(starts) == 1 and starts[0] >= 190
    assert any("MP МА 9.0%" in call.args[0] for call in duo.notify.call_args_list)
    assert (1, duo.config.next_target) not in controller.events
    assert (1, duo.config.attack) not in controller.events


def test_low_mp_does_not_disable_timeout_for_missing_party_observation():
    duo, controller, _, support, sent = recovery_duo()
    support["support_hp"] = None
    read = duo.profile.read
    duo.profile.read = lambda image, name: .09 if name == "mp" else read(image, name)
    with pytest.raises(RuntimeError, match="180 с.*support_hp"):
        duo.service_summon()
    assert not any(key == "NUM6" for _, _, key in sent)
    assert duo.summon_recovery.attempts == 0


def test_low_mp_defense_uses_autoattack_without_ma_skills():
    duo, controller, _, support, sent = recovery_duo(
        hp=0.,
        aggro_key="2", damage_keys=("F3",), overhit_keys=("F5",),
        vampiric_key="F6", ud_key="F12",
    )
    read = duo.profile.read
    duo.profile.read = lambda image, name: .09 if name == "mp" else read(image, name)
    duo.observe()
    controller.wait(.2)
    support["support_hp"] = .2
    controller.capture = lambda: (.3, .8 if controller.now < 2 else 0., False)
    duo.observe()
    assert duo.summon_recovery.pending
    duo.fight(.8)
    duo.cast_summon()
    ma_keys = [key for _, role, key in sent if role == 1]
    assert duo.config.attack in ma_keys
    forbidden = {"NUM6", "2", "F3", "F5", "F6", "F12", duo.config.mass_aggro_key}
    assert not forbidden.intersection(ma_keys)
    assert duo.summon_recovery.attempts == 0


def test_summon_mana_reserve_blocks_skills_until_panther_returns():
    duo, controller, _, _, sent = recovery_duo(aggro_key="2", damage_keys=("F3",),
                                               overhit_keys=("F5",), vampiric_key="F6")
    duo.observe()
    duo.summon_recovery.pending = True
    duo.under_attack = True
    duo.open_with_aggro()
    for key in ("2", "F3", "F5", "F6"):
        assert not duo.combat_key(key, 100)
    assert sent == []
    duo.summon_recovery.checked = True
    duo.summon_recovery.pending = False
    assert duo.combat_key("F3", 100)
    assert (1, "F3") in controller.events


def test_panther_appearing_on_tenth_attempt_is_rebuffed_without_stop():
    duo, controller, frame, _, sent = recovery_duo(summon_cast_seconds=1, summon_cooldown=1)
    appear_after_cast(duo, controller, frame, sent, attempt=10)
    duo.service_summon()
    assert sum(key == "NUM6" for _, _, key in sent) == 10
    assert not duo.summon_recovery.pending and not duo.summon_recovery.rebuff
    assert (2, "F1") in controller.events
    assert (1, "F8") in controller.events
