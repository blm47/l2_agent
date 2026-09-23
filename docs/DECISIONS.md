# Architecture Decision Log

Use this file for concise architecture decisions.

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
