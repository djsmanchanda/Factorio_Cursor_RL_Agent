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
- Bot-assisted construction is limited to building sandbox ghosts and never places real entities directly.
- Construction progress updates current capacity without advancing phases.
- Phase advancement requires explicit proposal and authorization.
- Authorized upgrades are limited to planner-sandbox entities and require explicit approval.
- Authorized deconstruction is bot-mediated only and limited to planner-sandbox entities.

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
- RL advisor (non-authoritative):
	- `python rl_advisor.py <rl_observation.json> [seed]`
	- The RL advisor is advisory only and cannot execute actions.
	- Every RL proposal sets `requires_authorization = true` and must flow through readiness, authorization, and then execution.
	- Safety boundaries: read-only inputs, no state mutation, no Lua calls, and no bypass of human/bot authorization.
	- Enriched observation fields now include `bot_utilization_ratio`, `power_stress_ratio`, `construction_backlog_estimate`, `phase_completion_ratio`, and `factory_density_score`.
	- These enrichment values are deterministic and derived from existing metrics/progress (see `core.metrics.derive_rl_observation_health`).
	- Future training hook points: replace the deterministic scoring policy in `rl_advisor.py` with a trained policy/value model while preserving schema validation and authorization gating.
- RL feedback builder (pre-training instrumentation):
	- `python rl_feedback_builder.py <progress_state.json> <metrics_summary.json> [construction_report.json] [execution_report.json]`
	- Builds deterministic, schema-validated RL feedback telemetry from read-only artifacts.
	- Closes the observational loop by attributing outcomes of authorized execution/construction without granting any control authority.
	- This is signal plumbing only for future training; no learning, no policy updates, and no state mutation are performed.
- Execution report validation:
	- `python tools/execution_reporter.py <execution_report.json>`
- Construction report validation:
	- `python tools/construction_reporter.py <construction_report.json>`
- Upgrade report validation:
	- `python tools/execution_reporter.py <upgrade_report.json>`
- Deconstruction report validation:
	- `python tools/execution_reporter.py <deconstruction_report.json>`
	- Deconstruction actions support both named targeting and position-only targeting.
- Construction progress update:
	- `python -c "from core.construction_progress_updater import update_progress_from_construction; import json; print(update_progress_from_construction(json.load(open('progress_state.json')), json.load(open('construction_report.json')))[0].to_dict())"`
- Phase advance proposal:
	- `python -c "from core.phase_advance_evaluator import propose_phase_advance; import json; print(propose_phase_advance(json.load(open('progress_state.json')), json.load(open('construction_progress.json'))).to_dict())"`
- Phase advance authorization:
	- `python -c "from core.phase_advance_evaluator import authorize_phase_advance; import json; proposal=json.load(open('phase_advance_proposal.json')); print(authorize_phase_advance(proposal, True, 'test', 'approved').to_dict())"`
