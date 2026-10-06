from unittest.mock import Mock

import pytest

from hid_pilot.bars import BarBox, BarProfile
from hid_pilot.healer import HealDecision, HealerConfig, HealerSnapshot
from hid_pilot.healer_runtime import Healer, TargetChanged
from hid_pilot.party import PartyMember, PartyProfile


def harness(monkeypatch, tmp_path):
    now = [10.]
    monkeypatch.setattr("hid_pilot.healer_runtime.time.monotonic", lambda: now[0])
    monkeypatch.setattr("hid_pilot.healer_runtime.time.time", lambda: 1000 + now[0])
    observer = Mock()
    monkeypatch.setattr("hid_pilot.healer_runtime.HealerObserver", lambda *args: observer)
    controller = Mock()
    controller.paused.is_set.return_value = False
    controller.stopped.is_set.return_value = False
    controller.transport.protocol = "HID_PILOT_V2"
    controller.desktop.target.title = "LU4 - Healer"
    box = BarBox(x1=.1, x2=.8, y1=.1, y2=.11)
    own = BarProfile(width=200, height=800, numbers={key: box for key in ("hp", "mp", "cp")})
    party = PartyProfile(width=200, height=800, row_step=70,
                         name=box, cp=box, hp=box, mp=box)
    healer = Healer(controller, own, party, HealerConfig(battle_cast=1, follow_key=None, mp_buff_key=None, follow_seconds=7),
                     Mock(), tmp_path / "healer-cooldowns.json")
    state = HealerSnapshot(at=10., hp=1., mp=1., cp=1., roster_known=True,
                           members=(PartyMember(slot=1, name="@slot1", hp=.4, mp=1, cp=1),))
    observer.snapshots.return_value = (state,)
    healer.policy.follow_name = "@slot1"
    healer.policy.follow_needed = False
    healer.policy.ready_at["balance"] = 100
    healer.policy.ingest(state.model_copy(update={
        "at": 9.2,
        "members": (state.members[0].model_copy(update={"hp": .8}),),
    }))
    healer.policy.ingest(state.model_copy(update={
        "at": 9.6,
        "members": (state.members[0].model_copy(update={"hp": .6}),),
    }))
    return healer, controller, observer, state, now


def test_follow_double_left_click_and_solo_single_left(monkeypatch, tmp_path):
    healer, controller, _observer, state, now = harness(monkeypatch, tmp_path)
    healer.execute(HealDecision(kind="follow", target="@slot1", reason="test"), state)
    assert [call.args[0] for call in controller.click.call_args_list] == ["left", "left"]
    controller.click.reset_mock()
    healer.execute(HealDecision(kind="cast", skill="battle", target="@slot1", reason="test"), state)
    controller.click.assert_called_once()
    assert controller.click.call_args.args[0] == "left"
    controller.key.assert_called_once_with("2", 100)
    assert healer.policy.follow_needed
    assert healer.policy.busy_until > now[0] + 1


def test_healer_uses_correct_member_position_below_pets(monkeypatch, tmp_path):
    healer, _controller, observer, state, _now = harness(monkeypatch, tmp_path)
    healer.party = PartyProfile.model_validate(healer.party.model_dump() | {
        "row_offsets": [0, 70, 140, 210, 300, 370, 440, 530]})
    observer.snapshots.return_value = (state.model_copy(update={
        "members": (PartyMember(slot=8, name="Mephala", hp=.4, mp=1, cp=1),)}),)
    x, y, slot = healer.point("@slot8")
    assert slot == 8
    assert x == pytest.approx(.45)
    assert y == pytest.approx(.105 + 530 / 800)


def test_self_click_uses_own_panel(monkeypatch, tmp_path):
    healer, controller, observer, state, _now = harness(monkeypatch, tmp_path)
    state = state.model_copy(update={"hp": .3})
    healer.policy.history["@self"].clear()
    healer.policy.history["@self"].extend([(9.2, .7), (9.6, .5)])
    healer.policy.history["@slot1"].clear()
    healer.policy.history["@slot1"].extend([(9.2, .4), (9.6, .4)])
    observer.snapshots.return_value = (state,)
    healer.execute(HealDecision(kind="cast", skill="battle", target="@self", reason="test"), state)
    _, x, y = controller.click.call_args.args
    assert (x, y) == pytest.approx((.45, .105))
    controller.key.assert_called_once_with("2", 100)


def test_changed_slot_after_navigation_cancels_skill(monkeypatch, tmp_path):
    healer, controller, observer, state, _now = harness(monkeypatch, tmp_path)

    def click(*args, verify):
        observer.snapshots.return_value = (state.model_copy(update={
            "members": (state.members[0].model_copy(update={"slot": 2}),)}),)
        verify()

    controller.click.side_effect = click
    with pytest.raises(TargetChanged):
        healer.execute(HealDecision(kind="cast", skill="battle", target="@slot1", reason="test"), state)
    controller.key.assert_not_called()


def test_own_danger_during_target_selection_cancels_old_party_decision(monkeypatch, tmp_path):
    healer, controller, observer, state, now = harness(monkeypatch, tmp_path)
    controller.click.side_effect = lambda *args, **kw: setattr(
        observer.snapshots, "return_value", (state.model_copy(update={"hp": .2, "at": 10.1}),))
    now[0] = 10.1
    with pytest.raises(TargetChanged, match="приоритет"):
        healer.execute(HealDecision(kind="cast", skill="battle", target="@slot1", reason="test"), state)
    controller.key.assert_not_called()


def test_cooldowns_and_cast_lock_survive_restart(monkeypatch, tmp_path):
    healer, _controller, _observer, _state, now = harness(monkeypatch, tmp_path)
    healer.policy.ready_at.update(balance=40., avatar=1210., invocation=310.)
    healer.policy.busy_until = 41.
    healer.save_timers()
    now[0] = 15.
    healer.policy.ready_at.clear()
    healer.policy.busy_until = 0
    healer.restore_timers()
    assert healer.policy.ready_at == {"balance": 40., "avatar": 1210., "invocation": 310.}
    assert healer.policy.busy_until == 41.


def test_old_firmware_rejected_before_any_game_action(tmp_path):
    controller = Mock()
    controller.transport.protocol = "HID_PILOT_V1"
    with pytest.raises(ValueError, match="HID_PILOT_V2"):
        Healer(controller, Mock(), Mock(), HealerConfig(), Mock(), tmp_path / "state.json")
    controller.key.assert_not_called()
    controller.click.assert_not_called()


def test_stale_or_missing_identity_prevents_follow(monkeypatch, tmp_path):
    healer, controller, _observer, _state, now = harness(monkeypatch, tmp_path)
    now[0] = 12.
    with pytest.raises(TargetChanged):
        healer.select("@slot1", "right", True)
    controller.click.assert_not_called()


def test_executor_itself_blocks_follow_and_heal_during_invocation(monkeypatch, tmp_path):
    healer, controller, _observer, state, _now = harness(monkeypatch, tmp_path)
    healer.policy.busy_until = 40
    for decision in (HealDecision(kind="follow", target="@slot1", reason="test"),
                     HealDecision(kind="cast", skill="battle", target="@slot1", reason="test")):
        with pytest.raises(TargetChanged, match="Invocation"):
            healer.execute(decision, state)
    controller.click.assert_not_called()
    controller.key.assert_not_called()


@pytest.mark.parametrize("cause", ["deadline", "leader", "stale"])
def test_approach_stops_with_s_without_needing_leader(monkeypatch, tmp_path, cause):
    healer, controller, _observer, state, now = harness(monkeypatch, tmp_path)
    healer.approach_until = 40.
    healer.policy.chase_name = "@slot2"
    if cause == "deadline":
        now[0] = 40.
        state = state.model_copy(update={"at": 40.})
    elif cause == "leader":
        state = state.model_copy(update={"members": ()})
    else:
        now[0] = 12.
    assert healer.guard_approach(state)
    controller.key.assert_called_once_with("S", 1000)
    assert healer.approach_until is None
    assert healer.policy.chase_name is None
    assert healer.policy.follow_needed
    assert not healer.guard_approach(state)


def test_solo_heal_keeps_existing_approach_budget(monkeypatch, tmp_path):
    healer, controller, _observer, state, _now = harness(monkeypatch, tmp_path)
    healer.approach_until = 25.
    healer.execute(HealDecision(kind="cast", skill="battle", target="@slot1", reason="test"), state)
    assert healer.approach_until == 25.
    controller.key.assert_called_once_with("2", 100)


def test_invocation_lock_does_not_trigger_movement_stop(monkeypatch, tmp_path):
    healer, controller, _observer, _state, now = harness(monkeypatch, tmp_path)
    healer.policy.busy_until = 45.
    now[0] = 20.
    assert not healer.guard_approach(None)
    controller.key.assert_not_called()


def test_observer_failure_cancels_running_approach_before_exit(monkeypatch, tmp_path):
    healer, controller, observer, _state, _now = harness(monkeypatch, tmp_path)
    healer.approach_until = 40.
    observer.snapshots.side_effect = RuntimeError("capture failed")
    with pytest.raises(RuntimeError, match="capture failed"):
        healer.run()
    controller.key.assert_called_once_with("S", 1000)
    observer.close.assert_called_once()


@pytest.mark.parametrize("missing", ["member", "leader", "hp"])
def test_unknown_member_does_not_stop_but_missing_critical_state_does(monkeypatch, tmp_path, missing):
    healer, controller, observer, state, now = harness(monkeypatch, tmp_path)
    state = state.model_copy(update={
        "roster_known": False,
        "hp": None if missing == "hp" else 1.,
        "members": (
            PartyMember(slot=1, name=None, hp=None if missing == "leader" else 1., mp=1.),
            PartyMember(slot=2, name=None, hp=1., mp=1.),
        ),
    })
    observer.snapshots.side_effect = lambda: (state.model_copy(update={"at": now[0]}),)
    controller.wait.side_effect = lambda _: now.__setitem__(0, now[0] + 1)
    if missing in {"member", "leader"}:
        healer.run(deadline=22.)
        assert now[0] == 22.
        assert [call.args[0] for call in controller.click.call_args_list] == (["left", "left"] if missing == "member" else [])
    else:
        with pytest.raises(RuntimeError, match="неизвестны более 10 секунд"):
            healer.run(deadline=22.)
        controller.click.assert_not_called()
    controller.key.assert_not_called()
    observer.close.assert_called_once()


@pytest.mark.parametrize("restored", [False, True])
def test_missing_leader_retries_without_recovery_movement(monkeypatch, tmp_path, restored):
    healer, controller, observer, state, now = harness(monkeypatch, tmp_path)
    missing = state.model_copy(update={"members": (), "roster_known": False})
    recovered = state.model_copy(update={"members": (PartyMember(slot=1, name=None, hp=1., mp=1.),)})

    def samples():
        current = recovered if restored and now[0] >= 30 else missing
        return (current.model_copy(update={"at": now[0]}),)

    observer.snapshots.side_effect = samples
    controller.wait.side_effect = lambda _: now.__setitem__(0, now[0] + 1)
    healer.execute = Mock()
    healer.run(deadline=60.)
    controller.recover_view.assert_not_called()
    assert not healer.policy.hp_missing_since
    assert healer.policy.ignored_slots == (set() if restored else {1})
    observer.close.assert_called_once()


def test_unreadable_member_casts_balance_without_camera_or_target_clicks(monkeypatch, tmp_path):
    healer, controller, observer, state, now = harness(monkeypatch, tmp_path)
    healer.policy.ready_at["balance"] = 0
    missing = state.model_copy(update={"members": (), "roster_known": False})
    observer.snapshots.side_effect = lambda: (missing.model_copy(update={"at": now[0]}),)
    controller.wait.side_effect = lambda _: now.__setitem__(0, now[0] + 1)
    healer.run(deadline=60.)
    assert [call.args for call in controller.key.call_args_list] == [("1", 100)]
    controller.click.assert_not_called()
    controller.recover_view.assert_not_called()


def test_emergency_follow_can_click_visible_leader_with_unknown_hp(monkeypatch, tmp_path):
    healer, controller, observer, state, now = harness(monkeypatch, tmp_path)
    now[0] = 25
    missing = state.model_copy(update={"at": 25, "roster_known": False,
                                      "members": (state.members[0].model_copy(update={"hp": None}),)})
    healer.policy.hp_missing_since[1] = 10
    healer.policy.follow_needed = True
    observer.snapshots.return_value = (missing,)
    decision = healer.policy.decide(missing, 25)
    assert decision.kind == "follow"
    healer.execute(decision, missing)
    assert controller.click.call_count == 2


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_snapshot_updates_panel_always_but_logs_only_in_debug(monkeypatch, tmp_path, caplog, level):
    healer, controller, observer, state, now = harness(monkeypatch, tmp_path)
    state = state.model_copy(update={"members": (state.members[0].model_copy(update={"hp": 1.}),)})
    healer.policy.next_follow_at = 100
    observer.snapshots.side_effect = lambda: (state.model_copy(update={"at": now[0]}),)
    controller.wait.side_effect = lambda _: now.__setitem__(0, now[0] + .2)
    with caplog.at_level(level, logger="hid_pilot.healer"):
        healer.run(deadline=11.)
    assert healer.notify.state.called
    assert ("СЕБЯ" in caplog.text) == (level == "DEBUG")
    assert any("СЕБЯ" in call.args[0] for call in healer.notify.call_args_list) == (level == "DEBUG")
