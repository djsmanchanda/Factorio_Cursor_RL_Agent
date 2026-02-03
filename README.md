# Factorio Autonomous Planning Agent

An autonomous planning + execution system for Factorio that can:
- Understand high-efficiency grid and line layouts
- Scale production symbolically
- Auto-prioritize construction zones
- Execute plans efficiently (eventually via RL)

This is **not** an end-to-end RL bot.
It is a factory compiler with an execution agent.

Core philosophy:
> Planning is symbolic. Execution is learned.

Additional principles:
- Progress State is read-only and derived from snapshot + metrics + prior intents
- Capacity Phasing realizes a fixed target capacity in deterministic stages
- BuildIntent represents ultimate intent; GhostPlan represents current materialization
- Early-game efficiency choices are policy-driven, not heuristic

Supervision:
- Capacity phasing is a supervisory policy that selects the next allowed capacity phase
	without executing or placing anything.

## Tooling

- Inspect Progress State:
	- `python tools/inspect_progress.py <snapshot.json> <metrics.json> <build_intent.json>`
