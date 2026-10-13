import cv2
from test_healer import snapshot
from test_paralysis import scene

from hid_pilot.healer import HealerConfig, HealPolicy
from hid_pilot.healer_observer import HealerReader
from hid_pilot.train_healer import TrainConfig, TrainPolicy


def test_destroyer_icon_is_detected_on_member_and_blocks_balance(tmp_path):
    image, own, party, _detector = scene(tmp_path)
    reader = HealerReader(own, party)
    icon = cv2.resize(reader.destroyer.template, (24, 23), interpolation=cv2.INTER_AREA)
    image[81:104, 240:264] = icon
    state = reader.read(image, 1)
    assert state.destroyer_slots == (2,)
    for cls, cfg in [(HealPolicy, HealerConfig), (TrainPolicy, TrainConfig)]:
        policy = cls(cfg(), 0)
        policy.ingest(state)
        assert not policy.ready("balance", 1)


def test_balance_unlocks_when_icon_is_no_longer_observed():
    policy = HealPolicy(HealerConfig(), 0)
    policy.ingest(snapshot(at=1).model_copy(update={"destroyer_slots": (1,)}))
    assert not policy.ready("balance", 1)
    policy.ingest(snapshot(at=1.2))
    assert policy.ready("balance", 1.2)


def test_destroyer_at_low_hp_gets_no_heal_that_could_exceed_twenty_percent():
    state = snapshot(at=1, party=(.1, .6)).model_copy(update={"destroyer_slots": (1,)})
    policy = HealPolicy(HealerConfig(mp_buff_key=None), 0)
    policy.follow_needed = False
    policy.ingest(state)
    assert not policy.needs_single(state, "@slot1")
    assert not policy.needs_battle(state, "@slot1")
    decision = policy.decide(state, 1)
    assert (decision.skill, decision.target) == ("major", "@slot2")
    assert not policy.ready("balance", 1)
    for skill in ("group", "mass_major", "avatar"):
        assert policy.ready(skill, 1)


def test_space_probe_remains_allowed_with_destroyer():
    state = snapshot(at=1).model_copy(update={"destroyer_slots": (2,)})
    policy = HealPolicy(HealerConfig(), 0)
    policy.stun_suspected = True
    assert policy.decide(state, 1).kind == "probe"


def test_train_emergency_respects_destroyer_rule():
    from test_train_healer import state

    policy = TrainPolicy(TrainConfig(), 0)
    current = state(0, tank=.1).model_copy(update={"destroyer_slots": (3,)})
    decision = policy.decide(current, 0)
    assert decision.skill != "balance"
    assert not (decision.kind == "cast" and decision.target == "@slot3" and decision.skill in {"major", "battle"})


def test_train_does_not_group_heal_only_for_destroyers_low_hp():
    from test_train_healer import state

    from hid_pilot.train_healer import TrainPhase

    policy = TrainPolicy(TrainConfig(mp_buff_key=None), 0)
    initial = state(0, tank=.1).model_copy(update={"destroyer_slots": (3,)})
    policy.ingest(initial)
    policy.phase = TrainPhase.COMBAT
    decision = policy.decide(initial.model_copy(update={"at": .1}), .1)
    assert decision.skill not in {"group", "mass_major", "avatar", "balance"}
