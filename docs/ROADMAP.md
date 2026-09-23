# Roadmap

## Progress — 2026-09-23

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
The remaining M0 check is the 10-minute preview stability run.
The smoke harness now records FPS, RSS, private committed memory, maximum frame gap
and post-warmup memory growth to JSON. A static picture does not establish continuous
frame delivery and must not be reported as a successful stability test.
The first long-run attempt was stopped after about 165 seconds: only 14 frames
were observed, with long zero-FPS intervals. RSS sampled 128.7–139.0 MB after startup;
this does not establish continuous capture or a ten-minute memory stability pass.
The current Parsec scene/connection needs confirmation before restarting the run.
M0 is not complete; later milestones have not started.

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
