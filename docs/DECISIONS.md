# Architecture Decision Log

Use this file for concise architecture decisions.

## ADR-022 — Versioned expanded detector classes
Status: implemented, 2026-09-26

Append requested object/UI classes while preserving existing IDs 0–5. Keep the
original six labels readable and introduce taxonomy_version independently of
sample format version. Missing taxonomy_version means v1. Saving an explicitly
reviewed frame marks v2; v2 export rejects v1 frames to prevent treating missing
new labels as confirmed negatives. No automatic semantic migration of ui_window
or player: the editor supports reclassification while retaining box geometry.
Existing exports and trained weights are not modified. See `DETECTOR_CLASSES.md`.

## ADR-021 — Isolated first detector training
Status: experimental, 2026-09-26

Use YOLOv8n with a separate `.venv-train`, pinned PyTorch 2.7.1 / torchvision
0.22.1 CUDA 11.8 and Ultralytics 8.3.200. Verify CUDA execution on the GTX 1080 Ti
before training. Preserve the existing application environment and copy the
exported dataset into each run for reproducibility. FP32, batch 2 and workers 0
bound the initial resource demand. This does not enable inference or game control.
See `DETECTOR_TRAINING.md`; quality must be measured on new scenes before acceptance.

## ADR-020 — Shared OCR provider and asynchronous target names
Status: implemented for M1; live name validation pending

The user confirms live bar perception works. Continue M1 with target-header OCR,
using a concrete second OCR consumer to introduce OCRProvider. RapidOCR/PP-OCRv4
is shared lazily under a lock with bar proposals, using two CPU intra-op threads.
Only one small crop/job/result is held by TargetNameReader; submit at most 1 Hz.
Failures back off ten seconds and report unknown rather than block the GUI.

Derive the standard LU4 header from the confirmed target-HP ROI: 5 to 3 bar heights
above its top, excluding avatar and service icons horizontally. Require a fresh
frame and the target panel's close-button template (correlation >=0.85), not visible
HP: a self/player target can have a name without a red HP bar. Require exactly one
acceptable text line with score >=0.85 and retain source frame/time separately.
Window/geometry/ROI or bright neutral text-mask changes invalidate the cached name;
source age >1.5 s also invalidates it. Loss of the panel clears the name.
Exact RGB hashing was removed after live captures showed continuously changing
transparent backgrounds with identical OCR text. The name mask excludes that background.
Different layouts may need configurable text ROIs later. Names do not imply entity
type or unique identity. No actions are driven by these observations.

Validation: 45 targeted tests passed. Real saved Gremlin header recognized with
score 0.951 (first call including model load: 1.17 s). General OCR/object detection
and a broader real screenshot set are still required for M1 acceptance.

Follow-up validation: 19 targeted reader/GUI tests pass, including self-target
without HP, animated background, panel disappearance and stale results. Eight
captured frames replayed across 24 asynchronous observations yield LenaBerkova
consistently after the first result (observations 5–23). Small panel regression
fixtures and a close-button template come from local LU4 captures.

## ADR-019 — Deterministic bar fractions and live WorldState
Status: implemented for M1, broader real-frame validation pending

Parse only confirmed full-width ROIs with HSV colors and horizontal/row support.
Permit small text gaps, reject displaced starts and disconnected color clusters.
Publish per-frame fractions with heuristic confidence and explicit unknowns.
Do not classify a blank crop as zero: it can also be a missing/covered UI element.
No OCR or model runs in this per-frame path; OCR remains in the background ROI
proposal stage. When high-confidence N/N text is aligned with a candidate, preserve
its individual full width: LU4's slanted panel gives CP/HP/MP different widths.
This evidence still requires confirmation before use as a profile.

GUI updates WorldState and overlays from latest capture; stale (>1 s), future,
dimension-mismatched or unconfigured inputs produce unknowns. Missing capture or
window changes clear the snapshot. No action path consumes these estimates yet.
Four real full-bar crops are checked in with labels; synthetic partial/occlusion
tests supplement but do not replace the real labeled acceptance dataset.

## ADR-018 — Backslash hard stop
Status: implemented at user request, 2026-09-25

Replace F10 with the backslash key above Enter, without modifiers:
VK_OEM_5 (0xDC), MOD_NOREPEAT. GUI labels and logs share the key constant.
The key remains excluded from both host and Pico action allowlists.
Previous F10 acceptance entries describe historical checks; live interruption
with the new key still needs manual validation. Registration conflicts continue
to disable START; changing the key does not permit multiple input controllers.
Reference: https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes

## ADR-017 — Optional Pico backend for keys and mouse buttons
Status: implemented at user request; real-game acceptance pending

Use CircuitPython's standard USB HID keyboard/mouse reports with a separate CDC
data channel and small optional pyserial dependency. ActionController retains all
window/focus/integrity/geometry checks and F10; only its hold/release transport
changes. Absolute cursor positioning retains existing validated SendInput because
the stock Pico mouse uses relative motion, which does not establish exact screen
coordinates under Windows acceleration. GUI explicitly labels this mixed mode.

Firmware rejects modifiers/unknown keys, overlapping holds, excessive duration,
replayed sequence and oversized lines. Hold deadlines are independent of heartbeat.
Loss of DTR or 350 ms without heartbeat releases/disarms; hardware watchdog resets
after two seconds without a running loop. Host expects matching ACK within bounded
serial timeouts, closes on errors and never retries presses or silently falls back.
Explicit app restart reconnects after a transport fault. START re-arms after STOP.

Hardware verified: handshake, ARM/STOP, heartbeat disarm, reconnect and GUI/F10
registration. No game input was sent during this automated validation. Native HID
delivery and F10 interruption in LU4 remain a manual acceptance step.

## ADR-016 — Pico USB bring-up
Status: diagnostic setup verified on hardware, input integration pending

At the user's request, install CircuitPython 10.3.1 for the photographed standard
Raspberry Pi Pico (RP2040). Keep console CDC separate from data CDC; expose standard
keyboard/mouse HID and disable MIDI to conserve endpoints. Store board scripts in
`firmware/pico/`. Initial firmware only answers bounded diagnostic commands and
does not send input. Hardware PING/STATUS passed; see `PICO_SETUP.md`.
Future HID commands must remain downstream of ActionController and preserve host
safety checks plus board-side deadlines/disconnect release. This does not change
the active M1 milestone or establish LU4 input compatibility.

## ADR-015 — Continue perception while hardware input is pending
Status: accepted for M1; hardware integration pending

On 2026-09-25 the user reports a Pico controller in transit and Bonsai 2 deployed
locally. Record these as setup progress, without treating them as tested input
compatibility or model integration. SendInput remains the only implemented input
backend. Pico model, connection topology and firmware remain unspecified.
Any future backend must preserve ActionValidator/ActionController, bounded holds,
focus/geometry checks, hard stop and release on disconnect. No client tampering
or protection bypass is part of this decision.

Continue the active M1 milestone independently of hardware arrival. Start the
WorldState implementation with per-bar observations, explicit unknown values,
confidence and source-frame metadata. Do not invent entity/UI/location values
before corresponding perception exists. Bonsai runtime integration and benchmark
remain M2 work; deploying a model alone does not satisfy those criteria.

Local setup verified the same day: `http://127.0.0.1:8080/v1/models` responds
with owner `llamacpp` and `Ternary-Bonsai-2-27B-PTQ1_0.gguf` (context 65536).
The user's launch command is `.\scripts\start_llama_server.ps1 -np 1` in the
Bonsai deployment, not this repository. This read-only check did not test generation
or validate the advertised multimodal capability.

## ADR-014 — Select among local Lineage 2 instances
Status: accepted at user request

Follow-up superseding the executable allowlist below: the user explicitly requests
all windows. Enumerate all visible top-level windows without title/process filters.
Executable lookup is optional display metadata; access denial must not hide an
otherwise accessible window. Selection remains tied to HWND/PID across title changes.
ActionController retains its focus, geometry, integrity and hard-stop checks.

Extend the shared executable allowlist with l2.exe, l2.bin, l2.bin.exe,
lineage2.exe and lineageii.exe. Enumerate every visible supported window, including
multiple instances with identical titles. Display executable, PID and HWND in the
selector. All consumers still revalidate the selected HWND/PID; title matches alone
do not authorize a process. Nonstandard renamed clients require an explicit addition.
This expands window selection, not compatibility of game actions with SendInput.

## ADR-013 — Support a local LU4 window alongside Parsec
Status: accepted at user request

Input follow-up: local LU4 is now the primary development target. A read-only
token check measured target integrity 12288 (High), agent 8192 (Medium).
START checks integrity and rejects higher-privilege targets with instructions to
run at matching privileges. Successful SendInput is logged as Windows acceptance,
not proof of game response. Native input must be rechecked at matching privileges.
Automatic bar clarification is deferred while manual input is armed, preventing
background perception from cancelling user input tests.
Reference: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput

Timing follow-up: the user reports long holds working but short presses failing,
even with matching integrity levels. Correct a pre-send deadline calculation:
start the requested hold after SendInput succeeds, retaining pending-release
tracking before the call for failure cleanup. Log successful releases and elapsed
hold time. Add same-key/button duration comparisons without changing SendInput or
introducing client tampering. A regression test covers slow pre-send validation
for both keyboard and mouse; real-game causality remains to be checked.

Rename the shared manager to GameWindowManager and extend its executable-name
allowlist with lu4.exe, lu4.bin and lu4.bin.exe. The desktop process listing showed
LU4 with process name lu4.bin; full-path verification was unavailable because the
approval service reached its usage limit. Do not authorize arbitrary windows by
title. Capture and ActionController continue checking the chosen HWND/PID through
the same manager. No input backend or milestone change is needed. Native LU4
capture/input acceptance remains pending; process-filter behavior is unit tested.

## ADR-012 — Propose bar regions automatically and confirm uncertain geometry
Status: accepted for the current M1 iteration; game-screen validation pending

The user requests setup without drawing ROI boxes. Use OpenCV color/shape grouping
to propose player HP/MP/CP, then bundled PP-OCRv4 models via rapidocr-onnxruntime
1.4.4 to check nearby labels. This changes the default setup workflow in ADR-011;
its local profile format and optional manual editor remain applicable.

Inference runs locally on CPU with two intra-op threads, one job and one result
slot. Load models lazily only after CV finds a plausible panel; OCR only enlarged
candidate crops. No GPU runtime or external screenshot service is needed. Search
at most every 15 seconds while no matching saved profile exists. Window/geometry
generations discard stale results; opening a clarification pauses manual input.

Scores express heuristic evidence agreement, not calibrated probability. Always
confirm the first proposal: colored fill alone cannot prove the full empty extent.
Additional red bars are optional target candidates, never silently assigned as
target HP. Rejected alternatives do not train the model. Confirmed coordinates
are saved atomically and reused at the same frame size. UI movement without a
size change requires the repeat-search button.

Validation: 151 automated tests passed. Real bundled OCR recognized all three
labels on a synthetic panel (about 1.00 s cold / 0.11 s warm); a captured desktop
returned no proposal (0.03 s). These are fixture timings, not gameplay accuracy
or performance acceptance. The user cannot currently expose the game, so live
game verification and numeric bar parsing remain outstanding.

Upstream implementation: [RapidOCR](https://github.com/RapidAI/RapidOCR).
The installed 1.4.4 API and bundled model files were inspected locally.

## ADR-011 — Calibrate UI bar regions before interpreting pixels
Status: accepted

M1 starts with manual HP/MP/CP/target-HP rectangles on a frozen Parsec frame.
Store a Pydantic-validated local JSON profile with normalized bounds and the frame
size used for calibration. The profile contains coordinates, not screenshots.
Use atomic replacement for saves; cancellation leaves the prior profile intact.

The editor accounts for preview scaling and letterboxing. Main preview only draws
overlays; selecting a region never sends game input. Opening calibration pauses
manual control and cancels pending tests.

A client-size mismatch disables overlays until recalibration rather than assuming
that the game UI scales uniformly. In-game UI movement still requires manual
recalibration. Numeric parsing and confidence estimates await the next M1 iteration.

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
