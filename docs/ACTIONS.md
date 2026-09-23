# Actions and Skills

## Separation of concerns

LLM:
- chooses a Skill

SkillExecutor:
- expands Skill into deterministic bounded actions

ActionValidator:
- checks whether each low-level action is allowed

ActionController:
- performs Win32 SendInput

## Low-level primitives
Planned ActionController API:
- key_down(key)
- key_up(key)
- key_press(key, duration_ms)
- mouse_move_relative(dx, dy)
- mouse_move_client(x_norm, y_norm)
- mouse_down(button)
- mouse_up(button)
- click(button)
- release_all()

Only this module may depend directly on SendInput.

## Blocked behavior
Default-deny for:
- Windows key
- Alt+F4
- Win+R
- Ctrl+Alt+Del style sequences
- clicks outside Parsec
- indefinite key holds
- unknown key identifiers

## Initial skills
- MoveToScreenPoint
- MoveCamera
- AttackTarget
- UseSkill
- Heal
- Rest
- InteractWithNpc
- PickupLoot
- OpenInventory
- OpenMap
- Escape
- AskUser

## Skill result
Every skill returns a result similar to:

```python
class SkillResult(BaseModel):
    skill: str
    success: bool
    status: str
    started_at: float
    finished_at: float
    observations: dict[str, Any] = {}
    error: str | None = None
```

## Timeouts
Every skill must have a bounded maximum duration.
No unbounded waits.

## Verification
Skills should verify game-visible outcomes where practical.

Examples:
- AttackTarget verifies a target became selected or HP changed
- OpenInventory verifies inventory UI became visible
- InteractWithNpc verifies a dialog appeared

The executor should prefer state transitions over blind sleeps.
