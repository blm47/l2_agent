import pytest

from test_duo_self_heal import observed_duo


def test_support_potion_uses_own_hp_and_cooldown(monkeypatch):
    duo, controller, reader = observed_duo(
        monkeypatch, .3, support_self_heal_key=None,
        support_potion_key="F5", support_potion_hp_threshold=.4,
    )
    duo.observe()
    assert (2, "F5") in controller.events
    duo.observe()
    assert controller.events.count((2, "F5")) == 1
    controller.wait(duo.config.support_potion_cooldown)
    reader.return_value = .5
    duo.observe()
    assert controller.events.count((2, "F5")) == 1


def test_support_vampiric_assists_then_casts_and_returns(monkeypatch):
    duo, controller, _ = observed_duo(
        monkeypatch, .4, support_vampiric_key="F6", support_self_heal_key=None,
        support_vampiric_cast_seconds=2,
    )
    duo.observe()
    assert controller.events == [
        ("window", 2), (2, duo.config.support_key), (2, "F6"), ("window", 1)]
    assert controller.now == pytest.approx(duo.config.support_seconds + 2)
    duo.observe()
    assert controller.events.count((2, "F6")) == 1


@pytest.mark.parametrize("support_hp,target", [(None, .9), (.6, .9), (.4, .5), (.4, None), (0, .9)])
def test_support_vampiric_needs_own_low_hp_and_healthy_monster(monkeypatch, support_hp, target):
    duo, controller, _ = observed_duo(
        monkeypatch, support_hp, support_vampiric_key="F6", support_self_heal_key=None,
    )
    controller.capture = lambda: (1., target, False)
    duo.observe()
    assert (2, "F6") not in controller.events


@pytest.mark.parametrize("farmer_hp,support_hp", [(.4, .9), (.9, .4)])
def test_party_heal_has_separate_key_and_cooldown(monkeypatch, farmer_hp, support_hp):
    duo, controller, _ = observed_duo(
        monkeypatch, support_hp, support_party_heal_key="F7", heal_key=None,
        support_self_heal_key=None,
    )
    controller.capture = lambda: (farmer_hp, .9, False)
    duo.observe()
    assert (2, "F7") in controller.events
    assert (2, "F4") not in controller.events
    duo.observe()
    assert controller.events.count((2, "F7")) == 1
