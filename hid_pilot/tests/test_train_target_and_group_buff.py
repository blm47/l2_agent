from unittest.mock import Mock

import pytest
from test_healer import snapshot
from test_healer_runtime import harness
from test_train_healer import state
from test_train_runtime import runtime

from hid_pilot.healer import HealDecision, HealerConfig, HealPolicy
from hid_pilot.party import PartyMember
from hid_pilot.train_healer import TrainConfig, TrainPhase, TrainPolicy


def damaged_party(at, slot, hp):
    return state(at).model_copy(update={
        "members": tuple(PartyMember(slot=index, hp=hp if index == slot else 1., cp=1., mp=1.)
                         for index in range(1, 9))})


@pytest.mark.parametrize("slot", range(1, 9))
@pytest.mark.parametrize("phase", list(TrainPhase))
def test_mass_heal_precedes_targeting_when_damage_continues(slot, phase):
    policy = TrainPolicy(TrainConfig(group_cast=.1, verify_seconds=.1), 0.)
    for at, hp in ((0., .94), (.1, .90), (.2, .86)):
        current = damaged_party(at, slot, hp)
        policy.ingest(current)
    policy.phase = phase
    assert not policy.mass_opened
    result = policy.decide(current, current.at)
    assert (result.skill, result.target) == ("group", None)
    policy.committed(result, current, current.at)
    for at, hp in ((.31, .86), (.41, .82), (.51, .78)):
        current = damaged_party(at, slot, hp)
        policy.ingest(current)
    result = policy.decide(current, current.at)
    assert (result.skill, result.target) == ("major", f"@slot{slot}")


@pytest.mark.parametrize("hp,skill", [(.8, "major"), (.4, "battle")])
def test_runtime_clicks_strong_damage_target_before_healing_even_if_hp_recovers(monkeypatch, tmp_path, hp, skill):
    healer, controller, observer, now = runtime(monkeypatch, tmp_path)
    healer.config.group_cast = .1
    healer.config.verify_seconds = .1
    healer.step_after_heal = Mock()
    for at, value in ((9.6, hp + .16), (9.8, hp + .12), (10., hp + .08)):
        current = state(at, others=(value, 1.))
        healer.policy.ingest(current)
    observer.snapshots.return_value = (current,)
    first = healer.policy.decide(current, now[0])
    assert first.skill == "group"
    healer.execute(first, current)
    controller.click.assert_not_called()
    controller.key.assert_called_once_with("F6", 100)
    controller.reset_mock()
    for at, value in ((10.11, hp + .08), (10.21, hp + .04), (10.31, hp)):
        current = state(at, others=(value, 1.))
        healer.policy.ingest(current)
    now[0] = current.at
    observer.snapshots.return_value = (current,)
    decision = healer.policy.decide(current, now[0])
    order = []

    def click(*args, verify):
        order.append("click")
        now[0] = 10.41
        observer.snapshots.return_value = (state(now[0], others=(hp + .01, 1.)),)
        verify()

    controller.click.side_effect = click
    controller.key.side_effect = lambda *args: order.append("key")
    healer.execute(decision, current)
    assert order == ["click", "key"]
    controller.click.assert_called_once()
    controller.key.assert_called_once_with(getattr(healer.config, skill + "_key"), 100)
    assert healer.active_cast[0].target == "@slot1"


def test_unknown_other_member_does_not_block_known_strong_damage_target():
    policy = TrainPolicy(TrainConfig(group_cast=.1, verify_seconds=.1), 0.)
    initial = state(0., others=(.9, 1.))
    policy.ingest(initial)
    policy.committed(HealDecision(kind="cast", skill="group", reason="test"), initial, 0.)
    for at, hp in ((.11, .9), (.21, .86), (.31, .82)):
        current = state(at, others=(hp, None), known=False)
        policy.ingest(current)
    result = policy.decide(current, current.at)
    assert (result.skill, result.target) == ("major", "@slot1")


@pytest.mark.parametrize("change", ["gap", "revision", "dead", "destroyer"])
def test_changed_or_invalid_observation_cancels_strong_damage_target(change):
    policy = TrainPolicy(TrainConfig(group_cast=.1, verify_seconds=.1), 0.)
    initial = state(0., others=(.9, 1.))
    policy.ingest(initial)
    policy.committed(HealDecision(kind="cast", skill="group", reason="test"), initial, 0.)
    for at, hp in ((.11, .9), (.21, .86), (.31, .82)):
        current = state(at, others=(hp, 1.))
        policy.ingest(current)
    assert policy.urgent_single(current, "@slot1")
    current = state(1.2 if change == "gap" else .41, others=(0. if change == "dead" else .83, 1.))
    if change == "revision":
        current = current.model_copy(update={"profile_revision": 1})
    if change == "destroyer":
        current = current.model_copy(update={"destroyer_slots": (1,)})
    policy.ingest(current)
    assert not policy.urgent_single(current, "@slot1")


@pytest.mark.parametrize("factory,config,make_state", [
    (HealPolicy, HealerConfig, snapshot), (TrainPolicy, TrainConfig, state)])
@pytest.mark.parametrize("chain", [False, True])
def test_group_buff_blocks_repeat_for_fifteen_seconds_after_cast(factory, config, make_state, chain):
    policy = factory(config(group_cast=.4), 0.)
    policy.chain_heals = chain
    policy.committed(HealDecision(kind="cast", skill="group", reason="test"), make_state(0.), 0.)
    assert not policy.ready("group", 15.399)
    assert policy.ready("group", 15.4)
    assert policy.ready("mass_major", .4)


def test_buff_uses_mass_major_when_party_needs_mass_heal_again():
    policy = TrainPolicy(TrainConfig(group_cast=.1, verify_seconds=.1), 0.)
    current = state(0., hp=.6, tank=.6, others=(.6, .6))
    first = policy.decide(current, 0.)
    assert first.skill == "group"
    policy.committed(first, current, 0.)
    policy.ingest(state(.1, hp=.6, tank=.6, others=(.6, .6)))
    current = state(.2, hp=.6, tank=.6, others=(.6, .6))
    assert policy.decide(current, .2).skill == "mass_major"


def test_damage_before_mass_cast_does_not_count_as_failed_mass_healing():
    policy = TrainPolicy(TrainConfig(group_cast=.4, verify_seconds=.1), 0.)
    for at, hp in ((0., .94), (.1, .90), (.2, .86)):
        current = state(at, tank=hp)
        policy.ingest(current)
    first = policy.decide(current, .2)
    assert first.skill == "group"
    policy.committed(first, current, .2)
    for at, hp in ((.3, .82), (.4, .78), (.5, .74), (.61, .70), (.71, .66)):
        current = state(at, tank=hp)
        policy.ingest(current)
        assert not policy.urgent_single(current, "@slot3")
    current = state(.81, tank=.62)
    policy.ingest(current)
    assert policy.urgent_single(current, "@slot3")
    assert policy.decide(current, current.at).target == "@slot3"


def test_stable_hp_after_mass_heal_does_not_allow_targeted_heal():
    policy = TrainPolicy(TrainConfig(group_cast=.1, verify_seconds=.1), 0.)
    initial = state(0., tank=.5)
    policy.ingest(initial)
    policy.committed(HealDecision(kind="cast", skill="group", reason="test"), initial, 0.)
    for at in (.11, .21, .31):
        current = state(at, tank=.6)
        policy.ingest(current)
    assert "@slot3" in policy.reachable_until
    assert not policy.needs_single(current, "@slot3")
    result = policy.decide(current, current.at)
    assert result.target is None or result.kind == "follow"


def test_group_buff_timer_survives_restart_and_includes_aura_delay(monkeypatch, tmp_path):
    healer, controller, observer, _initial, now = harness(monkeypatch, tmp_path)
    healer.config.aura_enabled = True
    healer.config.group_cast = .4
    current = snapshot(at=now[0], party=(.65, .65, .65))
    observer.snapshots.return_value = (current,)
    controller.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
    healer.execute(HealDecision(kind="cast", skill="group", reason="test"), current)
    until = now[0] + .4 + 15
    assert healer.policy.ready_at["group"] == pytest.approx(until)
    healer.policy.ready_at.clear()
    healer.restore_timers()
    assert healer.policy.ready_at["group"] == pytest.approx(until)
