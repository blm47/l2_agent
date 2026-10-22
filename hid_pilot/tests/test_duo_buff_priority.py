import pytest

from test_duo import make_duo
from test_duo_self_heal import observed_duo
from test_melee_farm import harness


def test_default_first_buff_delay_is_five_minutes_for_both_roles():
    duo, controller = make_duo(buff_delay_minutes=5, buff1_key="NUM9")
    duo.cast_buffs()
    assert controller.events == []
    assert duo.next_buff == 300
    assert duo.buff_ready[1] == 300
    controller.now = 300
    duo.cast_buffs()
    assert (2, duo.config.buff_key) in controller.events
    assert (1, "NUM9") in controller.events


def test_ma_and_healer_cast_buffs_concurrently():
    duo, controller = make_duo(buff1_key="NUM9", buff1_cast_seconds=2,
                               buff2_key="NUM0", buff2_cast_seconds=5)
    duo.config = duo.config.model_copy(update={"buff_seconds": 15})
    sent = []
    original_key = controller.key

    def key(name, duration):
        sent.append((controller.role.hwnd, name, controller.now))
        original_key(name, duration)

    controller.key = key
    duo.cast_buffs()
    healer_started = next(at for hwnd, name, at in sent if hwnd == 2 and name == "F1")
    ma_started = next(at for hwnd, name, at in sent if hwnd == 1 and name == "NUM9")
    second_started = next(at for hwnd, name, at in sent if hwnd == 1 and name == "NUM0")
    assert ma_started < healer_started + 1
    assert second_started < healer_started + 15
    assert controller.now < 17
    assert duo.next_buff > 0
    assert duo.buff_ready[1] > 0


def test_support_damage_interrupts_only_ma_buff_and_support_finishes_macro(monkeypatch):
    duo, controller, reader = observed_duo(
        monkeypatch, 1., aggro_key="2", heal_key=None, support_self_heal_key=None,
        support_party_heal_key="F2", buff1_key="NUM9", buff1_cast_seconds=2,
        buff2_key="NUM0", buff2_cast_seconds=5,
    )
    duo.config = duo.config.model_copy(update={"buff_seconds": 15})
    healed = [False]
    reader.side_effect = lambda *args: .9 if healed[0] else (.3 if controller.now >= .5 else 1.)
    original_key = controller.key

    def key(name, duration):
        original_key(name, duration)
        if controller.role.hwnd == 2 and name == "F2":
            healed[0] = True

    controller.key = key
    controller.capture = lambda: (.4 if controller.now >= .5 else 1., .9, False)
    duo.cast_buffs()
    events = controller.events
    assert events.index((1, "2")) < events.index((1, duo.config.attack))
    assert (2, "F2") not in events
    assert (1, "NUM0") not in events
    assert (2, "ESC") not in events
    assert (1, "ESC") not in events
    assert duo.next_buff == 0
    assert duo.buff_ready[1] == 0
    controller.capture = lambda: (1., 0., False)
    duo.fight(.9)
    controller.wait(5)
    duo.cast_buffs()
    assert controller.events.count((1, "NUM9")) == 2
    assert (1, "NUM0") in controller.events
    assert duo.buffing is True
    controller.wait(20)
    duo.observe()
    assert duo.next_buff > controller.now
    assert controller.events.count((2, "F1")) == 1
    assert duo.buff_ready[1] > controller.now


def test_aggro_opens_each_monster_before_attack_or_overhit():
    farmer, controller, _, state = harness(aggro_key="2", overhit_keys=("F7",))
    state[2] = 0.
    farmer.verify_target = lambda hp, mp, target, identity: (hp, mp, target, True)
    farmer.fight(.9)
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys.index("2") < keys.index(farmer.config.attack)
    assert keys.count("2") == 1
    controller.key.reset_mock()
    farmer.fight(.01)
    keys = [call.args[0] for call in controller.key.call_args_list]
    assert keys.index("2") < keys.index("F7")


def test_healthy_ma_can_buff_while_attack_is_active():
    farmer, controller, _, _ = harness(buff1_key="NUM9", buff2_key="NUM0")
    old_wait = controller.wait.side_effect

    def wait(seconds):
        old_wait(seconds)
        farmer.attacking = True

    controller.wait.side_effect = wait
    farmer.cast_buffs()
    assert [call.args[0] for call in controller.key.call_args_list] == ["NUM9", "NUM0"]


def test_party_hp_is_reported_for_gui_and_pending_buffs(monkeypatch):
    duo, _, _ = observed_duo(monkeypatch, .4, support_self_heal_key=None)
    payloads = []
    duo.observation = payloads.append
    duo.observe()
    assert payloads[-1]["hp"] == 1.
    assert payloads[-1]["support_hp"] == pytest.approx(.4)
    assert payloads[-1]["buffs_pending"] is True


def test_mass_heal_of_low_party_support_is_independent_of_single_heal_cooldown(monkeypatch):
    duo, controller, _ = observed_duo(
        monkeypatch, .25, support_party_heal_key="F2", support_self_heal_key=None,
        heal_cooldown=10,
    )
    duo.last_heal = controller.now
    duo.observe()
    assert (2, "F2") in controller.events
    assert controller.events.count((2, "F2")) == 1
    duo.observe()
    assert controller.events.count((2, "F2")) == 1


def test_aggro_is_not_used_as_overhit_even_in_legacy_configuration():
    farmer, controller, _, _ = harness(aggro_key="2", heal="F2", overhit_keys=("2",))
    assert not farmer.use_damage_skill(.01, None)
    controller.key.assert_not_called()


def test_damage_does_not_cancel_support_macro_when_no_heal_was_cast():
    duo, controller = make_duo(heal_key=None, aggro_key="2", buff1_key="NUM9")
    duo.config = duo.config.model_copy(update={"buff_seconds": 15})
    controller.capture = lambda: (.8 if controller.now >= .5 else 1., .9, False)
    duo.cast_buffs()
    assert (1, "2") in controller.events
    assert (1, duo.config.attack) in controller.events
    assert (2, "ESC") not in controller.events
    assert duo.buffing is True
    assert duo.next_buff == 0
