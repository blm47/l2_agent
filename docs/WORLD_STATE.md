# WorldState Contract

WorldState is the canonical perception output and primary agent input.

## Design rules
- structured, not prose
- unknown values are explicit
- every inferred value may carry confidence
- screen coordinates are normalized where practical
- perception time and frame id are retained
- no LLM-only hidden state belongs here

## Implemented M1 subset — 2026-09-25

`src/l2_agent/world_state.py` defines `WorldState`, `PlayerState` and
`BarObservation`. Each player HP/MP/CP and target HP observation contains
`value` (fill fraction 0..1 or null), `confidence` (0..1) and optional `reason`.
Unknown values require zero confidence; known values require positive confidence.
Confidence is evidence quality, not a calibrated probability. Zero fill is distinct
from unknown; a blank crop alone must not be interpreted as zero health.

`frame_id` is a nonnegative integer and `timestamp` is the source frame's monotonic
time in seconds, not wall-clock time. Consumers must check freshness before use.
Models reject extra fields and non-finite numeric observations. JSON roundtrip is
supported. `bar_parser.perceive_bars` now produces snapshots from confirmed ROIs.
GUI publishes them in `MainWindow.world_state`, shows approximate percentages and
overlay labels, and exposes confidence/reasons in the WorldState tooltip.
Frames older than one second (or future timestamps), missing/mismatched profiles,
unconfigured bars and ambiguous pixels yield unknown observations. A missing
frame or changed/unavailable selected window clears the GUI snapshot to None.
Unconfirmed proposals do not produce measurements. These values do not control input.

The parser estimates colored horizontal fill, not absolute HP/MP/CP points.
It tolerates small text gaps but rejects large gaps, displaced starts and weak
row evidence. Empty bars remain unknown until another signal establishes zero.
Confidence is capped at 0.85 and is heuristic. It does not detect all UI occlusions
or movement inside an unchanged window size; ROI calibration remains necessary.

`target_name: NameObservation` now contains an optional OCR name, confidence,
source `frame_id`, source monotonic `timestamp` and reason. Known names require
positive confidence and source metadata. Default remains unknown. This may refer
to an older frame than the bar measurements: the GUI accepts it only while the
header text mask/context match and it is no more than 1.5 seconds old. Transparent
background changes do not invalidate the text mask. The panel is checked by its
close-button template; unknown target HP alone does not suppress the name. Names require
OCR score >=0.85 and one unambiguous text candidate. A name is not a unique entity ID.

Entity identity/type, combat state, location and UI recognition are not implemented.
The sketch below describes future scope, not the current serialized API.

## Future schema sketch

```python
class NormalizedPoint(BaseModel):
    x: float
    y: float

class NormalizedBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float

class PlayerState(BaseModel):
    hp: float | None = None
    mp: float | None = None
    cp: float | None = None
    level: int | None = None
    in_combat: bool | None = None

class Entity(BaseModel):
    id: str
    kind: str
    name: str | None = None
    bbox: NormalizedBox | None = None
    hp: float | None = None
    selected: bool = False
    confidence: float

class UIState(BaseModel):
    inventory_open: bool | None = None
    map_open: bool | None = None
    dialog_open: bool | None = None
    chat_focused: bool | None = None

class LocationState(BaseModel):
    name: str | None = None
    confidence: float = 0.0

class WorldState(BaseModel):
    frame_id: int
    timestamp: float
    player: PlayerState
    target: Entity | None = None
    enemies: list[Entity] = []
    npcs: list[Entity] = []
    party: list[Entity] = []
    ui: UIState
    location: LocationState
    confidence: float
```

This is a starting point, not a frozen API.

## Coordinate convention
Normalized coordinates:
- top-left: (0.0, 0.0)
- bottom-right: (1.0, 1.0)

All conversions to actual screen coordinates happen downstream using the current Parsec client rectangle.

## Confidence
Avoid fake precision.
Use confidence only when it has an interpretable meaning.

Examples:
- detector confidence
- OCR confidence
- heuristic parser confidence

Unknown must remain unknown instead of being filled by guesses.
