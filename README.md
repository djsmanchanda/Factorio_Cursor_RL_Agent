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
- Progress reconciliation compares observed ghosts with planned progress state
	and emits a read-only reconciliation status.
- Execution readiness proposes permitted next actions without executing them.
- Execution authorization is mandatory before any execution actions are allowed.
- Authorized ghost execution is limited to ghost placement only and remains sandboxed.

## Tooling

- Inspect Progress State:
	- `python tools/inspect_progress.py <snapshot.json> <metrics.json> <build_intent.json>`
- Phase-aware Ghost Projection:
	- Requires BuildIntent + ProgressState + CapacityPhasing (no CLI yet)
- Observe GhostPlan sandbox:
	- `python tools/ghost_observer.py <ghost_observation.json>`
- Progress reconciliation:
	- `python -c "from core.progress_reconciler import reconcile_progress_state; import json; print(reconcile_progress_state(json.load(open('progress_state.json')), json.load(open('ghost_observation.json'))).to_dict())"`
- Execution readiness:
	- `python -c "from core.execution_readiness import propose_execution; import json; print(propose_execution(json.load(open('progress_state.json')), json.load(open('capacity_phasing.json')), json.load(open('build_intent.json')), 'OK').to_dict())"`
- Execution authorization:
	- `python -c "from core.execution_authorizer import authorize_execution; import json; proposal=json.load(open('execution_proposal.json')); print(authorize_execution(proposal, proposal['allowed_actions'], 'test').to_dict())"`
- Execution report validation:
	- `python tools/execution_reporter.py <execution_report.json>`
