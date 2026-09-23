# Architecture Decision Log

Use this file for concise architecture decisions.

## ADR-010 — Bounded manual input before autonomous control
Status: accepted

Keyboard compatibility follow-up: the user confirmed mouse input but reported no
keyboard response. Their tests/TEEEEST.py successfully sends W using scan code 0x11.
ActionController now sends scan codes for both down and up, preserving release on
STOP/watchdog. The single-key GUI test is R (0x13). Names denote physical US QWERTY
positions, independent of text layout. Real delivery of the fix remains to be checked.
API reference: https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput

Decision:
Keep all SendInput calls in ActionController, with Pydantic ActionRequest validation,
an explicit allowlist and live Parsec identity/focus/geometry checks. Only one held
input is allowed in M0. A separate watchdog releases holds at their deadlines and
on lost focus; release operations bypass ordinary action checks. Failed releases
remain tracked and prevent re-arming. Explicit START is required after stop/error.

Register global F10 with MOD_NOREPEAT on a dedicated message-pump thread independent
of Qt and capture. A failed registration blocks input. GUI manual tests wait three
seconds for the user to focus Parsec. Generation counters invalidate old requests.
Input is still only manual; no milestone M3 execution loop is introduced.

Limitations: Windows input is not real-time and SendInput does not atomically target
an HWND. Rechecks and the watchdog reduce but cannot eliminate focus/cursor races.
No bypass of UIPI; real input and focused-hotkey checks remain manual acceptance.

References checked 2026-09-23:
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput

Capture follow-up: the BetterCam 1.0.0 adapter clears owned comtypes pointers before
calling release during shutdown, avoiding an observed double Release/access violation.
This workaround also depends on the pinned backend's private fields.

## ADR-009 — M0 capture worker and bounded preview storage
Status: accepted

Decision:
Use one worker thread owning BetterCam and COM resources, polling `grab(region=...)`
at a target of 30 Hz. Publish only the latest RGB frame with a monotonic timestamp
and the client rectangle. GUI polls this slot; no queued frame signals or image queues.
Drop frames if window geometry changes during capture. Window selection changes
invalidate pending publication. Stop/disable clears the preview.

The initial capture supports a client rectangle wholly contained within one DXGI
output, including monitors with negative screen coordinates. Reject partial/offscreen
rectangles. Desktop occlusion remains visible in the captured pixels.

BetterCam is pinned to 1.0.0 because output enumeration uses its private factory
metadata; this dependency is isolated in BetterCamBackend. Public capture API:
https://github.com/RootKit-Org/BetterCam (checked 2026-09-23).

Known limitation: BetterCam can remain inside native/display recovery calls after
display loss. Closing the GUI waits at most two seconds for the daemon worker;
it does not forcibly terminate a thread owning DXGI resources. Display topology
changes may require restarting the application. This is not autonomous control.

---

## ADR-001 — Target one game first
Status: accepted

Decision:
Build specifically for Lineage 2 / LU4.

Reason:
A generic game-agent framework would add abstraction before a second game exists.

---

## ADR-002 — Remote game through Parsec
Status: accepted

Decision:
The game runs remotely. The local application captures and controls the Parsec window.

Implication:
All geometry is relative to the Parsec client area.

---

## ADR-003 — Win32 SendInput for M0
Status: accepted

Decision:
Use Windows SendInput for keyboard/mouse control.

Reason:
Smoke test succeeded and no external HID hardware is currently desired.

---

## ADR-004 — LLM does not control raw input
Status: accepted

Decision:
LLM -> Skill -> ActionValidator -> ActionController.

Reason:
Improves safety, determinism, testability, and latency.

---

## ADR-005 — Specialized perception before VLM
Status: accepted

Decision:
Use CV/OCR/detectors for routine perception and reserve multimodal LLM for ambiguity.

Reason:
Target GPU/RAM are constrained and game UI contains many deterministic elements.

---

## ADR-006 — Bonsai 2 is experimental primary candidate
Status: provisional

Decision:
Benchmark Bonsai 2 27B ternary as the first main planner/VLM candidate.

Fallbacks:
Qwen3.5-4B, Qwen3-4B quantized.

Final model choice requires target-hardware benchmark.

---

## ADR-007 — SQLite-first storage
Status: accepted

Decision:
Use SQLite locally and FAISS only when vector retrieval is needed.

Reason:
Single-machine system, 16 GB RAM budget, no need for distributed infrastructure.

---

## ADR-008 — No RL at project start
Status: accepted

Decision:
Collect trajectories before selecting a learning method.

Reason:
Reliable perception/action data is a prerequisite for meaningful policy learning.
