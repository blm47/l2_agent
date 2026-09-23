# WorldState Contract

WorldState is the canonical perception output and primary agent input.

## Design rules
- structured, not prose
- unknown values are explicit
- every inferred value may carry confidence
- screen coordinates are normalized where practical
- perception time and frame id are retained
- no LLM-only hidden state belongs here

## Initial schema sketch

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
