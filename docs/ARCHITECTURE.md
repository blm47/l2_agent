# Architecture

## 1. System boundary
The Lineage 2 client runs on a remote Windows machine.
The local machine runs Parsec and L2 Agent.

L2 Agent may:
- capture the Parsec client area
- parse pixels
- run local ML models
- move the local mouse
- send keyboard/mouse input through Win32 SendInput
- store local knowledge, memory, and trajectories

L2 Agent must not depend on reading game process memory, network packets, or client internals.

## 2. High-level data flow

```text
User goal / session context
          |
          v
     +-----------+
     | AgentCore |
     +-----+-----+
           |
           v
      selected Skill
           |
           v
    +-------------+
    | SkillExecutor|
    +------+------+
           |
           v
   +---------------+
   |ActionValidator|
   +-------+-------+
           |
           v
   +---------------+
   |ActionController|
   +-------+-------+
           |
           v
      Parsec window
           |
           v
      remote game
           |
           v
      rendered frame
           |
           v
   +---------------+
   | CaptureWorker |
   +-------+-------+
           |
           v
    +-------------+
    | Perception  |
    +------+------+
           |
           v
      WorldState
           |
     +-----+------+
     |            |
     v            v
 AgentCore   Memory/Recorder
```

## 3. Timing model
Not all components run at the same frequency.

Suggested initial targets:
- capture: 30 FPS
- deterministic UI parsing: 10-30 Hz depending on cost
- object detection: 3-10 Hz
- OCR: event-driven or ~1-2 Hz
- LLM planning: on state changes / failures / user goal changes, typically << 10 Hz

These are starting points, not hard guarantees.

## 4. ParsecWindowManager
Responsibilities:
- discover Parsec top-level window
- expose HWND
- expose client-area screen coordinates
- detect minimize/restore/resize/focus changes
- convert normalized coordinates to client/screen coordinates
- prevent stale coordinate use after resize

## 5. CaptureWorker
Preferred backend: BetterCam / Desktop Duplication API.

Responsibilities:
- capture only Parsec client area
- provide latest frame
- timestamp frames
- track FPS
- avoid queueing stale frames

The consumer should usually process the newest available frame, not every historical frame.

## 6. Perception
Perception should combine specialized modules.

### UI bars
OpenCV-based parsing for:
- HP
- MP
- CP
- target HP
- party health
- experience progress if useful

### OCR
Used for:
- mob/NPC names
- quest text
- system messages
- zone/location names
- inventory labels
- chat when explicitly needed

### Detector
A lightweight detector may identify:
- enemy
- selected enemy
- NPC
- player
- corpse
- loot
- key UI windows

### Vision LLM fallback
Used only when structured perception cannot interpret the scene confidently.

## 7. WorldState
WorldState is the canonical agent input.
All perception modules write structured observations, not prose.

Example:

```json
{
  "timestamp": 0.0,
  "player": {
    "hp": 0.82,
    "mp": 0.61,
    "cp": 0.97
  },
  "target": null,
  "enemies": [],
  "npcs": [],
  "ui": {
    "inventory_open": false,
    "map_open": false
  },
  "location": {
    "name": null,
    "confidence": 0.0
  },
  "confidence": 0.88
}
```

See docs/WORLD_STATE.md.

## 8. AgentCore
AgentCore is a slower reasoning layer.

Inputs:
- current WorldState
- current session goal
- recent episodic context
- relevant RAG documents
- skill catalog
- last skill result

Outputs:
- selected skill
- arguments
- optional rationale for logs
- optional user question
- confidence

The LLM must return schema-valid structured output.

## 9. Skills
Skills hide input-level details.

Example:
`attack_target(target_id)`

The LLM does not know how many pixels to move or which exact key-down sequence is necessary.

SkillExecutor:
- converts high-level intent into bounded low-level actions
- monitors success/failure
- may run short deterministic loops
- returns SkillResult

## 10. ActionValidator
Validation is mandatory.

Checks include:
- active Parsec target
- coordinate inside client area
- allowed keys/buttons
- bounded key-down duration
- blocked OS shortcuts
- hard-stop state
- focus assumptions
- rate limits

## 11. ActionController
The only module allowed to call SendInput.

Required primitives:
- key_down
- key_up
- key_press
- mouse_move_relative
- mouse_move_absolute_client
- mouse_button_down
- mouse_button_up
- click
- release_all

## 12. Knowledge
Knowledge is sourced information about the game/server.

Initial sources:
- LU4 official pages
- LU4 wiki
- patch notes
- later user-approved sources

Every stored knowledge chunk should retain:
- source URL
- retrieval timestamp
- title
- content
- optional category/entity metadata

## 13. Memory
Episodic memory stores the agent's experience:
- session goals
- locations encountered
- successful/failed routes
- skill results
- user corrections
- deaths/failures
- useful discovered facts

Knowledge and episodic memory must remain conceptually separate.

## 14. Trajectory recording
Each meaningful decision should be recordable as:
- observation / WorldState
- goal
- selected skill
- skill arguments
- result
- reward or user feedback
- timing
- optional frame references

This dataset is the basis for future policy learning.

## 15. Process model
Initial implementation may begin single-process for M0.
When load justifies it:
- GUI process/thread
- capture worker
- perception worker
- LLM worker
- storage worker

Large frames should use shared memory/latest-frame buffers, not serialized queues.

## 16. Resource policy
The application must be usable on:
- 16 GB RAM total
- 11 GB VRAM

Model runtimes and perception components should support lazy loading or configurable disabling.

## 17. Extensibility rule
This project targets one game: Lineage 2 / LU4.
Do not build a generic multi-game plugin framework until a concrete requirement appears.
