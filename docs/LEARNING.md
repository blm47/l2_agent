# Learning Strategy

## Phase 1: no RL
The initial agent is rule/skill based with LLM planning.
The priority is robust observation and action execution.

## Trajectory format
A trajectory record should be able to store:
- session id
- episode id
- timestamp
- frame/state reference
- WorldState
- user goal
- agent proposal
- selected skill
- skill arguments
- result
- user correction
- reward/score
- latency
- model metadata

## Sources of demonstrations
- user-controlled sessions
- user corrections
- successful LLM-driven sessions
- scripted training scenarios

## Future learning options
After enough data exists:
1. behavior cloning / imitation learning
2. policy distillation
3. offline RL
4. uncertainty-based routing to LLM

## Desired end state
Common known states should be handled by a small fast policy.
The LLM should handle:
- novel states
- strategic planning
- failure recovery
- user communication

## Evaluation
Never train and evaluate on the same episodes.

Create:
- train split
- validation split
- held-out scenario set

Track success by game outcome, not only action imitation.
