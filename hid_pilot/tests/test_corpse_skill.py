from unittest.mock import Mock

import pytest

from hid_pilot.duo import DuoConfig, format_duo_settings, parse_duo_settings
from hid_pilot.farm_settings import setting_group
from hid_pilot.target import TargetObservation
from test_melee_farm import harness


def test_corpse_settings_roundtrip_and_group():
    config = DuoConfig(corpse_key="F8", corpse_cast_seconds=2, corpse_cooldown=8)
    assert parse_duo_settings(format_duo_settings(config)) == config
    assert setting_group("corpse_key") == "МА — скилл на труп"
    assert DuoConfig(corpse_key="").corpse_key is None
    for key in ("ALT+F4", config.attack, config.next_target, config.loot):
        with pytest.raises(ValueError):
            DuoConfig(corpse_key=key)


@pytest.mark.parametrize("kind,hp,marker,same,paused,expected", [
    ("monster", 0., False, True, False, True),
    ("monster", None, True, True, False, True),
    ("monster", .4, False, True, False, False),
    ("monster", .4, True, True, False, False),
    ("monster", None, False, True, False, False),
    ("player", 0., True, True, False, False),
    ("npc", 0., True, True, False, False),
    ("unknown", 0., True, True, False, False),
    ("monster", 0., True, False, False, False),
    ("monster", 0., True, True, True, False),
])
def test_corpse_skill_requires_fresh_same_dead_monster(kind, hp, marker, same, paused, expected):
    farmer, controller, now, _ = harness(corpse_key="F8", corpse_cast_seconds=2)
    controller.pause_generation = 0
    farmer.last_target = TargetObservation(at=0)
    farmer.monster_identity = Mock(return_value=("Моб", 20) if same else ("Другой", 20))

    farmer.last_target = TargetObservation(at=0, kind=kind, hp=hp, death_marker=marker)
    farmer.observe = Mock(side_effect=AssertionError("Unexpected observation"))
    if paused:
        controller.pause_generation = 1
    farmer.use_corpse_skill((farmer.monster_identity.return_value[0] if same else "missing", 20), generation=0)
    if expected:
        controller.key.assert_called_once_with("F8", 100)
        assert now[0] == pytest.approx(.1)
        farmer.use_corpse_skill(("Моб", 20))
        controller.key.assert_called_once()
    else:
        controller.key.assert_not_called()


def test_confirmed_kill_uses_corpse_skill_before_loot():
    farmer, controller, now, _ = harness(corpse_key="F8")
    controller.pause_generation = 0
    farmer.monster_identity = Mock(return_value=("Моб", 20))

    def observe():
        now[0] += .01
        hp = 0. if now[0] >= 1.4 else .8
        farmer.last_target = TargetObservation(at=now[0], kind="monster", name="Моб", hp=hp)
        return 1., 1., hp

    farmer.observe = observe
    farmer.fight(1.)
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys == ["R", "F8", farmer.config.loot]
