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
Current M0 ActionController API:
- arm((hwnd, pid))
- stop(reason)
- key_down(key, duration_ms=100)
- key_up(key)
- key_press(key, duration_ms)
- mouse_move_relative(dx, dy)
- mouse_move_absolute_client(x_norm, y_norm)
- mouse_button_down(button, duration_ms=100)
- mouse_button_up(button)
- click(button)
- release_all()
- release_all_keys()
- release_all_mouse_buttons()

Only this module may depend directly on SendInput.

Keyboard down/up use KEYEVENTF_SCANCODE, wVk=0 and fixed physical US QWERTY positions
(W=0x11, R=0x13). Key-up retains the same scan code and adds KEYEVENTF_KEYUP.
The GUI single-key smoke test sends R. Virtual-key identifiers remain for key-state
checks; they are not emitted as keyboard events.

Holds are limited to 1–1000 ms and released by an independent watchdog, including
when a caller omits key_up/mouse_button_up. M0 allows one held input at a time,
20 new actions/second, no modifiers. Release is exempt from focus/rate checks.
The controller remembers unconfirmed releases and retries while blocking new input.
Physical modifier state, current process identity, foreground window, geometry and
mouse hit-testing are checked before dispatch. Mouse actions use the virtual desktop.

F10 is registered globally in its own thread. No successful registration means no
arming. GUI START only arms manual M0 tests; PAUSE, STOP and F10 cancel pending tests
and release holds. Each new arming/stop invalidates prior delayed requests.
No SkillExecutor or autonomous loop is introduced in M0.

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
