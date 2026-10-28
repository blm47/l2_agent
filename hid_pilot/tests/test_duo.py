from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import numpy as np
from pydantic import ValidationError

from hid_pilot.bars import BarProfile, BarBox
from hid_pilot.controller import ActionController, Stopped
from hid_pilot.desktop import ActivationDenied, FocusLost, Target
from hid_pilot.duo import DuoConfig, DuoFarmer, Rebuffed, prepare_duo, load_duo_party
from hid_pilot.party import PartyProfile
from hid_pilot.farm_melee import MeleeConfig, MeleeFarmer


FARMER = Target(hwnd=1, pid=11, title="МА", rect=(0, 0, 100, 100))
SUPPORT = Target(hwnd=2, pid=22, title="Саппорт", rect=(100, 0, 200, 100))


class FakeController:
    def release_inputs(self):
        pass

    def __init__(self):
        self.transport = SimpleNamespace(protocol="HID_PILOT_V4")
        self.now = 0.
        self.pause_generation = 0
        self.role = FARMER
        self.events = []
        self.frames = iter([(1., .9, False)])
        self.current = (1., .9, False)

    def active_time(self):
        return self.now

    def checkpoint(self):
        pass

    def capture(self):
        assert self.role == FARMER
        self.current = next(self.frames, self.current)
        return self.current

    def switch_window(self, target):
        self.role = target
        self.events.append(("window", target.hwnd))

    def key(self, key, duration, **kwargs):
        self.events.append((self.role.hwnd, key))

    def wait(self, seconds):
        self.now += seconds


def make_duo(**values):
    # Старые сценарии изолированы от нового отдельного расходника.
    values.setdefault("hp_elixir_key", None)
    values.setdefault("support_hp_elixir_key", None)
    values.setdefault("buff_delay_minutes", 0)
    values.setdefault("aggro_key", None)
    values.setdefault("pre_attack_loot", False)
    values.setdefault("heal_key", "F4")
    controller = FakeController()
    profile = SimpleNamespace(
        read=lambda image, name: 1. if name == "mp" else image[0 if name == "hp" else 1],
        dead_marker_visible=lambda image: image[2],
    )
    config = DuoConfig(cycles=1, buff_seconds=1, humanize=False, **values)
    duo = DuoFarmer(controller, FARMER, SUPPORT, profile, config, Mock())
    original_track = duo.track_damage

    def track(image, hp):
        original_track(image, hp)
        if duo.party_profile is None:
            # Старые сценарии тестов моделируют саппорта с полным MP.
            duo.previous_resources["support_mp"] = 1.

    duo.track_damage = track
    return duo, controller


def start_ma_attack(duo, controller, target_hp=1.):
    # Подготовка живой цели и реальная команда МА; последующие кадры моделируют урон.
    capture = controller.capture
    pending = duo.support_pending
    duo.support_pending = False
    controller.capture = lambda: (1., target_hp, False)
    duo.observe_ma()
    duo.combat_key(duo.config.attack, 100)
    controller.capture = capture
    duo.support_pending = pending


def test_duo_inherits_melee_algorithms_and_configuration():
    assert issubclass(DuoConfig, MeleeConfig)
    assert issubclass(DuoFarmer, MeleeFarmer)
    for method in ("acquire", "search_cycle", "search_known_target", "fight", "verify_target",
                   "recover", "use_damage_skill", "run_cycle"):
        assert getattr(DuoFarmer, method) is getattr(MeleeFarmer, method)


def test_legacy_duo_settings_migrate_without_losing_user_keys():
    legacy = {"buff_key": "F11", "follow_key": "F10", "support_key": "F10", "heal_key": "F9",
              "heal_seconds": 2, "next_target_key": "6", "attack_key": "3", "loot_key": "C",
              "defense_target_key": "2", "target_seconds": .8, "combat_seconds": 90,
              "loot_seconds": .9, "max_seconds": 10800, "cycles": 1000}
    config = DuoConfig.model_validate(legacy)
    for key in ("buff_key", "follow_key", "support_key", "heal_key", "heal_seconds",
                "defense_target_key", "max_seconds", "cycles"):
        assert getattr(config, key) == legacy[key]
    assert (config.next_target, config.attack, config.loot) == ("6", "3", "C")
    assert (config.target_delay, config.combat_timeout, config.loot_delay) == (.8, 90, .9)
    assert config.max_hours == 3
    assert DuoConfig.model_validate_json(config.model_dump_json()) == config
    assert "attack_key" not in config.model_dump()


def test_missing_support_heal_key_disables_healing_by_default():
    config = DuoConfig.model_validate_json('{}')
    assert config.heal_key is None
    assert DuoConfig.model_validate_json(config.model_dump_json()).heal_key is None


@pytest.mark.parametrize("key", [None, "", "   "])
def test_disabled_support_heal_does_not_switch_windows_or_exhaust_attempts(key):
    farmer, controller = make_duo(heal_key=key)
    controller.frames = iter([(.2, .9, False)])
    farmer.heals = farmer.config.heal_attempts
    for _ in range(3):
        assert farmer.protect() == (.9, False)
        controller.wait(farmer.config.heal_cooldown)
    assert farmer.config.heal_key is None
    assert controller.events == []
    assert farmer.last_heal == float("-inf")


def test_conflicting_legacy_and_canonical_setting_is_rejected():
    with pytest.raises(ValidationError, match="разные значения"):
        DuoConfig(attack_key="3", attack="R")


def test_inherited_fight_repeats_attack_but_assists_only_once():
    duo, controller = make_duo(attack_delay=.5)
    controller.capture = lambda: (1., 0. if controller.now >= 2.2 else .8, False)
    duo.fight(.9)
    assert controller.events.count((1, "R")) > 1
    assert controller.events.count((2, "F3")) == 1
    assert duo.training_kills == 1
    assert controller.events.count((1, "5")) == 1
    assert controller.role == FARMER


def test_two_immediate_targets_do_not_enable_follow_between_kills():
    farmer, controller = make_duo()
    farmer.config = farmer.config.model_copy(update={"cycles": 2})
    live = [False]
    original_key = controller.key

    def key(name, duration, **kwargs):
        original_key(name, duration)
        if controller.role == FARMER and name == farmer.config.next_target:
            live[0] = True
        if controller.role == SUPPORT and name == farmer.config.support_key:
            live[0] = False

    controller.key = key
    controller.capture = lambda: (1., (.8 if farmer.attacking else .9) if live[0] else 0., False)
    farmer.run()
    assert farmer.training_kills == 2
    # Следование после стартового баффа сохраняется, между боями его нет.
    assert controller.events.count((2, farmer.config.follow_key)) == 1
    assert controller.events.count((2, farmer.config.support_key)) == 2
    first_loot = controller.events.index((1, farmer.config.loot))
    assert controller.events[first_loot + 1] == (1, farmer.config.next_target)


def test_missing_target_enables_follow_once_before_search_movement():
    farmer, controller = make_duo(search_moves=1, search_cycles=1)
    controller.capture = lambda: (1., None, False)
    with pytest.raises(RuntimeError, match="не нашли мобов"):
        farmer.acquire()
    assert controller.events.count((2, farmer.config.follow_key)) == 1
    follow_at = controller.events.index((2, farmer.config.follow_key))
    movement_at = next(index for index, event in enumerate(controller.events)
                       if event[0] == 1 and event[1] in "WASD")
    assert follow_at < movement_at
    assert (2, farmer.config.support_key) not in controller.events


def test_target_found_after_search_switches_from_follow_to_attack_help():
    farmer, controller = make_duo(search_moves=1, search_cycles=1)
    selections = [0]
    dead = [False]
    original_key = controller.key

    def key(name, duration, **kwargs):
        original_key(name, duration)
        if controller.role == FARMER and name == farmer.config.next_target:
            selections[0] += 1
        if controller.role == SUPPORT and name == farmer.config.support_key:
            dead[0] = True

    controller.key = key
    controller.capture = lambda: (1., 0. if dead[0] else
                                 (.8 if farmer.attacking else .9) if selections[0] >= 2 else None, False)
    farmer.fight()
    assert controller.events.count((2, farmer.config.follow_key)) == 1
    assert controller.events.count((2, farmer.config.support_key)) == 1
    assert controller.events.index((2, farmer.config.follow_key)) < controller.events.index((1, farmer.config.attack))
    assert controller.events.index((1, farmer.config.attack)) < controller.events.index((2, farmer.config.support_key))
    assert not farmer.following


def test_opening_overhit_also_assists_once():
    duo, controller = make_duo(overhit_keys=("2",))
    controller.capture = lambda: (1., 0. if (2, "F3") in controller.events else .007, False)
    duo.fight(.01)
    assert controller.events.index((1, "2")) < controller.events.index((2, "F3"))
    assert controller.events.count((2, "F3")) == 1
    assert duo.training_kills == 1


def test_no_assist_when_opening_overhit_already_killed_target():
    duo, controller = make_duo(overhit_keys=("2",))
    controller.capture = lambda: (1., 0., False)
    duo.fight(.01)
    assert (1, "2") in controller.events
    assert (2, "F3") not in controller.events
    assert duo.training_kills == 1


def test_due_rebuff_can_start_during_real_melee_fight_without_losing_loot():
    duo, controller = make_duo()
    duo.next_buff = .1
    controller.capture = lambda: (1., 0. if controller.now >= 2 else .9, False)
    duo.fight(.9)
    assert (2, "F1") in controller.events
    assert (1, "5") in controller.events
    assert not duo.support_pending
    assert controller.events.index((2, "F1")) < controller.events.index((1, "5"))


def test_dead_farmer_uses_inherited_resurrection_wait(monkeypatch):
    duo, controller = make_duo()
    controller.frames = iter([(0., .9, False)])
    resurrection = Mock(side_effect=Stopped("Ожидание resurrection"))
    monkeypatch.setattr("hid_pilot.farm.wait_for_resurrection", resurrection)
    with pytest.raises(Stopped, match="resurrection"):
        duo.observe()
    resurrection.assert_called_once()
    assert controller.events == []


def test_full_cycle_returns_to_farmer_and_loots_only_after_death():
    duo, controller = make_duo()
    controller.capture = lambda: (1., 0. if (2, "F3") in controller.events else
                                 .8 if duo.attacking else .9, False)
    duo.run()
    keys = [event for event in controller.events if isinstance(event[0], int)]
    assert keys == [(2, "F1"), (2, "F2"), (1, "3"), (1, "R"), (2, "F3"),
                    (1, "5")]
    assert controller.role == FARMER
    for index, event in enumerate(controller.events):
        if event == ("window", 2):
            assert controller.events[index + 2] == ("window", 1)


def test_real_cycle_heals_before_support_and_observes_death_before_loot():
    duo, controller = make_duo()

    def capture():
        attacked = (1, "R") in controller.events
        healed = (2, "F4") in controller.events
        assisted = (2, "F3") in controller.events
        return (.4 if attacked and not healed else .9, 0. if assisted else .8 if attacked else .9, assisted)

    controller.capture = capture
    duo.run()
    assert controller.events.index((1, "R")) < controller.events.index((2, "F4"))
    assert controller.events.index((2, "F4")) < controller.events.index((2, "F3"))
    assert controller.events.index((2, "F3")) < controller.events.index((1, "5"))
    assert controller.role == FARMER


def test_buff_waits_without_attack_when_hp_is_low_but_not_falling():
    duo, controller = make_duo()
    controller.frames = iter([(.7, .9, False)])
    duo.observe()
    duo.buff()
    assert (1, "R") not in controller.events
    assert (1, "3") not in controller.events
    assert (2, "F3") not in controller.events
    assert (2, "F1") in controller.events
    assert controller.events[-2:] == [(2, "F2"), ("window", 1)]
    assert duo.next_buff == duo.support_buff_started + 19 * 60


def test_damage_before_buff_starts_attack_but_healthy_ma_allows_buff():
    duo, controller = make_duo(damage_memory_seconds=.2)
    controller.frames = iter([(.9, .9, False), (.8, .9, False)])
    duo.observe()
    duo.buff()
    assert controller.events.count((1, "R")) == 1
    assert (2, "F1") in controller.events
    assert controller.events.index((1, "R")) < controller.events.index((2, "F1"))
    assert (1, "ESC") not in controller.events
    assert duo.next_buff == 0


def test_rebuff_runs_at_configured_active_time_boundary():
    duo, controller = make_duo(rebuff_minutes=7)
    duo.observe()
    duo.buff()
    assert duo.next_buff == duo.support_buff_started + 7 * 60
    controller.now = duo.next_buff - .01
    duo.protect()
    assert controller.events.count((2, "F1")) == 1
    controller.now = duo.next_buff
    duo.protect()
    assert controller.events.count((2, "F1")) == 1
    duo.cast_buffs()
    assert controller.events.count((2, "F1")) == 2
    assert duo.next_buff == duo.support_buff_started + 7 * 60


def test_rebuff_restarts_interrupted_cycle():
    duo, controller = make_duo()
    buff = Mock()
    duo.buff = buff

    def cycle(number):
        if cycle_mock.call_count == 1:
            raise Rebuffed

    cycle_mock = Mock(side_effect=cycle)
    duo.run_cycle = cycle_mock
    duo.run()
    assert cycle_mock.call_count == 2
    assert [call.args for call in cycle_mock.call_args_list] == [(0,), (0,)]


@pytest.mark.parametrize("gap,pause", [(2, False), (.2, True)])
def test_damage_baseline_is_invalidated_after_gap_or_pause(gap, pause):
    duo, controller = make_duo()
    duo.track_damage(None, .9)
    controller.wait(gap)
    controller.pause_generation += int(pause)
    duo.track_damage(None, .7)
    assert controller.active_time() >= duo.damage_until


@pytest.mark.parametrize("resource", ["hp", "cp"])
def test_support_damage_is_read_from_selected_party_row(resource):
    duo, controller = make_duo(support_party_slot=2)
    box = lambda y1, y2: BarBox(x1=.1, x2=.9, y1=y1 / 800, y2=y2 / 800)
    party = PartyProfile(width=200, height=800, name=box(0, 20), cp=box(22, 26),
                         hp=box(28, 34), mp=box(36, 42), row_step=80)
    duo.party_profile = party
    duo.profile = BarProfile(width=200, height=800)
    image = np.zeros((800, 200, 3), dtype=np.uint8)
    image[102:106, 20:180] = (0, 150, 220)
    image[108:114, 20:180] = (0, 0, 180)
    duo.track_damage(image, .9)
    controller.wait(.2)
    y1, y2 = (108, 114) if resource == "hp" else (102, 106)
    image[y1:y2, 100:180] = 0
    duo.track_damage(image, .9)
    assert (controller.active_time() < duo.damage_until) == (resource == "hp")


def test_farmer_cp_drop_triggers_damage_even_with_unchanged_hp():
    duo, controller = make_duo()
    duo.profile = BarProfile(width=200, height=100, bars={
        "cp": BarBox(x1=.1, x2=.9, y1=.1, y2=.15)})
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[10:15, 20:180] = (0, 150, 220)
    duo.track_damage(image, .9)
    controller.wait(.2)
    image[10:15, 100:180] = 0
    duo.track_damage(image, .9)
    assert controller.active_time() < duo.damage_until


def test_unknown_cp_breaks_comparison_without_inventing_damage():
    duo, controller = make_duo()
    duo.profile = BarProfile(width=200, height=100, bars={
        "cp": BarBox(x1=.1, x2=.9, y1=.1, y2=.15)})
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[10:15, 20:180] = (0, 150, 220)
    duo.track_damage(image, .9)
    controller.wait(.2)
    image[:] = 0
    duo.track_damage(image, .9)
    controller.wait(.2)
    image[10:15, 20:100] = (0, 150, 220)
    duo.track_damage(image, .9)
    assert controller.active_time() >= duo.damage_until


def test_missing_party_profile_is_rejected_before_input(tmp_path):
    with pytest.raises(ValueError, match="Настройте пати"):
        load_duo_party(tmp_path, FARMER)


def test_heal_has_priority_and_uses_new_frame_after_return():
    duo, controller = make_duo()
    controller.frames = iter([(.59, .8, False), (.8, 0., True)])
    assert duo.protect() == (0., True)
    assert controller.events == [("window", 2), (2, "F4"), ("window", 1)]
    assert controller.role == FARMER


def test_heal_cooldown_and_attempt_limit():
    duo, controller = make_duo(heal_attempts=1)
    controller.frames = iter([(.4, .8, False)])
    duo.protect()
    duo.protect()
    assert controller.events.count((2, "F4")) == 1
    controller.wait(2)
    with pytest.raises(RuntimeError, match="лимита попыток"):
        duo.protect()


def test_unknown_farmer_hp_uses_base_observation_retries():
    duo, controller = make_duo(observation_attempts=2)
    controller.frames = iter([(None, .8, False)])
    with pytest.raises(RuntimeError):
        duo.protect()
    assert controller.events == []


def test_threshold_is_strictly_below_sixty_percent():
    duo, controller = make_duo()
    controller.frames = iter([(.6, .8, False)])
    duo.protect()
    assert controller.events == []


def test_unknown_target_never_becomes_death_or_loot():
    duo, controller = make_duo(combat_seconds=1)
    controller.capture = lambda: (1., None if (2, "F3") in controller.events else .9, False)
    duo.fight(.9)
    assert (1, "5") not in controller.events


def test_search_has_attempt_limit_without_attack():
    duo, controller = make_duo(search_moves=1, search_cycles=1)
    controller.frames = iter([(1., None, False)])
    with pytest.raises(RuntimeError, match="не нашли мобов"):
        duo.acquire()
    assert controller.events.count((1, "3")) == 2
    assert (1, "R") not in controller.events


def test_paused_or_stale_observation_is_rejected():
    duo, controller = make_duo()

    def read(image, name):
        controller.pause_generation += 1
        return .8

    duo.profile.read = read
    with pytest.raises(RuntimeError, match="HP/MP не распознаны"):
        duo.observe()


def test_stop_during_support_does_not_steal_focus_back():
    duo, controller = make_duo()
    controller.key = Mock(side_effect=Stopped("STOP"))
    with pytest.raises(Stopped):
        duo.visit("F4", .5)
    assert controller.events == [("window", 2)]


@pytest.mark.parametrize("values", [{"heal_key": "ALT+F4"}, {"heal_threshold": float("nan")},
                                    {"rebuff_minutes": 0}, {"rebuff_minutes": .1},
                                    {"support_party_slot": 0}, {"support_party_slot": 9},
                                    {"support_seconds": 15}, {"max_seconds": 0}])
def test_invalid_configuration(values):
    with pytest.raises(ValidationError):
        DuoConfig(**values)


def test_prepare_requires_distinct_windows_and_farmer_profile(tmp_path):
    with pytest.raises(ValueError, match="разных"):
        prepare_duo(tmp_path, FARMER, FARMER)
    (tmp_path / "farmer").mkdir()
    path = tmp_path / "farmer" / "farm-bars.json"
    path.write_text(BarProfile(width=100, height=100).model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="HP/MP МА"):
        prepare_duo(tmp_path, FARMER, SUPPORT)


def switch_controller(monkeypatch):
    source = Mock(target=FARMER)
    destination = Mock(target=SUPPORT)
    transport = Mock()
    controller = ActionController(source, transport, lambda: True)
    monkeypatch.setattr("hid_pilot.controller.snapshot", lambda hwnd: SUPPORT)
    monkeypatch.setattr("hid_pilot.controller.activate", Mock())
    monkeypatch.setattr("hid_pilot.controller.Desktop", lambda target: destination)
    monkeypatch.setattr("hid_pilot.controller.request_activation",
                        Mock(side_effect=RuntimeError("Не удалось подтвердить фокус второго окна")))
    return controller, destination, transport


def test_switch_releases_before_focus_and_arms_only_after_check(monkeypatch):
    controller, destination, transport = switch_controller(monkeypatch)
    order = []
    transport.stop.side_effect = lambda: order.append("stop")
    destination.check.side_effect = lambda: order.append("check")
    transport.arm.side_effect = lambda: order.append("arm")
    controller.switch_window(SUPPORT)
    assert order == ["stop", "check", "check", "arm"]
    assert controller.desktop is destination


def test_failed_focus_stops_without_arm(monkeypatch):
    controller, destination, transport = switch_controller(monkeypatch)
    destination.check.side_effect = FocusLost("фокус не подтверждён")
    with pytest.raises(RuntimeError, match="подтвердить фокус"):
        controller.switch_window(SUPPORT, timeout=.001)
    assert controller.stopped.is_set()
    transport.arm.assert_not_called()


def test_activation_denial_uses_helper_without_keyboard_events(monkeypatch):
    controller, destination, transport = switch_controller(monkeypatch)
    transport.protocol = "HID_PILOT_V4"
    request = Mock(side_effect=ActivationDenied("Windows отклонила фокус"))
    monkeypatch.setattr("hid_pilot.controller.activate", request)
    helper = Mock()
    monkeypatch.setattr("hid_pilot.controller.request_activation", helper)
    controller.switch_window(SUPPORT)
    assert request.call_count == 1
    assert controller.desktop is destination
    helper.assert_called_once()
    assert helper.call_args.args[:2] == (FARMER, SUPPORT)
    transport.hold.assert_not_called()
    transport.arm.assert_called_once_with()
    transport.stop.assert_called_once_with()


def test_failed_activation_retry_stops_without_destination_arm(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    transport.protocol = "HID_PILOT_V4"
    request = Mock(side_effect=ActivationDenied("Windows отклонила фокус"))
    monkeypatch.setattr("hid_pilot.controller.activate", request)
    with pytest.raises(RuntimeError, match="подтвердить фокус"):
        controller.switch_window(SUPPORT)
    assert request.call_count == 1
    assert controller.stopped.is_set()
    assert controller.desktop.target == FARMER
    transport.arm.assert_not_called()


def test_window_activation_succeeds_on_first_attempt_without_reset(monkeypatch):
    controller, destination, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.activate", Mock(side_effect=ActivationDenied("отказ")))
    helper = Mock()
    monkeypatch.setattr("hid_pilot.controller.request_activation", helper)
    monkeypatch.setattr(controller.stopped, "wait", lambda seconds: False)
    controller.switch_window(SUPPORT)
    assert helper.call_count == 1
    transport.reset_session.assert_not_called()
    assert not controller.stopped.is_set()
    assert controller.desktop is destination
    transport.arm.assert_called_once_with()
    transport.hold.assert_not_called()


def test_one_failed_activation_attempt_stop_without_arm(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.activate", Mock(side_effect=ActivationDenied("отказ")))
    helper = Mock(side_effect=RuntimeError("Windows не подтвердила foreground"))
    monkeypatch.setattr("hid_pilot.controller.request_activation", helper)
    monkeypatch.setattr(controller.stopped, "wait", lambda seconds: False)
    with pytest.raises(RuntimeError, match="восстановить фокус"):
        controller.switch_window(SUPPORT)
    assert helper.call_count == 2
    assert helper.call_args.kwargs == {"strategy": "attached_focus"}
    assert controller.stopped.is_set()
    transport.arm.assert_not_called()


def test_session_reset_succeeds_after_one_foreground_failure(monkeypatch):
    controller, destination, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.activate", Mock(side_effect=ActivationDenied("отказ")))
    helper = Mock(side_effect=[RuntimeError("Windows не подтвердила foreground")] + [None])
    monkeypatch.setattr("hid_pilot.controller.request_activation", helper)
    monkeypatch.setattr(controller.stopped, "wait", lambda seconds: False)
    controller.switch_window(SUPPORT)
    assert helper.call_count == 2
    assert all(not call.kwargs for call in helper.call_args_list[:1])
    assert helper.call_args.kwargs == {"strategy": "attached_focus"}
    assert controller.desktop is destination
    assert not controller.stopped.is_set()
    transport.hold.assert_not_called()
    transport.arm.assert_called_once_with()


def test_stop_on_first_failure_prevents_reserve_strategy(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.activate", Mock(side_effect=ActivationDenied("отказ")))

    def failure(*args, **kwargs):
        controller.stopped.set()
        raise RuntimeError("Windows не подтвердила foreground")

    helper = Mock(side_effect=failure)
    monkeypatch.setattr("hid_pilot.controller.request_activation", helper)
    monkeypatch.setattr(controller.stopped, "wait", lambda seconds: False)
    with pytest.raises(Stopped):
        controller.switch_window(SUPPORT)
    assert helper.call_count == 1
    assert all(not call.kwargs for call in helper.call_args_list)
    transport.arm.assert_not_called()


def test_stop_during_release_aborts_recovery(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.activate", Mock(side_effect=ActivationDenied("отказ")))
    helper = Mock(side_effect=RuntimeError("Windows не подтвердила foreground"))
    monkeypatch.setattr("hid_pilot.controller.request_activation", helper)
    controller.transport.release.side_effect = controller.stopped.set
    with pytest.raises(Stopped):
        controller.switch_window(SUPPORT)
    assert helper.call_count == 1
    transport.arm.assert_not_called()


def test_focus_loss_before_helper_prevents_activation_retry(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    transport.protocol = "HID_PILOT_V4"

    def reject(target):
        controller.desktop.check.side_effect = FocusLost("Пользователь сменил окно")
        raise ActivationDenied("Windows отклонила фокус")

    monkeypatch.setattr("hid_pilot.controller.activate", reject)
    with pytest.raises(FocusLost):
        controller.switch_window(SUPPORT)
    transport.hold.assert_not_called()
    transport.arm.assert_not_called()


def test_stop_during_helper_prevents_destination_arm(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    transport.protocol = "HID_PILOT_V4"
    request = Mock(side_effect=ActivationDenied("Windows отклонила фокус"))
    monkeypatch.setattr("hid_pilot.controller.activate", request)

    def cancel_helper(*args):
        controller.stopped.set()

    monkeypatch.setattr("hid_pilot.controller.request_activation", cancel_helper)
    with pytest.raises(Stopped):
        controller.switch_window(SUPPORT)
    request.assert_called_once_with(SUPPORT)
    assert transport.stop.call_count == 2
    transport.arm.assert_not_called()


def test_stop_during_activation_never_arms(monkeypatch):
    controller, _, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.activate", lambda target: controller.stopped.set())
    with pytest.raises(Stopped):
        controller.switch_window(SUPPORT)
    transport.arm.assert_not_called()


def test_manual_pause_during_switch_keeps_hid_disarmed(monkeypatch):
    controller, destination, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.activate", lambda target: controller.manual_paused.set())
    controller.switch_window(SUPPORT)
    assert controller.desktop is destination
    assert controller.paused.is_set()
    assert not controller.stopped.is_set()
    transport.arm.assert_not_called()


@pytest.mark.parametrize("changes", [{"pid": 33}, {"rect": (0, 0, 120, 120)}])
def test_changed_destination_rejected_before_activation(monkeypatch, changes):
    controller, _, transport = switch_controller(monkeypatch)
    monkeypatch.setattr("hid_pilot.controller.snapshot", lambda hwnd: SUPPORT.model_copy(update=changes))
    activate = Mock()
    monkeypatch.setattr("hid_pilot.controller.activate", activate)
    with pytest.raises(RuntimeError, match="изменились"):
        controller.switch_window(SUPPORT)
    activate.assert_not_called()
    transport.arm.assert_not_called()
