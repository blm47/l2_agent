from pathlib import Path

import pytest

from hid_pilot.auto_loot import AutoLoot
from hid_pilot.farm_ee import EEConfig
from hid_pilot.healer import HealerConfig, HealPolicy
from hid_pilot.script import parse_script
from hid_pilot.train_healer import TrainConfig, TrainPolicy
from test_farm_ee import policy, runtime
from test_healer import snapshot
from test_healer_runtime import harness
from test_train_healer import state as train_state


def loot_runtime(tmp_path, monkeypatch):
    farmer, now = runtime(tmp_path, monkeypatch, snapshot())
    farmer.ee.ready_at.update(clarity=1000, shock=1000, gain=1000, kiss=1000, bless=1200)
    farmer.ee.next_follow = 100
    farmer.loot = AutoLoot(0)
    farmer.loot.next_at = 10
    return farmer, now


def test_ee_loot_uses_bishop_series_and_configured_key(tmp_path, monkeypatch):
    farmer, now = loot_runtime(tmp_path, monkeypatch)
    farmer.config.loot_key = "V"
    monkeypatch.setattr("hid_pilot.auto_loot.random.randint", lambda low, high: 3)
    monkeypatch.setattr("hid_pilot.auto_loot.random.uniform", lambda low, high: (low + high) / 2)
    assert farmer.try_auto_loot(100)
    farmer.controller.key.assert_called_once_with("V", 100)
    assert farmer.loot.remaining == 2
    assert farmer.loot.next_at == 10.5
    assert not farmer.try_auto_loot(100)


@pytest.mark.parametrize("block", ["disabled", "heal", "mana", "buff", "cast", "follow", "dead", "low_mp", "stale", "deadline"])
def test_ee_loot_does_not_preempt_support_or_ignore_safety(tmp_path, monkeypatch, block):
    farmer, _ = loot_runtime(tmp_path, monkeypatch)
    current = snapshot()
    if block == "disabled":
        farmer.config.auto_loot = False
    elif block == "heal":
        current = snapshot(party=(.1,))
    elif block == "mana":
        current = current.model_copy(update={"members": (
            current.members[0].model_copy(update={"mp": .1}),)})
    elif block == "buff":
        farmer.ee.ready_at.pop("clarity")
    elif block == "cast":
        farmer.ee.busy_until = 11
    elif block == "follow":
        farmer.ee.next_follow = 0
    elif block == "dead":
        current = snapshot(party=(0.,))
    elif block == "low_mp":
        current = snapshot(mp=.04)
    elif block == "stale":
        current = snapshot(at=8)
    farmer.latest.return_value = current
    assert not farmer.try_auto_loot(10.05 if block == "deadline" else 100)
    farmer.controller.key.assert_not_called()


def test_ee_ring_settings_control_activation_key_and_timings():
    p = policy()
    p.config.circlet_of_heal_hp = .50
    p.config.circlet_of_heal_min_members = 2
    p.config.circlet_of_heal_key = "F3"
    p.config.circlet_of_heal_cast = .8
    p.config.circlet_of_heal_cooldown = 20
    assert p.decide(snapshot(party=(.49, .49)), 10).skill == "circlet_of_heal"
    assert p.timing("circlet_of_heal") == ("F3", .8, 20)
    p.config.circlet_of_heal_enabled = False
    assert p.decide(snapshot(party=(.1, .1, .1)), 10).skill != "circlet_of_heal"


@pytest.mark.parametrize("train", [False, True])
def test_bishop_ring_uses_configured_threshold_count_and_balance_cooldown(train):
    config_class = TrainConfig if train else HealerConfig
    policy_class = TrainPolicy if train else HealPolicy
    config = config_class(circlet_of_heal_enabled=True, circlet_of_heal_hp=.5, circlet_of_heal_min_members=2,
                          circlet_of_heal_key="F3", circlet_of_heal_cast=.7, circlet_of_heal_cooldown=20,
                          self_rescue_key=None)
    p = policy_class(config, 0)
    p.ready_at["balance"] = 30
    current = train_state(0, tank=.49, others=(.49, 1.)) if train else snapshot(at=0, party=(.49, .49))
    assert p.decide(current, 0).skill == "circlet_of_heal"
    assert config.cast_seconds("circlet_of_heal") == .7
    assert config.cooldown("circlet_of_heal") == 20
    config.circlet_of_heal_min_members = 3
    assert p.decide(current, 0).skill != "circlet_of_heal"
    config.circlet_of_heal_min_members = 2
    config.circlet_of_heal_enabled = False
    assert p.decide(current, 0).skill != "circlet_of_heal"


@pytest.mark.parametrize("config_class", [EEConfig, HealerConfig, TrainConfig])
@pytest.mark.parametrize("values", [{"circlet_of_heal_hp": 0}, {"circlet_of_heal_hp": 1.1},
                                    {"circlet_of_heal_min_members": 0}, {"circlet_of_heal_min_members": 10},
                                    {"circlet_of_heal_cast": 0}, {"circlet_of_heal_cooldown": 0},
                                    {"circlet_of_heal_key": "INVALID"}])
def test_ring_settings_reject_invalid_values(config_class, values):
    with pytest.raises(ValueError):
        config_class(**values)


@pytest.mark.parametrize("script", ["farm_ee", "healer", "healer-train"])
def test_ring_and_loot_script_settings_parse(script):
    path = Path(__file__).parents[1] / "extensions" / "scripts" / f"{script}.pilot"
    assert parse_script(path.read_text("utf-8"))


@pytest.mark.parametrize("ee,legacy", [(False, "area_heal"), (True, "area_heal"), (True, "area")])
def test_old_circlet_cooldown_is_migrated_without_shortening(ee, legacy, tmp_path, monkeypatch):
    if ee:
        actor, _now = runtime(tmp_path, monkeypatch, snapshot())
        timers = actor.ee.ready_at
    else:
        actor, _controller, _observer, _state, _now = harness(monkeypatch, tmp_path)
        timers = actor.policy.ready_at
    timers.clear()
    timers.update({legacy: 40., "circlet_of_heal": 30.})
    actor.save_timers()
    timers.clear()
    actor.restore_timers()
    restored = actor.ee.ready_at if ee else actor.policy.ready_at
    assert restored["circlet_of_heal"] == 40.
    assert legacy not in restored
