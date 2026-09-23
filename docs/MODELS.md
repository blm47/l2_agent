# Model Strategy

## Principle
Model selection is empirical.
The target machine is a GTX 1080 Ti 11 GB + 16 GB system RAM, so model-card claims are not enough.

## Primary LLM/VLM candidate
Bonsai 2 27B ternary quantized.

Why it is interesting:
- large parameter count relative to storage footprint
- reasoning
- multimodal capability
- tool-calling oriented use case
- potentially suitable as both planner and vision fallback

Risks:
- non-standard runtime requirements
- Pascal GPU compatibility/performance must be measured
- KV/cache/runtime overhead may reduce usable context
- vision may not be practical concurrently with detector workloads

No production architecture should assume Bonsai 2 until M2 benchmark passes.

## Fallbacks
- Qwen3.5-4B multimodal
- Qwen3-4B quantized text model

The LLMProvider abstraction must make these replaceable.

## Bonsai benchmark plan
Measure at least:
1. text-only structured reasoning
2. tool/skill selection
3. screenshot understanding
4. first-token latency
5. decode tokens/sec
6. prompt processing rate
7. peak VRAM
8. peak RAM
9. stability over repeated requests

Use game-relevant prompts, not generic benchmark prose.

## Context policy
Start small:
- 8k context target
- try 16k only if resources allow

Long-term game memory belongs in retrieval/storage, not permanent context.

## Detector
Use a lightweight YOLO-family nano model first.
Model version is less important than:
- Windows support
- Pascal compatibility
- latency
- export/runtime simplicity

The detector will require a Lineage 2-specific dataset.

Initial classes may include:
- enemy
- selected_enemy
- npc
- player
- corpse
- loot
- dialog_window
- inventory_window
- map_window

Do not over-segment classes before enough screenshots exist.

## OCR
Prefer a lightweight modern OCR implementation.
PP-OCR family is a strong candidate.

OCR should be:
- event-driven where possible
- ROI-based
- avoid full-frame OCR loops

## Embeddings
Choose a small multilingual embedding model only when M4 begins.
Requirements:
- Russian and English support
- low RAM/VRAM footprint
- CPU inference acceptable

Do not download an embedding model before retrieval is implemented.

## Policy model
No policy model is selected yet.
It will be chosen after trajectory data exists.
