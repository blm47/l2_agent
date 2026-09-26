# M0 acceptance

Status: accepted. The user explicitly accepted the stable six-minute observation
and authorized M1. The background run also completed all 600 seconds before its
session was collected. Logs date the run 2026-09-23 23:14:30–23:24:30 local time.

## Evidence

| Criterion | Verification |
| --- | --- |
| Resize mapping | Geometry unit tests and user-confirmed resize/minimize behavior |
| Outside clicks rejected | Controller tests cover outside/occluded points and geometry changes |
| STOP releases held keys | Tests cover STOP/PAUSE/F10, failed releases and retry; user confirmed requested manual tests |
| F10 with Parsec focused | Native registration checked; user reported requested R/W/F10 tests working |
| Ten-minute preview | 600.0 seconds, 14,225 frames, maximum observed frame gap 1.219 seconds |
| Memory | Peak sampled RSS 151.14 MB; peak private memory 388.07 MB; mean private growth after warmup 3.54 MB |
| No LLM dependency | M0 uses local window/capture/input/GUI components; no model runtime |

Machine-readable report: `artifacts/m0-preview-600s.json` (local, ignored by Git).
Command: `.venv\Scripts\python.exe scripts\smoke_preview.py --seconds 600 --report artifacts\m0-preview-600s.json`.

The harness processes the Qt preview offscreen against the real Parsec window and
samples memory every five seconds. It sends no game input and stores no images.
Thresholds: frame gap ≤3 seconds; mean private growth ≤32 MB between 60–120 seconds
and the final minute. FPS declined in the later portion of the run; this verifies
continuous delivery within that threshold, not a guaranteed 30 FPS minimum.
This finite run shows no unbounded memory growth during the observation period;
it does not prove the absence of all possible leaks.

The earlier run with only 14 frames in about 165 seconds was not accepted as
continuous capture. Its low update rate was not reproduced after the user confirmed
the active moving game scene; the original cause was not established.
