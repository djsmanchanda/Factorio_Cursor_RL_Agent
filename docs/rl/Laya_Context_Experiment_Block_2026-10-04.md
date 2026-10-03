<!-- Path: docs/rl/Laya_Context_Experiment_Block_2026-10-04.md | Purpose: Frozen eight-hour context, bootstrap and connected production experiments with a dedicated Muse observer. -->

# Context and connected production experiment block

This suite compares the current clean runner against explicit context/request
variants, then measures electronic-circuit and plastic chains in separately
fixtured disposable worlds. It leaves baseline weights and production authority
unchanged. No compatible neural Laya checkpoint is claimed: the actor remains
**guided exploration**. Text-only observation additions are an ablation control,
not a claim that a language model learned to use them.

Run root: `data/laya-experiments/context-growth-20261004/`.
The eight-hour block's timestamps/deadline are in `suite.json`; preparation
and helper observation are included in that window. There is no ten-hour
preflight allowance or post-deadline external model call. Actor ports are
**34300–34311 / 27200–27211**, separate from the console, mall, viewer,
deterministic server and earlier experiment allocations.

## Controlled factors

Raw starter cohorts use the same immutable base save, kit, mod copies, target,
speed and RNG seeds across variants. Settings are declared in
`docs/rl/laya_variants/context-20261004/`:

- baseline (unchanged defaults);
- requester multipliers 1 and 3, versus default recipe horizons;
- eight versus fifteen visible strategy guidelines;
- broader option enumeration/menu exposure;
- dependency/stock-lock context, with an observation-only control;
- the same dependency context with a reviewed soft stock-conservation weight;
- a longer no-progress window (24 versus 12 decisions).

Request-size changes are applied to cloned experimental candidates before
funding checks; identities are rehashed. Shared mall generators/defaults are
not edited. Dependency context reports requests, available input crafts,
unbacked inputs and which currently funded alternatives a draw would block.
It never chooses or substitutes an action. Existing rule text is quoted and
verified; the experimental preference is an explicit catalog patch.

The four two-hour segments cover C1 bootstrap, C2 mine/mall/smelter coupling,
component supply/transport/capacity interactions, then further confirmation
with fresh RNG seeds. The baseline remains in every matched comparison. Raw
runs share one terrain/base scenario; these seeds establish repeatability on
that base, not unseen-terrain generalization or independent calibration groups.
No automatic adoption, promotion, fine-tuning or production writes occur.

## Connected mechanics domain

`training/laya_component_lab.py` reuses `LocalLayoutPlanner` line/chain
realizers, paired mall cells, fluid machine rows and purity-aware fluid links.
Bots build funded ghosts. Fixture inputs, power, research and construction
budgets are explicit; intermediates/products are never gifted.

Circuit cases compare belt and robot delivery, 2/3/4 cable assemblers feeding
2 circuit AM2s, requester buffers 1/3/15, half input rate and delayed input.
Plastic cases compare 1/2/3 basic-oil refineries feeding 2 chemical plants,
coal supply reduction and input delay. Crude oil is a declared source;
petroleum gas and plastic are genuinely manufactured. Context variants also
choose among legal stage builds and waits, with the complete distribution,
features, scope, transactions and engine outcomes recorded.

The mechanics force completes research as a declared fixture. Actual inserter
capacity and per-recipe productivity bonuses are recorded. Observed productivity
also enters recipe demand/capacity arithmetic; missing bonuses retain the old
zero-bonus behavior. Fixture successes do not mean the raw runner reached C4:
its fluid action path remains an open capability gap.

Preflight engine evidence (all 7,200-tick windows):

| Test | Measured output | Notes |
|---|---:|---|
| Circuit belt chain, 3 cable / 2 circuit AM2 | 3 circuits/s | 9 cable/s produced and consumed, 3 iron/s and 4.5 copper/s consumed; zero ghosts |
| Circuit bot chain, one-craft requests | 0.7083 circuits/s | Production and delivered output are separately measured; transport/buffer bottleneck |
| Circuit bot chain, fifteen-craft requests | 3 circuits/s | Same supplied inputs and machine counts; early-bootstrap stock risk is a separate question |
| 2 refineries / 2 plastic plants | 1.9833 plastic/s | 18 gas/s and 0.9 coal/s consumed; observed research productivity, not an inventory-derived rate |

Evidence: `data/laya-saved-world/component-circuit-pilot-f/`,
`component-bots-pilot-b/`, `component-bots-buffer15-pilot/`, and
`component-plastic-pilot-c/`. The raw `context-c1-pilot-a` stalled before copper,
showing that smaller requests alone are not a demonstrated bootstrap fix.

Preserved preflight tooling failures: circuit a exported a recipe as an item
counter; b used an invalid typed history enum; c required power for a belt-only
link; d/e lacked existing fixture/conveyor geometry in power observations.
Plastic a used the removed fluidbox access; b left refineries unwired because
they were absent from the powered-entity palette. Bot pilot a lacked requester
coverage. These became reusable observation/realization fixes, not coordinate
exceptions or policy labels. All component attempts are in the `rl-lab` ledger;
raw attempts are in the standard Laya index/ledger.

## Observer and check-ins

`tools/laya_experiment_helper.py` adapts the deterministic and Laya helper's
session/provenance approach to the **whole experiment suite**. It uses exactly
**OpenCode Go Muse Spark 1.3 Contributor, xhigh**, one retained session, bounded
packets and new checkpoint findings. Permissions explicitly deny edits, shell,
RCON/lifecycle, delegation and other unapproved tools. The script saves final
text; the model cannot write repository files or mutate worlds.

It observes during the block and reviews selected complete trajectories,
matched comparisons, dependency failures and context effects. Findings live in
`<run>/helper/findings-*.md`, with the newest in `helper/findings.md` and raw
transcripts retained. Two-hour check-ins are `checkin-{2,4,6,8}h.json`; review
requests are processed in the same observer session. Recommendations remain
advisory and separate from execution.

Operational failures, interrupted comparisons and missing terminal evidence are
excluded from usable policy comparisons. Component capacity shortfalls remain
measured outcomes. Three repeated component infrastructure failures quarantine
that case instead of endlessly retrying. The helper must distinguish correlation
from matched intervention evidence and never label untried alternatives or
all-failed runs as positive training examples.

## Ownership, controls and lifecycle

The supervisor is `tools/run_laya_context_suite.py`; `suite.json` identifies its
unit, frozen code commit, copied source/mod inputs and deadline. Worker roots
are under the frozen checkout's `data/laya-saved-world/` namespace. Each endpoint
has an explicit slot lease. Cleanup verifies the exact disposable process,
ports, source and mod hashes before stopping only that owned host. User-systemd
hard supervision bounds the lifetime of the complete actor cgroup.

Progress: `progress.json`, `jobs.jsonl`, `analysis.json`, final `report.json`.
The helper is read-only and has a separate bounded lifetime. Existing console
9140 is not restarted or repointed. Installed mods, real saves and other
runtimes are not deployed or reset. Source save hash remains
`9763436071d2ad138e2844a81c7bbd6891218f612c9bf69dc5bede6517c6d5bb`.

Lifecycle: new private experiment launches load their frozen helpers. Existing
runners receive no restart. Later use of the changed lab helper on an existing
worker requires deliberate deployment and that worker/consumer restart.

Verification: 64 focused tests passed, plus native Lua parsing, Python
compilation, live circuit/plastic/buffer pilots and a successful exact-model
read-only Muse review. Validation covers unchanged defaults, cloned request
identities, stock-lock arithmetic, productivity demand, assembly/fluid geometry,
protected power obstacles, ownership expiry and exclusion of invalid pairs.
