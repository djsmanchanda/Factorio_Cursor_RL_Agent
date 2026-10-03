<!-- Path: docs/rl/Laya_Clean_Runner.md | Purpose: Design of the clean Laya runner: existing deterministic tooling for observe/realize/execute, Laya for what and how. -->

# Laya clean runner

Status: adopted direction 2026-10-03 (user). First runner slice implemented
and run live on disposable `mod_playground` copies (see Current state). The deterministic workflow stays usable
and unchanged until Laya matches and exceeds its milestones.

## Why

The deterministic workflow reaches advanced circuits from `mod_playground`,
but its decision code keeps growing and breaks on unseen conditions. From
2026-08-01 to 2026-10-03 it took 309 non-RL fix commits, and
`orchestrator/autonomous_builder.py` grew from 1,344 to 13,212 lines. Roughly
40% of fix subjects are decision/timing rules (gate, defer, ration, scale,
reserve), ~23% layout mechanics (mostly belt/pipe routing) and ~20%
runner/observability. Mall slots/stock was the largest subsystem.

The decisions are what break. The tooling underneath (reading the base,
blueprint ghosts, upgrades, deconstruction, research, measurement) is the
valuable, reusable part.

## Split

| Layer | Owner | Grows by |
|---|---|---|
| Tooling: observe, enumerate legal alternatives, realize plans, validate, execute, measure | Existing server/mod tooling behind thin adapters | New Factorio mechanics only |
| Status board: goal demand, measured supply, machine conditions, upgrade facts, bottleneck chain | `training/laya_factory_status.py` | Better observations |
| Goal ladder: C0–C6, then science for `mining-productivity-4` | Data, checked by `orchestrator/checkpoint_milestones.py` | Data edits |
| Guidelines: the user's strategy as soft priors | `docs/rl/Laya_Strategy_Guidelines.md`, quoted by `docs/rl/laya_mission_guidelines.json` | Doc edits |
| What and how | Laya | Training data and checkpoints |

Laya decides **what** (which goal need, which operation family) and **how**
(site, scale, tier, transport mode, layout variant). Tooling only enumerates
the legal alternatives and realizes the chosen one exactly.
`docs/rl/laya_decision_authority_inventory.md` lists which deterministic
functions are decision authority (not reused) and which are mechanics (reused).

## Tooling reused

- Observation: `orchestrator/game_bridge.py` (`request_snapshot`,
  `logistic_inventory`, `research_status`, `science_status`,
  `export_recipe_catalog`, `export_ghost_observation`), `orchestrator/live_base.py`
  reads (statuses, machine health, stock, power, roboports, clear areas).
- Realizers: `planners/*` layouts (resource, smelter, mall/quad, fluid,
  refinery, electronics, science chains), `planners/assembler_tiers.py`
  upgrade plans, `orchestrator/stage_services.py` power/coverage primitives.
- Validation: `planners/plan_validation.py`, affordability and material ledger.
- Execution: `execute_ghost_plan`, `execute_upgrade_plan`,
  `execute_deconstruction`, `set_research`; bots build funded ghosts only.
- Measurement: production counters, `chain_telemetry`, milestone predicates.

Each adapter states its input/output contract and is tested on its own. If a
realizer embeds a choice (picks the "best" site or tier), the adapter exposes
the alternatives instead of calling the choosing wrapper.

## Operations

| Operation | Laya chooses | Tooling |
|---|---|---|
| mine | resource, patch site, drill count, output mode | resource survey, mining layouts |
| smelt | plate, furnace count, site, feed mode | smelter layouts |
| mall slot | recipe for a slot, permanent vs rotating, rotate/retarget | mall/quad layouts, recipe configuration |
| assemble line | recipe, machine count, tier, feed mode | line/chain layouts |
| fluid | oil/refinery/chemical module and variant | fluid/refinery layouts |
| power / coverage | where to extend | power and roboport primitives |
| upgrade | which machines/inserters, target tier | assembler tier upgrade plans |
| retire | which owned structure | bot deconstruction |
| research | technology, lab feeding | research actuator, science status |
| wait / observe | horizon | measurement |

Transport mode is a Laya "how" choice where both realizers exist. The
strategy guideline is bots for one or two machines and mall machines, direct
belt transfers once demand and sustained production exceed that; the status
board's `machines_needed` and measured rates are the facts Laya applies it to.

## Never-crash contract

- Every adapter returns `ok` or a typed failure with evidence; it never raises
  through the run loop.
- Unknown facts stay unknown in the status board; wait, observe and retire are
  always offered.
- Model uncertainty or failure means wait/escalate, never the deterministic
  cascade.
- One journal line per decision: status digest, options, choice,
  probabilities, execution receipt, measured outcome.

## Change rule

A fix to the Laya runner may only:

1. correct a tooling adapter that realized or executed something wrong;
2. add a genuinely new Factorio mechanic;
3. improve an observation;
4. add or edit a guideline, goal, or training data.

It may not add a situation-specific decision branch. Repeated bad decisions
become training cases or guideline edits.

## Acceptance

Same `mod_playground` copy, starter kit, mods and timing as the deterministic
runner. Laya must reach C0–C6 and then complete `mining-productivity-4`
research, measured by the same milestone predicates, with zero safety
violations, before it replaces anything. Mall-simulation scores are separate
evidence.

## Current state

Console: `.venv/bin/python tools/laya_ops_server.py` → http://127.0.0.1:9140/
(modelled on the deterministic console at 9137): game/RCON/runner status and
connect address, mission ladder, status board with bottleneck and blocked
needs, latest choice and menu, recent decisions, provider stock, guidelines in
use, live log tabs, and fixed actions (start/stop runner, fresh episode,
start/stop server, sync GUI mods, stop console). One episode is current at a
time (`data/laya-saved-world/laya-ops-current.json`); default ports 34230/27130.

CLI: `tools/run_laya_clean.py prepare|run|stop --root data/laya-saved-world/<name>`.
`run` is live by default (game unpaused at `--speed`, decisions taken while the
factory runs) and unlimited with `--decisions 0`; it ends on mission complete,
safe stop (3 consecutive failures) or SIGTERM after the current decision.
`--stepped` keeps the paused fixed-tick mode. Artifacts under the root:
`logs/laya-runner.log` (deterministic `RUN START`/`+Ns`/`RUN END` format),
`laya-runner.pid.json`, `laya-runner.heartbeat.json`,
`evidence/clean-decisions.jsonl`, `evidence/latest-status.json`, transaction
receipts. Checkpoint save/resume is not implemented.

After every run: the run is appended to the shared LLM-free run ledger
(`tools/run_ledger.py` → `~/.local/share/factorio-rl/run-ledger.jsonl` and
`.csv`: milestones, outcome, failure cause, limiting factor, log file,
commit; deterministic runs are recorded the same way) and the read-only
OpenCode Laya Helper (`tools/laya_helper_agent.py`, config
`docs/rl/laya_helper.json`) compares it with the latest Laya run from an
earlier commit and the deterministic reference, writing
`docs/rl/laya_helper_runs/<run_id>/findings.md`.

- World interface: the saved-world adapter (live legality/funding checks,
  mod layout executor, exact tick stepping, receipts).
- Options are tagged with the need they serve; construction items that checks
  report missing become `stock:<item>` needs; options whose main entity
  already exists are pruned as `no_op`; needs without legal options are journaled with reasons.
- Power (`training/laya_power.py`): existing coverage, a medium-pole cover, a
  big-pole relay for long distances, or a shared substation for mine/mall
  builds only; never substation relay chains. The policy chooses.
- Land (`training/laya_site_plan.py`): the first decision of an episode picks
  the mall site; ore patches, smelting strips beside the nearest iron, copper
  and stone patches, and the mall zone are reserved and drawn in game. Options
  that take another purpose's land are rejected (`reserved:*`).
- Mission order starts with C0 (iron and copper starter plates) before the
  starter mall, so mall builds are not offered until C0 is measured.
- Policy: seeded exploration (`authority: exploration`) until a compatible
  Laya checkpoint exists. Its decisions are training data, not learned play.

Live evidence 2026-10-03 (worlds `clean-20261003-a/b/c`, 219 decisions, 0
failures): from the starter kit the runner built iron/copper mines, plate
furnaces, cable/gear/long-inserter mall slots and coverage; world C satisfied
5 of 7 C1 needs. Blockers it reported: substations exhausted by relays
(substations need steel and advanced circuits), stone mines outside logistic
reach, iron gear blocked by the substation shortfall. No phase reached yet.

## Delivery slices

1. Status board — done (`0b4b4d2`).
2. Strategy guidelines as data — done; goal ladder as data next.
3. Done in part: mine, smelt, mall slot, coverage and wait via the
   saved-world adapter. Next: an all-medium-pole power realizer, upgrade
   (`execute_upgrade_plan`), retire, belt-fed line variants from the
   deterministic planners. Adapters for C0–C3 operations (mine, smelt, mall slot, power/coverage,
   upgrade, retire, wait) over the existing tooling, each with focused tests.
4. Never-crash run loop on a fresh `mod_playground` copy, journaling every
   decision; reach C1–C2 with exploration/guideline priors.
5. Measured alternatives, compatible checkpoint, then fluids/plastic/advanced
   circuits and science.
6. Matched head-to-head against the deterministic runner.

Coordinate before editing files reserved in
`docs/rl/Laya_Mall_Training_Active_Handoff_2026-10-03.md`.
