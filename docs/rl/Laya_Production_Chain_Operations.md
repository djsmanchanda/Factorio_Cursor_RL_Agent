<!-- Path: docs/rl/Laya_Production_Chain_Operations.md | Purpose: Define available chain facts, candidate operation hooks and honest post-C6 gaps. -->

# Production-chain operations and evidence

Status: offline implementation on `f40b875`, 28 October 2026. This is not an
adopted variant, a deployed runtime, or evidence of improved cohort outcomes.
The C43 repeated reference is `9d9b279`. C45 steel candidate `33a7c73` and
C46 consumer candidate are frozen, queued experiments, not established wins.
Their code was inspected, not copied wholesale.

## Observed consumer

The saved-world survey optionally reports `unit_number` and
`products_finished` for crafting machines. `ConsumerObservation` compares
consecutive surveys on the same disposable Nauvis/player scope and unchanged
mods and recipe catalog. It reports positive counter progression only for an
identified, currently surveyed machine on the same recipe consuming an
advanced circuit, at increasing ticks. First sightings, missing identity or
counter, missing scope/catalog, resets, retargets, disappearances, changed
catalogs and non-increasing ticks cannot establish progression. A reset clears
the baseline. The observation is **not latched** and has no choice baseline.
It cannot detect an unobserved intermediate retarget and assumes the surveyed
isolated surface/force is exclusively controlled by the disposable run. Its
decision-state digest is `advanced_circuit_consumer`.

This fact is not an acceptance predicate. Strict deterministic `c6-v1` remains
unchanged and separately reported by its existing observer. Later reference
code's `laya-c6-choice-output-v2` is an explicit-choice/output proxy; C46's
candidate v3 adds consumer continuity, but neither is strict `c6-v1`. This
baseline predates those v2/v3 implementations. Its existing rate-based phase
is yet another proxy, not strict C6. None may be relabeled as another.

## Independent experimental factors

All four settings default to false and have individual loadable JSON variants
under `docs/rl/laya_variants/`. They do not change weights or exploration.

| Variant | Behavior | Evidence limits |
| --- | --- | --- |
| `consumer-menu-v1` | Adds catalog-backed solid advanced-circuit consumer batches through existing `produce`; validates observed one-craft free inputs and existing mall capability. | The input bill excludes construction and future delivery. Strict C6 is unchanged. |
| `solid-fluid-filter-v1` | Removes observed fluid shortages before forming the capped solid batch menu. | Filtering creates no fluid delivery. Untyped modded fluids remain a gap. |
| `committed-chain-facts-v1` | Adds registered project bills, allocations by owner, joint shortfall, requester demand and displaced lender recipe to the observation digest. | Future construction plans are not priced; requester demand is not reserved; lost lender throughput is unknown. |
| `production-services-v1` | Adds unmet supported permanent production choices using the shared `ensure_produced(upgrade_bootstrap=True)` executor. | Reuses builder prerequisite selection. Provider availability is not sustained output or delivery. |

Committed construction accounting uses one surveyed stock pool. Individual
owners can each be feasible while their combined bill is unaffordable.
Already submitted construction is shown in flight and is not charged again.
Allocations exceeding observed transferable stock produce unknown. This
projection does not allocate stock or add planner geometry. The independent
`bill_facts` helper accepts caller-verified complete disjoint bills; it does
not discover a future chain's bill.

Permanent solid services require an enabled observed recipe matching the
shared recipe ingredients. Plastic, sulfur, acid and battery use existing
chemical operations, with typed ingredients and output quantities matched to
`FLUID_RECIPES`. Plate/mine lifecycles retain their existing explicit menu.
A prerequisite advance returns `ok:false, code:prerequisite_advanced`; an
available provider returns `producer_available`. Both explicitly record
`sustained_service_verified:false`. No new geometry is implemented here.

The ops training journal separately records policy and executor wall time.
Model-client timing remains unknown when the policy supplies no measurement.
Summary fault accounting separates expected blocks, typed bugs and untyped
faults, and distinguishes event counts from affected-run booleans. Exhausting
the proxy phase ladder reports `phase_ladder_complete`, with research completion
unverified; it no longer implies the full mission has completed.

## Remaining work

- Complete planner previews must include whole-chain construction bills,
  requester ownership and measured lender opportunity costs.
- Permanent services still need live validation of belts, connected fluids,
  output and sustained delivery. Lab delivery/consumption and research actions
  are absent; mining-productivity-4 is not established as achievable.
- Steel-site C45 candidate `33a7c73` is not imported or adopted here.
- Outcome-aware training requires joined, qualified downstream evidence;
  these facts and timings alone do not establish learned improvement.

## Separate regression recipes for the checkpoint-suite owner

These are specifications, not existing runner configuration or claimed runs.
Freeze source and uncommitted diff, use matched seeds and account for every
planned job, including faults and unstarted jobs. Keep common policy,
exploration, tick budgets and strict predicate versions fixed.

| Factor ID | Start and successor | Window and guardrails |
| --- | --- | --- |
| `consumer-observation-v1` | C5 saved checkpoint; strict `c6-v1` remains independent. | Fixed 540,000-tick maximum or identical earlier terminal condition; check machine/recipe/identity resets and simultaneous strict evidence; no menu change. |
| `consumer-batch-menu-v1` | C5 checkpoint; successor strict `c6-v1`. | Same window; toggle only consumer choice hook, count unsupported/unfundable choices, production, typed blocks, safety faults and early-service regressions. |
| `solid-fluid-filter-v1` | Fluid-shortage checkpoint; successor strict C4/C6 independently. | Same fixed window; toggle only filter; retain valid solid choices and measure actual fluid delivery, safety and all planned terminal states. |
| `joint-bill-contract-v1` | Replay captured competing construction claims; no successor milestone implied. | Fixed recorded observation and bills; verify shared-stock totals, unknown provenance, reservation ownership and independently funded versus jointly funded. No policy arm until planner integration. |
| `steel-template-c45` | C1/steel checkpoint; strict C2/C3/C4, then C6. | Evaluate frozen `33a7c73` separately; preserve collision, ore and reservation rejection, and count all terminal states. Do not import that arm into another factor. |

## Reproduction and lifecycle

From this checkout, with the parent checkout's virtualenv if necessary:

```sh
/home/miniship/Github_Desktop/Factorio_Cursor_RL_Agent/.venv/bin/python -m pytest tests/test_laya_chain_facts.py tests/test_laya_ops_runner.py tests/test_laya_ops.py tests/test_laya_saved_world.py tests/test_laya_fluid_status.py -q --tb=short -o addopts=''
/home/miniship/Github_Desktop/Factorio_Cursor_RL_Agent/.venv/bin/python -m pytest -m fast -q --tb=short -o addopts=''
```

No live episode, save, installed mod or campaign is modified by this change.
Integrate surgically with the dirty original checkout, especially its survey
files. Queue any disposable frozen-code validation after active campaign
deadlines; a Python/code freeze must include the Lua and schema. This survey is
embedded by the adapter and is not an installed-mod deployment. No policy is
adopted and no real-base action is authorized.
