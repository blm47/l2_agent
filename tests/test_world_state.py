import pytest
from pydantic import ValidationError

from l2_agent.world_state import BarObservation, PlayerState, WorldState


def test_unobserved_bars_remain_unknown_after_json_roundtrip():
    state = WorldState(frame_id=7, timestamp=12.5)
    restored = WorldState.model_validate_json(state.model_dump_json())
    assert restored == state
    for bar in (restored.player.hp, restored.player.mp, restored.player.cp, restored.target_hp):
        assert bar.value is None
        assert bar.confidence == 0


def test_zero_health_is_distinct_from_unknown():
    state = WorldState(
        frame_id=8,
        timestamp=13,
        player=PlayerState(hp=BarObservation(value=0, confidence=0.8)),
    )
    assert state.player.hp.value == 0
    assert state.player.mp.value is None
    assert WorldState.model_validate_json(state.model_dump_json()) == state


@pytest.mark.parametrize(
    "payload",
    [
        {"value": -0.1, "confidence": 0.8},
        {"value": 1.1, "confidence": 0.8},
        {"value": float("nan"), "confidence": 0.8},
        {"value": float("inf"), "confidence": 0.8},
        {"value": 0.5, "confidence": 1.1},
        {"value": 0.5, "confidence": float("nan")},
        {"value": None, "confidence": 0.8},
        {"value": 0.5, "confidence": 0},
        {"value": 0.5, "confidence": 0.8, "guessed": True},
    ],
)
def test_invalid_bar_evidence_is_rejected(payload):
    with pytest.raises(ValidationError):
        BarObservation.model_validate(payload)


@pytest.mark.parametrize(
    "updates",
    [
        {"frame_id": -1},
        {"frame_id": True},
        {"timestamp": -1},
        {"timestamp": float("inf")},
        {"version": 2},
        {"raw_key": "W"},
    ],
)
def test_invalid_frame_metadata_and_extra_fields_are_rejected(updates):
    with pytest.raises(ValidationError):
        WorldState.model_validate({"frame_id": 0, "timestamp": 0, **updates})
