<!-- Path: docs/deterministic/README.md | Purpose: Describe the current deterministic runtime honestly and define what RL may reuse. -->

# Deterministic runtime

## Role

The deterministic runtime is the current real-base implementation and a measurable baseline for learning. It is not the project's final architecture and is not assumed correct merely because it has broad test coverage.

Active path:

```text
tools/autonomous_run.py
  -> orchestrator/autonomous_builder.py
  -> orchestrator/stage_*.py
  -> planners/*.py
  -> factorio_mod build-plan executor
```

Use explicit `surface=nauvis` and `force=player` for real-base work.

## Semi-stable mechanics worth sharing

- Live recipe, technology, entity, and inventory exports.
- Versioned snapshots, goals, plans, and reports.
- Collision and occupancy surveys.
- Material preflight and legal build-plan execution.
- Measurements for power, logistics, machines, belts, fluids, and research.
- Safe failure reporting and lifecycle tooling.

Sharing means extracting a narrow interface. It does not mean importing the full orchestrator or its layout choices into training.

## Known weaknesses

- Layout and transport are often added after machine placement instead of planned as one structure.
- Recovery can encode one observed failure as a new deterministic branch.
- Supply-starved machines, finite mall demand, and true capacity shortages have been confused.
- Power, roboport, belt, pipe, and future-expansion corridors are not consistently reserved.
- Fluid item recipes require explicit real-base stages. Battery is supported;
  processing units, concrete/refined concrete, electric engines, explosives,
  express belts, and rocket fuel are still catalog-known but not all executable.
- The first basic refinery is executable. Advanced oil and cracking geometry and
  rate contracts exist, but source-capacity-aware oil expansion is not yet wired
  into the real-base controller.
- Live deployment drift can make tested code differ from running behavior.

Fix these when they block the real runtime or when the fix produces a reusable primitive. Do not let deterministic cleanup displace the RL roadmap.

New power and construction-service infrastructure follows the same material
boundary as production blueprints: electric poles, substations, and roboports
are bot-built ghosts funded through the episode material ledger. Coverage
expansion advances one powered roboport hop at a time; the executor does not
gift a distant pole line or roboport chain to the base.

## Real-base rule

Observe the live factory, identify the planner decision that caused the failure, fix the code and focused tests, deploy deliberately when Lua changed, then rerun from the requested save. Never manually repair the base to conceal a planner defect.

## Planned Factorio 2.1 science integration

[Science telemetry and research control plan](science_control_plan.md) stages
lab-inventory/status observation, deterministic diagnosis and research
ranking, and a disposable-only lab-circuit experiment.  The Python planner
retains research-selection authority until a separately authorized and tested
controller is promoted.

The permanent [OpenCode Helper Agent](opencode_helper_agent.md) is the
implemented per-run observability layer. It starts at `RUN START`, observes the
same isolated run every two minutes, and writes one read-only findings document
after `RUN END`; it is not a controller and does not change planner authority.

For long live-debugging campaigns, use the restart-safe
[OpenCode campaign prompt](opencode_campaign_prompt.md) and its bounded
last-ten-runs journal.

For unattended campaigns, `tools/opencode_campaign_orchestrator.py` owns the
isolated fresh-run lifecycle and starts a **new OpenCode session for each
run**. It persists the active run's session ID, so a controller restart resumes
that same session rather than duplicating an hour-long run. It records a controller/log/logistic-inventory snapshot every two
minutes with a rolling `logs/latest-context.md` packet and short references in
`docs/deterministic/opencode_campaign_observations.md`, uses that
run's session for the checkpoint reviews, then requires its final comparison
against the preceding documented run before one focused edit can trigger the
next episode. Continuation requires both an explicit `change` verdict and a detected
edit; `stop` and `no-change` cannot be overridden by changed files. It also stops
on the same terminal failure twice in legacy mode. In reliability mode, another
run requires an independently reviewed and tested change with a falsifiable
production prediction. The [console supervisor](reliability_workflow.md#operations-console-supervised-12-hour-loop)
adds four aspect observers and bounded process recovery while preserving the
secondary helper role:

```bash
.venv/bin/python tools/opencode_campaign_orchestrator.py \
  --technology mining-productivity-4 --max-runtime-hours 8
```

The controller uses the isolated server managers and the loopback Operations
Console endpoint only; it does not modify the source save or training runtime.

## Mission evidence

Every runner invocation declares a versioned bootstrap supply profile:
`reduced-v1` for the current reduced-stock contract or `supplied-v1` for the
control contract. Managed episode manifests carry the profile; direct runner
calls may select it with `--bootstrap-profile` and otherwise default to
`reduced-v1`. Neither profile grants items. All new deterministic construction
is submitted as blueprint ghosts funded by actual inventory and built by bots.
Existing entities may be configured, but the player-force executor refuses new
direct placements. The former two-requester bootstrap subsidy and post-build
requester replacement are removed. A starting save without enough materials
must expose its shortage; the runtime cannot manufacture the missing seed.

The runner persists one mission across its per-science-pack controller calls in
`deterministic-mission-state.json`. The ledger records episode/save provenance,
profile, mission stages, controller attempts, terminal status, and blockers.
Each terminal blocker is also appended to `deterministic-blockers.jsonl` with a
stable code, lifecycle state, target, and an explicit `bug` or
`intended_difficulty` classification. Legacy untyped failures are classified as
bugs until the originating stage supplies a narrower contract; they must not be
silently counted as intended reduced-supply difficulty.

Deferred work and terminal blockers share the same typed lifecycle contract.
Supply, construction, coverage, power, first-output, retirement, and failure
states therefore remain comparable even when their human-readable messages
change.

Human runner logs write one second-precision absolute timestamp in the
`RUN START: ts=...` header, then prefix later messages with whole-second elapsed
times such as `+15s`. Structured decision JSONL uses schema `v=2`, with `ts`
only on the first event and compact `seq`, `dt`, `type`, and `message` fields.
Readers accept both this format and archived logs that repeat an ISO timestamp
on every line. Helper Agent summaries scope the append-only structured event
file to the newest completed run, so priorities and zero-placement counts from
older episodes cannot contaminate the current diagnosis.
Runner retries whose semantic template is unchanged are emitted at exponentially
spaced occurrence counts with one terminal count summary; changing positions
and zero/nonzero outcomes remain distinct. Process liveness does not enter the
append-only log: the runner overwrites one `autonomous-run.heartbeat.json`
sidecar every ten seconds, while the PID record remains the dashboard's
authoritative running-state probe.
Deferred-control lines remain bounded as the factory grows: they report origin
counts by lifecycle state, at most four active project IDs with the task-item
quantity, the material-ledger revision, and only tracked-stock deltas after the
first observation. Complete project bills stay in the episode material ledger;
the human runner log does not copy every bill and inventory field on each retry.
Unhandled tracebacks retain only their final 24 frames. Helper packets remove
duplicate excerpt lines, cap individual lines and the total excerpt, and refuse
model calls above a 36,000-character prompt ceiling so a 16K local review
context retains room for the system rubric and structured answer.

For compact handoffs and cross-run lookup, use the [run context workflow](run_context.md).

For repeated plastic acceptance and reviewed automatic fixes, use the [reliability workflow](reliability_workflow.md).
