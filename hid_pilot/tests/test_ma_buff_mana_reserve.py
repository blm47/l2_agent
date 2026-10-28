import pytest

from test_duo import make_duo
from test_melee_farm import harness


@pytest.mark.parametrize("duo_mode", [False, True])
def test_pending_rebuff_reserves_mana_until_own_buff_is_cast(duo_mode):
    settings = dict(buff1_key="F5", aggro_key="2", damage_keys=("F7",),
                    overhit_keys=("F8",), overhit_during_combat=True, vampiric_key="F9")
    if duo_mode:
        farmer, controller = make_duo(**settings)
        farmer.next_buff = float("inf")
    else:
        farmer, controller, _, _ = harness(**settings)
    farmer.last_mp = .05
    farmer.open_with_aggro()
    assert not farmer.use_damage_skill(.1, ("Gremlin", 1))
    assert not farmer.use_vampiric(.1, .9)
    assert farmer.saving_mana_for_buffs()
    farmer.last_mp = .1
    assert farmer.saving_mana_for_buffs()
    farmer.open_with_aggro()
    farmer.cast_buffs()
    assert farmer.buff_actions == 1
    assert not farmer.saving_mana_for_buffs()
    farmer.open_with_aggro()
    keys = [event[1] for event in controller.events if event[0] == 1] if duo_mode else [
        call.args[0] for call in controller.key.call_args_list]
    assert keys == ["F5", "2"]


def test_low_mp_combat_uses_only_normal_attack_and_loot_while_rebuff_pending():
    farmer, controller, _, state = harness(
        buff1_key="F5", aggro_key="2", damage_keys=("F7",), overhit_keys=("F8",),
        overhit_during_combat=True, vampiric_key="F9", regen_enabled=False, hp_threshold=.1,
    )
    state[:] = [.4, .05, .9]
    original = controller.key.side_effect

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name == farmer.config.attack:
            state[2] = max(0., state[2] - .3)

    controller.key.side_effect = key
    farmer.run_cycle(0)
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys.count(farmer.config.attack) >= 3
    assert keys[-1] == farmer.config.loot
    assert set(keys) <= {farmer.config.next_target, farmer.config.attack, farmer.config.loot}


def test_recovered_mp_casts_queued_buff_before_damage_skill_resumes():
    farmer, controller, _, state = harness(
        buff1_key="F5", overhit_keys=("F7",), overhit_during_combat=True, regen_enabled=False,
    )
    state[:] = [.8, .05, .9]
    original = controller.key.side_effect
    attacks = [0]

    def key(name, duration, **kwargs):
        original(name, duration, **kwargs)
        if name == farmer.config.attack:
            attacks[0] += 1
            state[2] -= .05
            if attacks[0] >= 2:
                state[1] = .1
        elif name == "F7":
            state[2] = 0.

    controller.key.side_effect = key
    farmer.run_cycle(0)
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys.count(farmer.config.attack) >= 2
    assert keys.index("F5") < keys.index("F7")
    assert not farmer.saving_mana_for_buffs()


def test_low_mp_does_not_reserve_before_buff_deadline_or_for_disabled_buffs():
    farmer, _, now, _ = harness(buff1_key="F5", buff_delay_minutes=5)
    farmer.last_mp = .01
    assert not farmer.saving_mana_for_buffs()
    now[0] = 300
    assert farmer.saving_mana_for_buffs()
    farmer.config = farmer.config.model_copy(update={"buff1_key": None})
    assert not farmer.saving_mana_for_buffs()


def test_pending_support_buff_does_not_reserve_ma_mana():
    duo, _ = make_duo()
    duo.last_mp = .01
    assert duo.buffs_due()
    assert not duo.saving_mana_for_buffs()


def test_ud_does_not_spend_ma_mana_while_self_rebuff_is_waiting():
    duo, controller = make_duo(buff1_key="F5", ud_key="F6", heal_key=None)
    duo.on_observation(None, .06, .05, .9)
    controller.wait(.2)
    duo.on_observation(None, .06, .05, .9)
    assert controller.events == []
    assert duo.ud_ready_at == float("-inf")
