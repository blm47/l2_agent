import pytest

from hid_pilot.bars import BarProfile
from hid_pilot.target import TargetObservation
from test_melee_farm import harness


def test_target_changes_and_hp_availability_are_logged_without_per_frame_spam(caplog):
    farmer, _, _, _ = harness()
    with caplog.at_level("INFO", logger="hid_pilot.farm"):
        farmer.last_target = TargetObservation(at=0, name="Graundfu", kind="player",
                                               player_class="Plains Walker", confidence=.98)
        farmer.log_target_observation()
        farmer.log_target_observation()
        assert len(caplog.records) == 1
        assert "Graundfu" in caplog.text and "Plains Walker" in caplog.text
        assert "HP неизвестно" in caplog.text
        farmer.last_target = TargetObservation(at=1, name="Gremlin", kind="monster", hp=.8)
        farmer.log_target_observation()
        farmer.last_target = farmer.last_target.model_copy(update={"hp": .7})
        farmer.log_target_observation()
        assert len(caplog.records) == 2
        farmer.last_target = farmer.last_target.model_copy(update={"hp": None})
        farmer.log_target_observation()
        assert len(caplog.records) == 3
        farmer.invalidate_target()
        farmer.log_target_observation()
        assert "имя неизвестно" in caplog.records[-1].message


@pytest.mark.parametrize("kind,hp,expected", [("player", None, "ESC"),
                                               ("unknown", None, None),
                                               ("monster", .8, "3")])
def test_offensive_decisions_log_target_and_actual_key(caplog, kind, hp, expected):
    farmer, controller, _, _ = harness(attack="3", next_target="6")
    farmer.profile = BarProfile(width=100, height=100)
    farmer.last_target = TargetObservation(at=0, name="Graundfu" if kind == "player" else "Gremlin",
                                           kind=kind, hp=hp,
                                           player_class="Plains Walker" if kind == "player" else None)
    with caplog.at_level("INFO", logger="hid_pilot.farm"):
        sent = farmer.combat_key("3", 100)
    assert sent is (expected == "3")
    assert ("Graundfu" if kind == "player" else "Gremlin") in caplog.text
    if expected is None:
        controller.key.assert_not_called()
        assert "кнопка 3" in caplog.text
    else:
        assert controller.key.call_args.args[0] == expected
    if expected == "3":
        assert "Атакующая команда 3" in caplog.text
