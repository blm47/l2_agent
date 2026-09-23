# Roadmap

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
