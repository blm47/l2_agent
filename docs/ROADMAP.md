# Roadmap

## Current status — 2026-09-25

- Hard-stop changed at user request from F10 to backslash (`\`, above Enter).
  Historical F10 evidence below predates this change. New live-key acceptance is pending.

- Active milestone: **M1 — Perception**, not yet accepted.
- Expanded annotation taxonomy to 23 classes, preserving original IDs 0–5.
  Editor supports changing a box class without redrawing. Old samples require
  explicit review for taxonomy v2 export; existing baseline weights remain v1.
  See `DETECTOR_CLASSES.md`. 48 affected dataset/export/GUI tests passed.
- First YOLOv8n baseline trained on GTX 1080 Ti for 60 epochs after annotation
  corrections: 23 frames, 466 objects, 17 train / 6 val. mAP50=0.210,
  mAP50-95=0.133; UI AP50=0.799, mob=0.199, player=0.264, rare classes=0.
  This is not acceptance or safe target selection. See `DETECTOR_TRAINING.md`.
- 2026-09-26: added YOLO dataset export with explicit scene groups, reviewed-only
  validation, integrity checks and rejection of identical images across train/val.
  The first real dataset now contains 23 reviewed frames and 464 objects (17 train,
  6 val). File/export checks passed; visual review found missing player annotations
  and class imbalance. See `DATASET_AUDIT.md`; the baseline above uses corrected labels.
- 2026-09-26: added a local frame annotation dialog with normalized object boxes,
  class/target selection, deletion and explicit complete-frame review. Image integrity
  is checked on load/save; cancellation preserves existing labels. Training and
  inference remain outstanding.
- Target-name OCR remains unstable against changing backgrounds according to
  the user. Further tuning is deferred at their request; see `TODO.md`.
- Added manual delayed collection of original frames with WorldState and ROI
  metadata for object-detector labeling. See `DETECTOR_DATASET.md`. This is dataset
  groundwork; detector inference and trained game weights are not implemented.
- The user confirms working control through Pico. Individual mouse-button and
  hard-stop acceptance details have not been separately recorded.
- M0 remains accepted for the previously tested Parsec setup. Local LU4 input
  is a separate unresolved compatibility check: Windows accepted SendInput, but
  skill activation was not observed (see `LU4_INPUT_CHECK.md`).
- Pico is connected with CircuitPython 10.3.1 and L2_PICO_V1 firmware. Optional
  `--pico-port COM4` routes keys/buttons through Pico behind ActionController;
  cursor positioning retains validated SendInput. Board deadlines, heartbeat
  timeout, release/disarm and host protocol checks are implemented. Hardware
  handshake/ARM/STOP/heartbeat timeout and GUI smoke passed. Native R (300 ms)
  triggered a visible cast and a new `You use Wind Strike` log entry in LU4 on
  2026-09-25. Mouse-button response and backslash hard-stop during HID holds remain
  manual acceptance; see `PICO_SETUP.md`.
- Bonsai 2 is served locally at `http://127.0.0.1:8080` using the user's
  `.\scripts\start_llama_server.ps1 -np 1` command in the Bonsai deployment.
  A read-only `/v1/models` check succeeded on 2026-09-25 and reported owner
  `llamacpp`, model `Ternary-Bonsai-2-27B-PTQ1_0.gguf` and context 65536.
  Generation quality, latency and total runtime memory are not benchmarked.
  Deployment is not M2 acceptance; no LLMProvider or advisory integration exists yet.
- M1 now estimates HP/MP/CP/target-HP fill fractions from confirmed ROIs and
  publishes WorldState in GUI with percentages, overlays and confidence/reasons
  in the tooltip. Frames older than one second, missing/size-mismatched ROIs and
  ambiguous/missing colors produce unknown values. Blank crops do not imply zero.
- The user confirms that the live bar/WorldState iteration works in the game.
- Target-name OCR is implemented for the standard LU4 target header above the
  confirmed target-HP ROI. One shared lazy CPU OCRProvider serves region proposals
  and target names. The name reader runs at most once per second in a background
  thread with one crop/job/result, requiring a fresh frame and visible panel close
  button. Self-targets without HP are supported. Changed header text mask/window/ROI
  or a result older than 1.5 s invalidates the name; dark background pixels are
  filtered, but changing backgrounds still cause unstable recognition (deferred).
- Target-name validation: 45 affected OCR/ROI-proposal/WorldState/GUI tests passed.
  Installed PP-OCRv4 read `Gremlin` from a real saved header with score 0.951;
  first-call time 1.17 s including model load. Live name acceptance is pending.
- Real LU4 full bars have different widths due to the slanted panel. OCR N/N
  evidence allows proposals to retain each bar's own width; user confirmation
  remains required. A saved HP ROI was found on the target panel and retired;
  backup: `artifacts/roi-before-m1-parser.json`. Recalibration is required.
- Validation: 265 tests pass, including four labeled full-bar crops from one real
  game frame (error <2 percentage points). Partial fill and rejection paths are
  tested synthetically. A 100-iteration parser check on the three player ROIs
  measured median 0.44 ms, maximum 3.17 ms; this is not a live stability benchmark.
- Next: collect and label object-detector frames and real partial/empty/occluded bars.
  Target-name stability is deferred in `TODO.md`. OCR beyond the header and object detection
  remain outstanding. M1 is not accepted.

The dated progress below is historical evidence, not a claim that later local
LU4 input or M1 acceptance has passed.

## Progress — 2026-09-24

M0 iteration 1 implemented: package skeleton, diagnostic PySide6 GUI, Parsec
discovery by executable name, current client-area geometry, normalized coordinate
mapping, centralized console/file logging, unit tests and offscreen GUI smoke test.
Live discovery verified on a running Parsec window (1727 × 960 client area).
User confirmed resize and minimize detection; focus check remains for user validation.
Iteration 2: BetterCam worker with a latest-frame slot, live preview, FPS, frame age,
monitor-relative ROI mapping and clearing of unavailable frames.
Live offscreen GUI/capture smoke: 30 seconds, 843 frames observed, sampled FPS 28–31,
sampled RSS 142.8–156.4 MB after initialization. This is not a 10-minute stability pass.
User confirmed iteration 2 preview, resize and restore behavior.
Iteration 3 implements ActionValidator, SendInput primitives, bounded keyboard/mouse
holds, release on stop/error, a 10 ms watchdog, global F10 on a separate thread,
and START/PAUSE/STOP with delayed manual input tests. Unit tests use a fake sender;
no native input was sent to the game during automated validation.
Native F10 registration and GUI shutdown verified; a BetterCam 1.0.0 COM double-release
on shutdown was fixed in the local adapter and checked by another native smoke run.
User confirmed mouse input, but virtual-key keyboard events did not work in Parsec.
Keyboard down/up now use scan codes following the user's successful W example in
tests/TEEEEST.py; the GUI single-key test is R. The user subsequently reported all
requested tests working (R, W and F10 during the hold); mouse input was already confirmed.
The final M0 check was the 10-minute preview stability run.
The smoke harness now records FPS, RSS, private committed memory, maximum frame gap
and post-warmup memory growth to JSON. A static picture does not establish continuous
frame delivery and must not be reported as a successful stability test.
The first long-run attempt was stopped after about 165 seconds: only 14 frames
were observed, with long zero-FPS intervals. RSS sampled 128.7–139.0 MB after startup;
this does not establish continuous capture or a ten-minute memory stability pass.
After the user confirmed a moving scene, a new run completed 600 seconds:
14,225 frames, maximum gap 1.219 seconds, peak sampled RSS 151.14 MB, private
memory growth after warmup 3.54 MB. Automated thresholds passed. The user also
explicitly accepted M0 based on the stable first six minutes and authorized M1.
M0 is accepted; see docs/M0_ACCEPTANCE.md for evidence and limits.

Current milestone: M1. ROI profiles, size mismatch rejection and debug overlays are
implemented. Following user feedback, default setup now proposes bar regions using
OpenCV geometry/colors and local CPU PP-OCRv4 labels; the user confirms or rejects
proposals without drawing boxes. Target HP requires an explicit candidate choice.
Manual calibration remains optional. First proposals always need confirmation,
especially because filled pixels do not establish the full empty bar extent.
151 tests pass; actual OCR was also checked on a synthetic labeled panel, and a
desktop capture produced no proposal. Game-screen validation is pending because
the user cannot currently display the game. Numeric bar parsing, WorldState,
general OCR and object detection remain outstanding. M1 is not complete.

## M0 — Eyes & Hands

### Goal
Reliably observe and control the Parsec client window without any ML dependency.

### Deliverables
- repository skeleton
- PySide6 GUI shell
- Parsec window discovery
- client-area coordinate tracking
- BetterCam capture
- live preview
- FPS indicator
- SendInput keyboard primitives
- SendInput mouse primitives
- normalized coordinate conversion
- START / PAUSE / STOP
- global hard-stop hotkey
- release_all on stop/error
- unit tests for coordinate mapping and validation
- smoke tests for keyboard/mouse control

### Acceptance criteria
- resizing Parsec updates coordinate mapping correctly
- clicks requested outside the viewport are rejected
- key-down states are released on STOP
- hard-stop works while Parsec is focused
- preview updates continuously for at least 10 minutes without unbounded memory growth
- M0 contains no LLM dependency

---

## M1 — Perception

### Goal
Produce a structured WorldState from the game screen.

### Deliverables
- configurable ROI system
- HP/MP/CP parser
- target HP parser
- OCR abstraction and first implementation
- first detector dataset format
- initial small object detector integration
- WorldState Pydantic models
- confidence model
- debug overlay

### Acceptance criteria
- HP/MP parsers are validated against a labeled screenshot set
- WorldState updates continuously
- every field exposes unknown/low-confidence rather than invented values
- UI overlay shows what perception believes

---

## M2 — Brain advisory mode

### Goal
Let an LLM reason over WorldState without controlling the game.

### Deliverables
- LLMProvider interface
- Bonsai 2 benchmark harness
- fallback provider implementation
- structured skill proposal schema
- prompt templates
- advisory panel in GUI
- model telemetry: latency, tokens/s, memory usage where available

### Acceptance criteria
- model can consume WorldState and return schema-valid skill proposals
- invalid tool/skill outputs are rejected
- no proposed action is executed
- GTX 1080 Ti benchmark results are documented

---

## M3 — Closed loop

### Goal
Observe -> decide -> execute -> verify.

### Deliverables
- SkillExecutor
- initial skill library
- ActionValidator
- SkillResult
- retry/failure policies
- emergency state
- closed-loop execution mode

### Acceptance criteria
- agent can complete a deterministic training scenario repeatedly
- failures produce explicit SkillResult
- no direct LLM-to-SendInput path exists
- hard stop interrupts active skill execution

---

## M4 — Knowledge / RAG

### Goal
Ground high-level reasoning in current LU4 knowledge.

### Deliverables
- ingestion pipeline
- source metadata
- SQLite document store
- chunking
- embeddings
- FAISS retrieval if needed
- RAG context builder

### Acceptance criteria
- retrieved answers cite stored sources internally
- stale/retrieval timestamps are visible
- the agent does not treat unsupported information as known game fact

---

## M5 — Episodic memory

### Goal
Continue useful behavior across sessions.

### Deliverables
- session model
- episode storage
- user corrections
- route/skill outcome history
- memory retrieval
- session-start context builder

### Acceptance criteria
- agent can recover the previous session's goal and meaningful outcome
- user corrections influence future suggestions
- memory is inspectable from the GUI or debug tooling

---

## M6 — Learning

### Goal
Use collected trajectories to reduce dependence on the LLM.

### Deliverables
- trajectory dataset specification
- recorder
- labeling/reward tooling
- baseline imitation policy
- offline evaluation
- routing policy between small policy model and LLM

### Acceptance criteria
- dataset is reproducible and versioned
- learned policy is evaluated against a held-out trajectory set
- learned policy can be disabled independently
- no online self-modification occurs without explicit configuration
