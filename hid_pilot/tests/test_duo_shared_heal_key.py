import pytest

from test_duo_self_heal import observed_duo


def test_f9_shared_by_single_and_party_heal_cannot_repeat_after_five_seconds(monkeypatch):
    duo, controller, _ = observed_duo(
        monkeypatch, 1., heal_key="F9", support_party_heal_key="F9",
        heal_cooldown=12, support_party_heal_cooldown=5,
    )
    controller.capture = lambda: (.4, .9, False)
    duo.observe()
    healed_at = duo.last_heal
    for delay in (5, 8, 11.99):
        controller.now = healed_at + delay
        duo.observe()
        assert controller.events.count((2, "F9")) == 1
    controller.now = duo.support_party_heal_ready
    duo.observe()
    assert controller.events.count((2, "F9")) == 2


def test_shared_f9_uses_only_party_threshold_and_cannot_bypass_cooldown(monkeypatch):
    duo, controller, _ = observed_duo(
        monkeypatch, 1., heal_key="F9", support_party_heal_key="F9",
        heal_threshold=.7, support_party_heal_threshold=.5, heal_cooldown=12,
    )
    controller.capture = lambda: (.6, .9, False)
    duo.observe()
    assert controller.events.count((2, "F9")) == 0
    controller.capture = lambda: (.4, .9, False)
    duo.observe()
    assert controller.events.count((2, "F9")) == 1
    controller.now = duo.last_heal + 11.99
    duo.observe()
    assert controller.events.count((2, "F9")) == 1
    controller.now = duo.last_heal + 12
    duo.observe()
    assert controller.events.count((2, "F9")) == 2


@pytest.mark.parametrize("global_cooldown,party_cooldown", [(12, 5), (2, 15)])
def test_same_button_uses_longest_assigned_cooldown(monkeypatch, global_cooldown, party_cooldown):
    duo, _, _ = observed_duo(
        monkeypatch, 1., heal_key="F9", support_party_heal_key="F9",
        heal_cooldown=global_cooldown, support_party_heal_cooldown=party_cooldown,
    )
    duo.mark_support_heal("F9")
    assert duo.support_heal_ready["F9"] - duo.last_heal == max(global_cooldown, party_cooldown)


def test_party_heal_keeps_independent_cooldown_when_assigned_to_separate_key(monkeypatch):
    duo, controller, _ = observed_duo(
        monkeypatch, .3, heal_key="F9", support_party_heal_key="F7",
        support_self_heal_key=None, heal_cooldown=12, support_party_heal_cooldown=5,
    )
    duo.mark_support_heal("F9")
    duo.observe()
    assert (2, "F7") in controller.events
