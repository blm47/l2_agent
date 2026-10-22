from unittest.mock import Mock

import pytest

from hid_pilot.farm_ee import EEConfig, EEDecision, EEFarmer, EEPolicy
from hid_pilot.healer_runtime import TargetChanged
from test_healer import snapshot


def policy():
    result = EEPolicy(EEConfig(party_buffs_on_start=True, random_movement=False), now=0)
    result.ready_at.update(invoke_single=9999, invoke_party=9999)
    return result


def mana_reserve_state(own_mp=.05, members_mp=(.01, .015, .001), at=10.):
    state = snapshot(at=at, mp=own_mp, party=(1.,) * len(members_mp))
    return state.model_copy(update={"members": tuple(
        m.model_copy(update={"mp": mp}) for m, mp in zip(state.members, members_mp))})


def test_low_mana_allows_one_emergency_recharge_per_first_two_slots():
    p = policy()
    p.ready_at["bless"] = 9999
    state = mana_reserve_state()
    assert p.decide(state, 10) == EEDecision(skill="mana", target="@slot1")
    p.emergency_mana_used.add("@slot1")
    assert p.decide(state, 10) == EEDecision(skill="mana", target="@slot2")
    p.emergency_mana_used.add("@slot2")
    assert p.decide(state, 10).skill != "mana"
    assert p.decide(mana_reserve_state(at=100), 100).skill != "mana"


@pytest.mark.parametrize("values", [(.02, .03, .001), (None, .02, .001)])
def test_low_mana_exception_is_strict_and_does_not_apply_to_other_slots(values):
    p = policy()
    p.ready_at["bless"] = 9999
    assert p.decide(mana_reserve_state(members_mp=values), 10).skill != "mana"


def test_emergency_quota_resets_at_ten_percent_but_not_on_unknown_or_stale_mana():
    p = policy()
    p.ready_at["bless"] = 9999
    p.emergency_mana_used = {"@slot1", "@slot2"}
    p.decide(mana_reserve_state(own_mp=None), 10)
    p.decide(mana_reserve_state(own_mp=.1, at=8), 10)
    assert p.emergency_mana_used == {"@slot1", "@slot2"}
    state = mana_reserve_state(own_mp=.1, at=11)
    assert p.decide(state, 11) == EEDecision(skill="mana", target="@slot3")
    assert not p.emergency_mana_used
    p.ready_at["bless"] = 9999
    assert p.decide(mana_reserve_state(at=12), 12).target == "@slot1"


def test_emergency_mana_does_not_override_heal_or_empty_own_mana():
    p = policy()
    p.ready_at["bless"] = 9999
    state = mana_reserve_state().model_copy(update={"hp": .1})
    assert p.decide(state, 10).skill == "single"
    assert p.decide(mana_reserve_state(own_mp=.019), 10).skill == "follow"


def test_emergency_recharge_is_reserved_once_and_persisted(tmp_path, monkeypatch):
    state = confirmed_mana_target(mana_reserve_state())
    farmer, _ = runtime(tmp_path, monkeypatch, state)
    farmer.ee.ready_at["bless"] = 1200
    farmer.execute_ee(EEDecision(skill="mana", target="@slot1"), 100)
    farmer.controller.key.assert_called_once_with("3", 100)
    farmer.restore_timers()
    assert farmer.ee.emergency_mana_used == {"@slot1"}
    assert farmer.ee.decide(mana_reserve_state(at=12), 12).target == "@slot2"


def test_failed_emergency_recharge_does_not_allow_repeat_attempt(tmp_path, monkeypatch):
    state = confirmed_mana_target(mana_reserve_state())
    farmer, _ = runtime(tmp_path, monkeypatch, state)
    farmer.ee.ready_at["bless"] = 1200
    farmer.controller.key.side_effect = RuntimeError("ACK потерян")
    with pytest.raises(RuntimeError, match="ACK"):
        farmer.execute_ee(EEDecision(skill="mana", target="@slot1"), 100)
    farmer.restore_timers()
    assert farmer.ee.emergency_mana_used == {"@slot1"}


@pytest.mark.parametrize("mp", [0., .0199, .0499])
@pytest.mark.parametrize("party", [(.1,), (.1, .1, .1), (0., .1), (1., 1.)])
def test_empty_own_mana_blocks_support_and_keeps_following(mp, party):
    p = policy()
    p.ready_at["bless"] = 1000
    p.config.random_movement = True
    p.next_move = 0
    state = snapshot(hp=1., mp=mp, party=party)
    state = state.model_copy(update={"members": tuple(
        m.model_copy(update={"mp": .1}) for m in state.members)})
    expected = "resurrect" if 0. in party else "follow" if party[0] > 0 else None
    decision = p.decide(state, 10)
    assert (decision.skill if decision else None) == expected


def test_empty_own_mana_uses_available_self_recovery_before_healing():
    p = EEPolicy(EEConfig(), now=0)
    state = snapshot(hp=.1, mp=.01, party=(.1, .1))
    assert p.decide(state, 10) == EEDecision(skill="bless", target="@self")
    p.ready_at["bless"] = 1200
    assert p.decide(state, 10) == EEDecision(skill="invoke_single", target="@self")
    p.ready_at["invoke_single"] = 310
    assert p.decide(state, 10) == EEDecision(skill="single", target="@self")
    p.ready_at["single"] = 30
    assert p.decide(state, 10).skill == "follow"
    p.next_follow = 14
    assert p.decide(state, 10) is None
    assert p.decide(state.model_copy(update={"at": 14}), 14).skill == "follow"


def test_empty_own_mana_waits_for_mass_invocation_then_uses_it():
    p = EEPolicy(EEConfig(), now=0)
    p.ready_at.update(bless=1200, invoke_party=310)
    state = snapshot(mp=.01, party=(1.,) * 4)
    state = state.model_copy(update={"members": tuple(
        m.model_copy(update={"mp": .79}) for m in state.members)})
    assert p.decide(state, 10).skill == "follow"
    assert p.decide(state.model_copy(update={"at": 310}), 310).skill == "invoke_party"


def test_normal_healing_resumes_at_exactly_five_percent_mana():
    p = policy()
    assert p.decide(snapshot(mp=.05, party=(.1,)), 10) == EEDecision(skill="single", target="@slot1")
    assert p.decide(snapshot(mp=None, party=(.1,)), 10) is None


@pytest.mark.parametrize("mp", [0., .02, .0499])
def test_below_five_percent_only_self_single_heal_is_allowed(mp):
    p = policy()
    p.ready_at["bless"] = 1200
    p.ready_at["resurrect"] = 1200
    state = snapshot(hp=.1, mp=mp, party=(.1, .1, .1, 0.))
    assert p.decide(state, 10) == EEDecision(skill="single", target="@self")
    p.ready_at["single"] = 30
    assert p.decide(state, 10).skill == "follow"
    state = state.model_copy(update={"hp": p.config.self_heal_hp_threshold})
    p.ready_at.pop("single")
    assert p.decide(state, 10).skill == "follow"


def test_mana_loss_during_target_selection_cancels_healing(tmp_path, monkeypatch):
    state = snapshot(mp=.1, party=(.1,))
    farmer, _ = runtime(tmp_path, monkeypatch, state)
    farmer.ee.ready_at["bless"] = 1200
    farmer.latest.side_effect = [state, state, state.model_copy(update={"mp": .01})]
    with pytest.raises(TargetChanged):
        farmer.execute_ee(EEDecision(skill="single", target="@slot1"), 100)
    farmer.controller.key.assert_not_called()


def test_group_heal_precedes_single_heal_and_mana():
    p = policy()
    state = snapshot(party=(.19, .19), mp=.1)
    assert p.decide(state, 10).skill == "group"
    p.ready_at["group"] = 20
    assert p.decide(state, 10) == EEDecision(skill="single", target="@slot1")


def test_single_threshold_is_strict_and_destroyer_is_protected():
    p = policy()
    assert p.decide(snapshot(party=(.2,)), 10).skill != "single"
    state = snapshot(party=(.19,)).model_copy(update={"destroyer_slots": (1,)})
    assert p.decide(state, 10).skill != "single"


def test_mana_targets_lowest_living_member():
    p = policy()
    p.ready_at["resurrect"] = 9999
    state = snapshot(party=(1., 1., 0.))
    state = state.model_copy(update={"members": tuple(
        m.model_copy(update={"mp": mp}) for m, mp in zip(state.members, (.29, .1, .01)))})
    assert p.decide(state, 10) == EEDecision(skill="mana", target="@slot2")
    assert p.decide(state.model_copy(update={"mp": .29}), 10).skill == "bless"


@pytest.mark.parametrize("others,skill", [(0, "invoke_single"), (3, "invoke_single"),
                                        (4, "invoke_party"), (5, "invoke_party")])
def test_invocation_party_size_includes_self(others, skill):
    p = EEPolicy(EEConfig())
    state = snapshot(party=(1.,) * others)
    state = state.model_copy(update={"members": tuple(
        m.model_copy(update={"mp": .79}) for m in state.members)})
    result = p.decide(state, 10)
    assert result.skill == skill
    assert p.timing(skill)[1:] == (1.5, 300)


def test_unknown_roster_blocks_invocation_and_stale_observation_blocks_all_casts():
    p = EEPolicy(EEConfig())
    assert p.decide(snapshot(known=False), 10).skill == "clarity"
    assert p.decide(snapshot(at=8), 10) is None
    assert p.decide(snapshot(hp=None), 10) is None


def test_ocr_name_changes_preserve_slot_buff_timers():
    p = policy()
    state = snapshot(party=(1., 1.))
    p.ready_at["buff:clarity:self"] = 1000
    first = p.decide(state, 10)
    assert first == EEDecision(skill="clarity", target="@slot1")
    p.ready_at[p.buff_timer("clarity", first.target, state)] = 1000
    swapped = state.model_copy(update={"members": tuple(
        m.model_copy(update={"slot": 3 - m.slot}) for m in state.members)})
    assert p.decide(swapped, 10).target == "@slot2"
    assert p.ready_at["buff:clarity:slot1"] == 1000
    p.ready_at["clarity"] = 11
    assert p.decide(state, 10).skill == "shock"


@pytest.mark.parametrize("skill", ["clarity", "shock", "gain"])
def test_party_buff_start_delay_and_boundary(skill):
    p = EEPolicy(EEConfig(random_movement=False), now=10)
    p.ready_at.update(invoke_single=9999, invoke_party=9999)
    for other in ("clarity", "shock", "gain", "kiss"):
        if other != skill:
            p.ready_at[other] = 9999
    p.ready_at[f"buff:{skill}:self"] = 9999
    assert p.config.party_buffs_on_start is False
    assert p.decide(snapshot(at=309.9), 309.9).skill == "follow"
    assert p.decide(snapshot(at=310), 310) == EEDecision(skill=skill, target="@slot1")


@pytest.mark.parametrize("skill", ["clarity", "shock", "gain", "kiss"])
def test_self_buffs_have_no_start_delay(skill):
    p = EEPolicy(EEConfig(), now=10)
    p.ready_at.update(invoke_single=9999, invoke_party=9999)
    for other in ("clarity", "shock", "gain", "kiss"):
        if other != skill:
            p.ready_at[other] = 9999
    assert p.decide(snapshot(), 10) == EEDecision(
        skill=skill, target=None if skill == "kiss" else "@self")


def test_start_delay_does_not_delay_heal_mana_or_invocation():
    p = EEPolicy(EEConfig(), now=10)
    assert p.decide(snapshot(party=(.19,)), 10).skill == "single"
    state = snapshot()
    state = state.model_copy(update={"members": (
        state.members[0].model_copy(update={"mp": .1}),)})
    assert p.decide(state, 10).skill == "invoke_single"
    p.ready_at["invoke_single"] = 310
    assert p.decide(state, 10).skill == "mana"
    p.ready_at.pop("invoke_single")
    assert p.decide(snapshot(), 10).skill == "invoke_single"


@pytest.mark.parametrize("hp,target", [(.49, "@slot1"), (.50, None)])
def test_configurable_single_heal_threshold(hp, target):
    p = policy()
    p.config.heal_hp_threshold = .5
    decision = p.decide(snapshot(party=(hp,)), 10)
    assert (decision.target if decision.skill == "single" else None) == target


def test_configurable_group_heal_includes_self():
    p = policy()
    p.config.heal_hp_threshold = .5
    assert p.decide(snapshot(hp=.49, party=(.49,)), 10).skill == "group"
    assert p.decide(snapshot(hp=.5, party=(.49,)), 10).skill == "single"


@pytest.mark.parametrize("mp,expected", [(.59, True), (.60, False)])
def test_configurable_party_mana_threshold(mp, expected):
    p = policy()
    p.config.party_mp_threshold = .6
    state = snapshot(party=(1.,))
    state = state.model_copy(update={"members": (state.members[0].model_copy(update={"mp": mp}),)})
    assert (p.decide(state, 10).skill == "mana") is expected
    assert p.decide(snapshot(mp=.4), 10).skill != "bless"


@pytest.mark.parametrize("field", ["heal_hp_threshold", "party_mp_threshold"])
@pytest.mark.parametrize("value", [0, -.1, 1.1, float("nan"), float("inf")])
def test_resource_threshold_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        EEConfig(**{field: value})


def confirmed_mana_target(state):
    from hid_pilot.target import TargetObservation

    return state.model_copy(update={"selected_name": "A", "selected_class": "Hawkeye",
        "selected_confidence": .99, "target": TargetObservation(at=state.at, kind="player",
        name="A", player_class="Hawkeye", confidence=.99)})


def runtime(tmp_path, monkeypatch, state):
    now = [10.]
    monkeypatch.setattr("hid_pilot.farm_ee.time.monotonic", lambda: now[0])
    monkeypatch.setattr("hid_pilot.farm_ee.time.time", lambda: 1000 + now[0])
    farmer = object.__new__(EEFarmer)
    farmer.config = EEConfig()
    farmer.ee = policy()
    farmer.ee.ready_at.update(invoke_single=300, invoke_party=300)
    farmer.controller = Mock(pause_generation=0)
    farmer.controller.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
    farmer.latest = Mock(return_value=state)
    farmer.point = Mock(return_value=(.1, .2, 0))
    farmer.click_point = Mock(return_value=(.1, .2))
    farmer.observer = Mock()
    farmer.report = Mock()
    farmer.identity = "test"
    farmer.state_path = tmp_path / "ee.json"
    return farmer, now


def test_bless_sequence_and_persisted_cooldown(tmp_path, monkeypatch):
    farmer, now = runtime(tmp_path, monkeypatch, snapshot(mp=.1))
    farmer.execute_ee(EEDecision(skill="bless", target="@self"), 100)
    assert [c.args[0] for c in farmer.controller.key.call_args_list] == ["F8", "F9", "F10"]
    assert [c.args[0] for c in farmer.controller.wait.call_args_list] == [.3, 1.5]
    farmer.restore_timers()
    assert farmer.ee.ready_at["bless"] == pytest.approx(1210.3, abs=.1)


def test_buff_cast_persists_individual_duration_and_global_cooldown(tmp_path, monkeypatch):
    farmer, _ = runtime(tmp_path, monkeypatch, confirmed_mana_target(snapshot()))
    farmer.ee.ready_at.update({"buff:clarity:self": 1000, "buff:clarity:slot1": 1000,
                              "buff:clarity:slot2": 1000, "shock": 1000})
    farmer.execute_ee(EEDecision(skill="gain", target="@self"), 100)
    assert farmer.ee.ready_at["gain"] == 110
    assert farmer.ee.ready_at["buff:gain:self"] == 1092
    farmer.restore_timers()
    assert farmer.ee.ready_at["buff:gain:self"] == pytest.approx(1092, abs=.1)


def test_script_is_registered_and_valid():
    from pathlib import Path
    from hid_pilot.script import parse_script

    path = Path(__file__).parents[1] / "extensions" / "scripts" / "farm_ee.pilot"
    assert parse_script(path.read_text("utf-8"))[-1].operation == "farm_ee"


@pytest.mark.parametrize("protocol", ["HID_PILOT_V3", "HID_PILOT_V4"])
def test_supported_protocol_reaches_shared_runtime(protocol, monkeypatch, tmp_path):
    init = Mock()
    monkeypatch.setattr("hid_pilot.farm_ee.Healer.__init__", init)
    controller = Mock()
    controller.transport.protocol = protocol
    EEFarmer(controller, Mock(), Mock(), EEConfig(random_movement=protocol == "HID_PILOT_V4"), Mock(), tmp_path / "ee.json")
    init.assert_called_once()


def test_revalidate_before_cast(tmp_path, monkeypatch):
    farmer, _ = runtime(tmp_path, monkeypatch, snapshot(mp=.1))
    farmer.latest.side_effect = [snapshot(mp=.1), snapshot(mp=1.)]
    with pytest.raises(TargetChanged):
        farmer.execute_ee(EEDecision(skill="bless", target="@self"), 100)
    farmer.controller.key.assert_not_called()


def damage_samples(p, count=3, rate=.21, start=10., duration=2.):
    for step in range(round(duration / .2) + 1):
        at = start + step * .2
        state = snapshot(at=at, party=(.99 - rate * (at - start),) * count)
        p.ingest(state)
    return state


def test_sustained_damage_triggers_group_above_hp_threshold_and_publishes_rates():
    p = policy()
    state = damage_samples(p)
    assert p.decide(state, state.at).skill == "group"
    assert p.damage_rates["@slot1"] == pytest.approx(.21)
    p.ready_at["group"] = 20
    assert p.decide(state, state.at).skill != "group"


@pytest.mark.parametrize("count,rate,duration", [(2, .21, 2), (3, .20, 2), (3, .21, 1.8)])
def test_damage_requires_three_members_strict_rate_and_full_duration(count, rate, duration):
    p = policy()
    state = damage_samples(p, count=count, rate=rate, duration=duration)
    assert p.decide(state, state.at).skill != "group"


@pytest.mark.parametrize("break_kind", ["gap", "missing", "heal", "revision", "name", "pause"])
def test_interrupted_damage_requires_new_continuous_observation(break_kind):
    p = policy()
    state = damage_samples(p)
    at = 12.2
    if break_kind == "gap":
        at = 13.2
    state = snapshot(at=at, party=(.5,) * 3)
    if break_kind == "missing":
        state = snapshot(at=at, party=(None,) * 3)
    elif break_kind == "heal":
        state = snapshot(at=at, party=(.9,) * 3)
    elif break_kind == "revision":
        state = state.model_copy(update={"profile_revision": 1})
    elif break_kind == "name":
        state = state.model_copy(update={"members": tuple(
            m.model_copy(update={"name": f"new{m.slot}"}) for m in state.members)})
    elif break_kind == "pause":
        p.reset_damage(after=12.1)
        damage_samples(p)
    assert p.decide(state, at).skill != "group"


@pytest.mark.parametrize("hp,count,expected", [(.149, 3, "circlet_of_heal"), (.299, 3, "circlet_of_heal"),
                                             (.30, 3, "group"), (.299, 2, "group"),
                                             (.149, 2, "group")])
def test_circlet_of_heal_strict_threshold_count_and_priority(hp, count, expected):
    p = policy()
    p.config.heal_hp_threshold = .35
    assert p.decide(snapshot(party=(hp,) * count), 10).skill == expected
    assert p.timing("circlet_of_heal") == ("SPACE", .5, 24.)


def test_circlet_of_heal_counts_self_and_falls_back_during_cooldown():
    p = policy()
    state = snapshot(hp=.1, party=(.1, .1))
    assert p.decide(state, 10).skill == "circlet_of_heal"
    p.ready_at["circlet_of_heal"] = 34
    assert p.decide(state, 10).skill == "group"
    assert p.decide(snapshot(at=34, hp=.1, party=(.1, .1)), 34).skill == "circlet_of_heal"


def test_circlet_of_heal_at_thirty_percent_counts_self_but_not_dead_or_unknown_members():
    p = policy()
    assert p.decide(snapshot(hp=.29, party=(.29, .29)), 10).skill == "circlet_of_heal"
    assert p.decide(snapshot(party=(.29, .29, None)), 10).skill != "circlet_of_heal"
    assert p.decide(snapshot(party=(.29, .29, 0.)), 10).skill != "circlet_of_heal"


def test_circlet_of_heal_execution_and_persisted_cooldown(tmp_path, monkeypatch):
    farmer, _ = runtime(tmp_path, monkeypatch, snapshot(party=(.1,) * 3))
    farmer.execute_ee(EEDecision(skill="circlet_of_heal"), 100)
    farmer.controller.key.assert_called_once_with("SPACE", 100)
    farmer.controller.wait.assert_called_once_with(.5)
    farmer.controller.click.assert_not_called()
    farmer.restore_timers()
    assert farmer.ee.ready_at["circlet_of_heal"] == pytest.approx(34.)


def test_runtime_processes_buffered_frames_between_casts(monkeypatch):
    p = policy()
    samples = [snapshot(at=10 + i * .2, party=(.99 - .21 * i * .2,) * 3)
               for i in range(11)]
    farmer = object.__new__(EEFarmer)
    farmer.ee = p
    farmer.observer = Mock()
    farmer.observer.snapshots.return_value = tuple(samples)
    monkeypatch.setattr("hid_pilot.farm_ee.Healer.latest", lambda self: samples[-1])
    state = farmer.latest()
    assert p.decide(state, state.at).skill == "group"
    assert farmer.latest() == state
    assert p.high_damage_since["@slot1"] == 10.


@pytest.mark.parametrize("skill", ["clarity", "shock"])
def test_ten_minute_refresh_and_saved_countdown(skill, tmp_path, monkeypatch):
    farmer, now = runtime(tmp_path, monkeypatch, snapshot())
    for other in ("clarity", "shock", "gain", "kiss"):
        if other != skill:
            farmer.ee.ready_at[other] = 1000
    farmer.execute_ee(EEDecision(skill=skill, target="@self"), 100)
    assert farmer.ee.ready_at[f"buff:{skill}:self"] == 612
    farmer.restore_timers()
    label = "Клара" if skill == "clarity" else "РШ"
    assert farmer.ee.buff_status(snapshot(), now[0])["@self"][label] == 600


def test_buff_status_and_cast_do_not_require_names_or_known_roster():
    p = policy()
    state = snapshot(known=False)
    state = state.model_copy(update={"members": tuple(
        m.model_copy(update={"name": None, "confidence": 0}) for m in state.members)})
    p.ready_at["buff:clarity:self"] = 1000
    assert p.decide(state, 10) == EEDecision(skill="clarity", target="@slot1")
    p.ready_at["buff:clarity:slot1"] = 610
    p.ready_at["buff:shock:slot1"] = 12
    assert p.buff_status(state, 10)["@slot1"] == {"Клара": 600, "РШ": 2}
    assert p.buff_status(state, 13)["@slot1"]["РШ"] == 0
    assert p.decide(state, 10).target == "@slot2"
    named = snapshot()
    assert p.buff_status(named, 10)["@slot1"]["Клара"] == 600


def test_legacy_buff_schedule_is_shortened_once(tmp_path, monkeypatch):
    import json

    farmer, _ = runtime(tmp_path, monkeypatch, snapshot())
    farmer.state_path.write_text(json.dumps({"identity": "test", "ready_at": {
        "buff:clarity:A": 2090, "buff:shock:A": 2090, "buff:gain:A": 2090}}), "utf-8")
    farmer.restore_timers()
    assert farmer.ee.buff_status(snapshot(), 10)["@slot1"] == {
        "Клара": 600, "РШ": 600, "Mana Gain": 1080}
    farmer.save_timers()
    farmer.restore_timers()
    assert farmer.ee.buff_status(snapshot(), 10)["@slot1"]["Клара"] == 600


def test_no_early_rebuff_for_ten_minutes_despite_ocr_changes_and_status_updates():
    p = policy()
    p.ready_at.update(gain=9999, kiss=9999)
    for skill in ("clarity", "shock"):
        for target in ("self", "slot1", "slot2"):
            p.ready_at[f"buff:{skill}:{target}"] = 610
    for at in (10, 10.2, 20, 100, 300, 609.999):
        state = snapshot(at=at)
        state = state.model_copy(update={"members": tuple(
            m.model_copy(update={"name": f"OCR{at}-{m.slot}" if at != 100 else None})
            for m in state.members)})
        p.buff_status(state, at)
        decision = p.decide(state, at)
        assert decision is None or decision.skill not in {"clarity", "shock"}
        assert p.ready_at["buff:clarity:slot1"] == 610
        assert p.ready_at["buff:shock:slot1"] == 610
    assert p.decide(snapshot(at=610), 610).skill == "clarity"


def test_ready_invocation_is_not_starved_by_party_mana_requests():
    p = EEPolicy(EEConfig(), now=0)
    state = snapshot(party=(1.,) * 5, mp=.29)
    state = state.model_copy(update={"members": tuple(
        m.model_copy(update={"mp": .1}) for m in state.members)})
    assert p.decide(state, 10).skill == "bless"
    p.ready_at["bless"] = 1210
    assert p.decide(state, 10).skill == "invoke_party"
    p.ready_at["invoke_party"] = 310
    assert p.decide(state, 10).skill == "mana"
    assert p.decide(state.model_copy(update={"at": 310}), 310).skill == "invoke_party"


def test_mass_invocation_available_with_five_observed_characters_despite_missing_resources():
    p = EEPolicy(EEConfig(), now=0)
    state = snapshot(party=(1.,) * 4, known=False)
    state = state.model_copy(update={"members": tuple(
        m.model_copy(update={"mp": .79 if m.slot < 3 else None}) for m in state.members)})
    assert p.decide(state, 10).skill == "invoke_party"


@pytest.mark.parametrize("mana,own_mp,expected", [
    ((.79, .79, 1., 1.), 1., True),
    ((.79, .8, 1., 1.), 1., False),
    ((.79, None, 1., 1.), 1., False),
    ((.79, 1., 1., 1.), .79, True),
    ((.79, 1., 1., 1.), .8, False),
    ((1., 1., 1., 1.), 1., False),
])
def test_mass_invocation_requires_two_known_mana_values_below_eighty(mana, own_mp, expected):
    p = EEPolicy(EEConfig(), now=0)
    state = snapshot(party=(1.,) * 4, mp=own_mp)
    state = state.model_copy(update={"members": tuple(
        m.model_copy(update={"mp": mp}) for m, mp in zip(state.members, mana))})
    assert (p.decide(state, 10).skill == "invoke_party") is expected
    p.ready_at["invoke_party"] = 310
    assert p.decide(state, 10).skill != "invoke_party"


def test_bless_finishes_sequence_if_mana_recovers_after_f8(tmp_path, monkeypatch):
    farmer, _ = runtime(tmp_path, monkeypatch, snapshot(mp=.1))
    farmer.latest.side_effect = [snapshot(mp=.1)] * 3 + [snapshot(mp=1.)]
    farmer.execute_ee(EEDecision(skill="bless", target="@self"), 100)
    assert [c.args[0] for c in farmer.controller.key.call_args_list] == ["F8", "F9", "F10"]


def test_interrupted_bless_cleans_up_f8_without_starting_cooldown(tmp_path, monkeypatch):
    farmer, _ = runtime(tmp_path, monkeypatch, snapshot(mp=.1))
    farmer.latest.side_effect = [snapshot(mp=.1)] * 3 + [TargetChanged("Нет свежего кадра")]
    with pytest.raises(TargetChanged):
        farmer.execute_ee(EEDecision(skill="bless", target="@self"), 100)
    assert [c.args[0] for c in farmer.controller.key.call_args_list] == ["F8", "F10"]
    assert "bless" not in farmer.ee.ready_at


def test_resurrection_targets_dead_member_and_respects_cooldown():
    p = policy()
    state = snapshot(party=(None, 0., 1.))
    assert p.decide(state, 10) == EEDecision(skill="resurrect", target="@slot2")
    p.ready_at["resurrect"] = 30
    assert p.decide(state, 10).skill != "resurrect"
    assert p.decide(state.model_copy(update={"at": 30}), 30).skill == "resurrect"
    assert p.decide(snapshot(at=30, party=(None, 1.)), 30).skill != "resurrect"


def test_resurrection_precedes_healing_living_member():
    p = policy()
    assert p.decide(snapshot(party=(0., .1)), 10).skill == "resurrect"


def test_resurrection_execution_and_saved_cooldown(tmp_path, monkeypatch):
    farmer, _ = runtime(tmp_path, monkeypatch, snapshot(party=(0., 1.)))
    farmer.execute_ee(EEDecision(skill="resurrect", target="@slot1"), 100)
    farmer.controller.click.assert_called_once()
    farmer.controller.key.assert_called_once_with("F1", 100)
    farmer.controller.wait.assert_called_once_with(1.)
    farmer.restore_timers()
    assert farmer.ee.ready_at["resurrect"] == pytest.approx(30.)


def test_revived_target_is_not_resurrected_again(tmp_path, monkeypatch):
    farmer, _ = runtime(tmp_path, monkeypatch, snapshot(party=(0., 1.)))
    farmer.latest.side_effect = [snapshot(party=(0., 1.)), snapshot(party=(1., 1.))]
    with pytest.raises(TargetChanged):
        farmer.execute_ee(EEDecision(skill="resurrect", target="@slot1"), 100)
    farmer.controller.key.assert_not_called()


def idle_movement_policy():
    p = policy()
    p.config.random_movement = True
    p.ready_at.update(clarity=9999, shock=9999, gain=9999, kiss=9999)
    p.next_move = 10
    return p


def test_movement_runs_when_due_before_follow_and_can_be_disabled():
    p = idle_movement_policy()
    assert p.decide(snapshot(at=9.9), 9.9).skill == "follow"
    assert p.decide(snapshot(), 10).skill == "move"
    p.config.random_movement = False
    assert p.decide(snapshot(), 10).skill == "follow"


@pytest.mark.parametrize("state", [snapshot(party=(.1,)), snapshot(mp=.1),
                                   snapshot(party=(None,)), snapshot(known=False)])
def test_movement_waits_for_safe_observation_and_support_actions(state):
    p = idle_movement_policy()
    decision = p.decide(state, 10)
    assert decision is None or decision.skill != "move"
    p.busy_until = 12
    assert p.decide(snapshot(), 10) is None


def test_movement_execution_duration_interval_and_follow(tmp_path, monkeypatch):
    farmer, now = runtime(tmp_path, monkeypatch, snapshot())
    farmer.ee = idle_movement_policy()
    monkeypatch.setattr("hid_pilot.farm_ee.random.choice", lambda keys: "A")
    monkeypatch.setattr("hid_pilot.farm_ee.random.randint", lambda low, high: 1750)
    monkeypatch.setattr("hid_pilot.farm_ee.random.uniform", lambda low, high: 45.)
    farmer.controller.key.side_effect = lambda key, duration: now.__setitem__(0, now[0] + duration / 1000)
    farmer.execute_ee(EEDecision(skill="move"), 100)
    farmer.controller.key.assert_called_once_with("A", 1750)
    assert farmer.ee.next_move == 56.75
    assert farmer.ee.next_follow == 11.75


def test_movement_is_cancelled_if_heal_becomes_needed(tmp_path, monkeypatch):
    farmer, _ = runtime(tmp_path, monkeypatch, snapshot())
    farmer.ee = idle_movement_policy()
    farmer.latest.side_effect = [snapshot(), snapshot(hp=.1)]
    with pytest.raises(TargetChanged):
        farmer.execute_ee(EEDecision(skill="move"), 100)
    farmer.controller.key.assert_not_called()


def test_movement_does_not_exceed_scenario_deadline(tmp_path, monkeypatch):
    farmer, _ = runtime(tmp_path, monkeypatch, snapshot())
    farmer.ee = idle_movement_policy()
    monkeypatch.setattr("hid_pilot.farm_ee.random.randint", lambda low, high: high)
    farmer.execute_ee(EEDecision(skill="move"), 10.5)
    farmer.controller.key.assert_not_called()


@pytest.mark.parametrize("protocol,maximum", [("HID_PILOT_V3", 1000), ("HID_PILOT_V4", 3000)])
def test_ee_movement_duration_matches_protocol(tmp_path, monkeypatch, protocol, maximum):
    farmer, _ = runtime(tmp_path, monkeypatch, snapshot())
    farmer.ee = idle_movement_policy()
    farmer.controller.transport.protocol = protocol
    choose_duration = Mock(side_effect=lambda low, high: high)
    monkeypatch.setattr("hid_pilot.farm_ee.random.randint", choose_duration)
    farmer.execute_ee(EEDecision(skill="move"), 100)
    choose_duration.assert_called_once_with(300, maximum)
    assert farmer.controller.key.call_args.args[1] == maximum
