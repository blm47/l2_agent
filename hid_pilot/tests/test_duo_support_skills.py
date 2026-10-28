import pytest

from hid_pilot.duo import SupportDied
from test_duo import start_ma_attack
from test_duo_self_heal import observed_duo


@pytest.mark.parametrize("heal_settings,support_hp,farmer_hp,heal_key", [
    ({"heal_key": "F9", "support_self_heal_key": None}, .9, .4, "F9"),
    ({"heal_key": None, "support_self_heal_key": "F2"}, .4, .9, "F2"),
    ({"heal_key": None, "support_self_heal_key": None,
      "support_party_heal_key": "F7"}, .4, .9, "F7"),
])
@pytest.mark.parametrize("remaining_hp", [.4, 0., None])
def test_heal_restarts_assist_only_for_fresh_living_target(
        monkeypatch, heal_settings, support_hp, farmer_hp, heal_key, remaining_hp):
    duo, controller, _ = observed_duo(monkeypatch, support_hp, **heal_settings)
    start_ma_attack(duo, controller)
    controller.capture = lambda: (
        farmer_hp, remaining_hp if (2, heal_key) in controller.events else .9, False)
    duo.observe()
    assert (2, heal_key) in controller.events
    assert ((2, duo.config.support_key) in controller.events) is (remaining_hp is not None and remaining_hp > 0)
    if remaining_hp:
        assert controller.events.index((2, heal_key)) < controller.events.index((2, duo.config.support_key))
        assert controller.events.count((2, duo.config.support_key)) == 1


def test_assist_waits_for_focus_and_uses_exact_press(monkeypatch):
    duo, controller, _ = observed_duo(monkeypatch, .9)
    start_ma_attack(duo, controller)
    timeline = []
    key = controller.key

    def press(name, duration, **kwargs):
        timeline.append((name, duration, kwargs, controller.now, controller.role))
        key(name, duration, **kwargs)

    controller.key = press
    duo.support_pending = True
    duo.attacking = True
    duo.observe()
    name, duration, options, when, role = timeline[0]
    assert name == duo.config.support_key
    assert duration == 150
    assert options == {"exact": True}
    assert when == pytest.approx(.5)
    assert role == duo.support
    assert not duo.support_pending


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
    assert controller.now == pytest.approx(.15 + duo.config.support_seconds + 2)
    duo.observe()
    assert controller.events.count((2, "F6")) == 1


@pytest.mark.parametrize("support_hp,target", [(None, .9), (.6, .9), (.4, 0), (.4, None), (0, .9)])
def test_support_vampiric_needs_own_low_hp_and_living_monster(monkeypatch, support_hp, target):
    duo, controller, _ = observed_duo(
        monkeypatch, support_hp, support_vampiric_key="F6", support_self_heal_key=None,
    )
    controller.capture = lambda: (1., target, False)
    if support_hp == 0:
        # Нулевой HP видимой строки означает смерть и запрещает новые лечебные команды.
        with pytest.raises(SupportDied):
            duo.observe()
    else:
        duo.observe()
    assert (2, "F6") not in controller.events


@pytest.mark.parametrize("support_hp,target,expected", [
    (.59, .3001, True), (.5999, 1., True), (.6, .9, False),
    (.61, .9, False), (.59, .3, True), (.59, .01, True), (.59, .0001, True),
    (.59, .5, True), (.59, None, False),
    (.59, 0., False), (0., .9, False), (None, .9, False),
])
def test_support_vampiric_default_thresholds_are_strict(monkeypatch, support_hp, target, expected):
    duo, controller, _ = observed_duo(
        monkeypatch, support_hp, support_vampiric_key="F6", support_self_heal_key=None,
    )
    controller.capture = lambda: (1., target, False)
    if support_hp == 0:
        with pytest.raises(SupportDied):
            duo.observe()
    else:
        duo.observe()
    assert ((2, "F6") in controller.events) is expected


def test_empty_support_vampiric_key_disables_cast(monkeypatch):
    duo, controller, _ = observed_duo(
        monkeypatch, .55, support_vampiric_key="", support_self_heal_key=None,
    )
    duo.observe()
    assert controller.events == []


@pytest.mark.parametrize("heal_key", ["F9", "F6"])
def test_support_vampiric_uses_own_cooldown_despite_recent_heal(monkeypatch, heal_key):
    duo, controller, _ = observed_duo(
        monkeypatch, .4, support_vampiric_key="F6", support_self_heal_key=None,
        heal_key=heal_key, heal_cooldown=12, support_vampiric_cooldown=4)
    controller.capture = lambda: (1., .4, False)
    duo.mark_support_heal(heal_key)
    duo.observe()
    assert controller.events.count((2, "F6")) == 1
    ready = duo.support_vampiric_ready
    controller.now = ready - .01
    duo.observe()
    assert controller.events.count((2, "F6")) == 1
    duo.mark_support_heal(heal_key)
    controller.now = ready
    duo.observe()
    assert controller.events.count((2, "F6")) == 2


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
