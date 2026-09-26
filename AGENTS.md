# L2 Agent — Codex Instructions

## Project goal
Build a Windows-native agent that can play Lineage 2 on the LU4 server through a Parsec client window. The game itself runs on a remote Windows machine; all observation and control happen locally through the Parsec window.

## Hardware constraints
- OS: Windows
- CPU: Intel Core i7-9700, 8C/8T
- RAM: <= 16 GB total for the whole application stack
- GPU: NVIDIA GTX 1080 Ti, 11 GB VRAM
- No extra HID hardware for now

Design for this machine first. Avoid architectures that assume modern RTX tensor cores, >16 GB system RAM, or >11 GB VRAM.

## Core architecture
Perception -> WorldState -> Agent -> Skill -> ActionValidator -> ActionController -> Parsec -> Remote game

The LLM must never emit raw mouse or keyboard events directly.
It may only select high-level skills and parameters.
Only ActionController may call Win32 input APIs.

## Main components
1. ParsecWindowManager
2. CaptureWorker
3. Perception
4. WorldState
5. AgentCore
6. SkillExecutor
7. ActionValidator
8. ActionController
9. Knowledge/RAG
10. EpisodicMemory
11. GUI
12. TrajectoryRecorder

## Input
Use Win32 SendInput.
All coordinates must be relative to the Parsec client area and internally represented as normalized coordinates where practical.
Never click outside the current Parsec client area.
Always support release_all_keys() and release_all_mouse_buttons().
A hard-stop hotkey must be implemented before autonomous control is enabled.

## Capture
Prefer BetterCam / Desktop Duplication API on Windows.
Capture the Parsec client area only.
Do not stream full-resolution frames through multiprocessing queues. Prefer shared memory or latest-frame buffers.

## Perception
Use deterministic CV where possible.
Examples:
- HP/MP/CP bars: OpenCV, color/shape-based parsing
- fixed UI regions: configured ROI
- text: OCR
- mobs/NPC/UI objects: small detector such as YOLO nano-class model
- multimodal LLM: fallback for ambiguous states, not the primary per-frame detector

## LLM
Primary experimental candidate: Bonsai 2 27B ternary quantized variant.
Fallback candidates: Qwen3.5-4B or Qwen3-4B quantized.
Do not lock the implementation to one model runtime. Define an LLMProvider interface.
Model choice is provisional until benchmarked on GTX 1080 Ti.

The model is used for:
- high-level planning
- tool/skill selection
- interpreting ambiguous screenshots
- conversation with the user
- reflection after failures

The model is not used for:
- per-frame control
- direct keyboard/mouse generation
- deterministic HP/MP parsing
- tight combat reaction loops

## Agent behavior
Use a hierarchical agent.
Fast deterministic loop handles execution and safety.
The LLM is a slower planning layer.

Suggested states:
IDLE -> OBSERVE -> PLAN -> EXECUTE -> VERIFY
Failures may transition to REFLECT -> PLAN.
Danger may transition to EMERGENCY.

At session start:
1. observe the game for a few seconds
2. identify known state
3. retrieve relevant memory and game knowledge
4. infer a likely continuation only if confidence is sufficient
5. otherwise ask the user what the session goal is

## Skills
Examples:
- attack_target
- use_skill
- heal
- rest
- move_camera
- move_to_screen_point
- interact_with_npc
- open_inventory
- open_map
- pickup_loot
- follow_target
- escape
- ask_user

Later composite skills may include:
- farm_area
- complete_quest
- return_to_town
- buy_consumables
- level_until

## Knowledge and memory
Initial stack:
- SQLite for structured metadata and episodic memory
- FAISS for vector retrieval if needed

Avoid deploying PostgreSQL, Redis, Kafka, Elasticsearch, Kubernetes, Airflow, or a separate vector database unless a later ADR explicitly justifies it.

Knowledge sources may include:
- LU4 wiki
- official LU4 pages
- patch notes
- user-approved guides
- later: Telegram news channel if a concrete source is provided

Never invent game mechanics. Persist source URL and retrieval timestamp for external knowledge.

## Learning
Do not start with RL.
First collect trajectories:
state -> goal -> selected_skill -> result -> reward/feedback

Use human demonstrations and LLM decisions as data.
Later consider imitation learning, offline RL, or policy distillation after enough trajectories exist.

## GUI
Use PySide6 unless a later ADR changes this.
Initial GUI should expose:
- Parsec window selection/status
- live preview
- FPS
- current WorldState
- agent state
- current goal
- current skill
- START / PAUSE / STOP
- hard-stop status

Debug overlays are important.

## Engineering constraints
- Python 3.12 target
- Pydantic models for cross-component contracts
- pytest for tests
- type hints required for public APIs
- structured logging preferred
- configuration in TOML/YAML where useful
- Windows is the primary supported platform

Keep dependencies minimal due to the 16 GB RAM constraint.

## Milestone discipline
Do not skip milestones.

### M0 — Eyes & Hands
Parsec window detection, capture, coordinate mapping, input controller, emergency stop, GUI preview.

### M1 — Perception
HP/MP/target bars, OCR, initial object detection, WorldState.

### M2 — Brain advisory mode
LLM consumes WorldState and proposes skills but cannot execute them.

### M3 — Closed loop
Observe -> decide -> skill -> verify.

### M4 — Knowledge/RAG
Wiki/game knowledge ingestion and retrieval.

### M5 — Memory
Episodes, trajectory storage, session continuation.

### M6 — Learning
Policy distillation / imitation learning based on collected trajectories.

A milestone is complete only when its acceptance criteria in docs/ROADMAP.md are satisfied.

## Safety rules
Before autonomous mode:
- hard-stop hotkey exists
- all held keys/buttons can be released
- actions are restricted to Parsec
- unknown key combinations are rejected
- dangerous OS shortcuts are blocked
- action durations are bounded
- focus/window state is checked

Do not implement anti-cheat bypasses, process injection, memory reading, packet manipulation, or game-client tampering. The project observes pixels and controls normal keyboard/mouse input only.

## Development style for Codex
- Read docs/ARCHITECTURE.md and docs/ROADMAP.md before large changes.
- Prefer small, testable modules.
- Add tests with every behavior change.
- После минорных доработок запускайте только тесты, затронутые изменением, включая непосредственно связанные сценарии. Не запускайте весь набор тестов после каждого изменения кода.
- Полный набор тестов запускайте только при закрытии крупной вехи (milestone), а не после отдельных исправлений или промежуточных итераций.
- Do not create generic abstractions until a concrete second use case exists.
- Record meaningful architecture changes in docs/DECISIONS.md.
- Do not silently add heavyweight infrastructure.
- Do not change the chosen milestone without explicit user instruction.
