import pytest

from test_duo_self_heal import observed_duo


def buff_team(monkeypatch, mp=.8, **settings):
    settings.setdefault("support_self_heal_key", None)
    duo, controller, reader = observed_duo(monkeypatch, 1., **settings)
    resources = {"hp": 1., "mp": mp}
    reader.side_effect = lambda image, kind: resources[kind]
    duo.config = duo.config.model_copy(update={"buff_seconds": 15})
    return duo, controller, resources


@pytest.mark.parametrize("mp,can_buff", [(None, False), (.59, False), (.6, True), (.8, True)])
def test_full_buff_requires_known_sufficient_party_mp(monkeypatch, mp, can_buff):
    duo, controller, _ = buff_team(monkeypatch, mp)
    duo.buff()
    assert ((2, duo.config.buff_key) in controller.events) is can_buff


def test_ma_defends_but_does_not_interrupt_or_heal_support_during_macro(monkeypatch):
    duo, controller, _ = buff_team(monkeypatch, aggro_key="2", heal_key="F9",
                                    support_party_heal_key="F2")
    controller.capture = lambda: (.4 if controller.now >= .5 else 1., .9, False)
    duo.buff()
    assert duo.buffing
    assert (1, "2") in controller.events
    assert (1, duo.config.attack) in controller.events
    assert (2, "ESC") not in controller.events
    assert (2, "F9") not in controller.events
    assert (2, "F2") not in controller.events
    duo.on_target_missing()
    assert (2, duo.config.follow_key) not in controller.events
    controller.wait(16)
    duo.observe()
    assert not duo.buffing
    assert duo.next_buff > controller.now
    assert controller.events.count((2, duo.config.buff_key)) == 1


@pytest.mark.parametrize("support_hp,interrupted", [(.2, False), (.19, True)])
def test_only_support_hp_below_twenty_percent_can_cancel_macro(monkeypatch, support_hp, interrupted):
    duo, controller, resources = buff_team(monkeypatch, support_party_heal_key="F2", heal_key=None)
    old_wait = controller.wait

    def wait(seconds):
        old_wait(seconds)
        if controller.now >= .5:
            resources["hp"] = support_hp

    controller.wait = wait
    duo.buff()
    assert ((2, "ESC") in controller.events) is interrupted
    if interrupted:
        assert (2, "F2") in controller.events
        assert duo.next_buff == 0
        assert not duo.buffing
    else:
        assert duo.buffing


def test_mp_cost_during_active_buff_does_not_cancel_started_macro(monkeypatch):
    duo, controller, resources = buff_team(monkeypatch)
    old_wait = controller.wait

    def wait(seconds):
        old_wait(seconds)
        resources["mp"] = .1

    controller.wait = wait
    duo.buff()
    assert controller.events.count((2, duo.config.buff_key)) == 1
    assert (2, "ESC") not in controller.events
    assert duo.next_buff > controller.now
