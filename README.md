# L2 Agent

Experimental Windows-native AI agent for playing Lineage 2 on the LU4 server through a Parsec client window.

The project is intentionally designed around constrained local hardware:
- Intel Core i7-9700
- NVIDIA GTX 1080 Ti 11 GB
- 16 GB RAM
- Windows

The remote game client runs elsewhere. L2 Agent sees and controls only the local Parsec window.

## Core idea

```text
Parsec window
    |
    v
Capture -> Perception -> WorldState -> Agent -> Skill -> ActionValidator -> ActionController
                     ^                                      |
                     |                                      v
              Knowledge / Memory                        SendInput
```

The LLM is a planner, not a frame-by-frame controller.

## Current model plan
Primary experiment:
- Bonsai 2 27B ternary quantized model

Fallbacks:
- Qwen3.5-4B
- Qwen3-4B quantized

The final choice will be based on benchmarks on the target GTX 1080 Ti.

## Initial perception plan
- BetterCam for Windows capture
- OpenCV for deterministic UI parsing
- OCR for game text
- small YOLO-family detector for mobs/NPC/UI objects
- multimodal LLM only as a fallback for ambiguous scenes

## Storage
Initial design:
- SQLite
- FAISS only when semantic retrieval becomes necessary
- local trajectory data

No distributed infrastructure is planned for the first milestones.

## Milestones
- M0: Eyes & Hands
- M1: Perception
- M2: LLM advisory mode
- M3: Autonomous closed loop
- M4: Knowledge/RAG
- M5: Episodic memory
- M6: Learning / policy distillation

See docs/ROADMAP.md.

## Development status
Repository initialized. Implementation should start from M0 only.
