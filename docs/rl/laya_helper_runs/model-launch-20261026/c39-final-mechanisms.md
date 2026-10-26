<!-- Path: docs/rl/laya_helper_runs/model-launch-20261026/c39-final-mechanisms.md | Purpose: Final C39 mechanism, chest exposure, fault and measurement audit. -->
# C39 final mechanism audit — 2026-10-27 IST

Research-only report for parent 94dcfaf2. No delegation, cross-thread messaging, live actions, source/freeze changes, git mutations or account changes. Lifecycle: **nothing**. Parent retains base selection and cleanup authority.

## Scope and denominator

Audited `data/laya-experiments/laya-c39-2123-miniship-20261026` against orchestration `state.json`, `chest_exposure.py`, variant adoption rules and final `report.md`. All **120 distinct job IDs**, **60 distinct seeds per arm (80240–80299)**, completed; all return codes zero. Reference **9d9b279**, integrated **f098f9f**, common harness f098f9f. Read all 120 decision-state journals: **20,995 reference + 5,147 integrated = 26,142 rows**. All present and parseable; job evidence-error/malformed-row totals zero. Terminal status is not milestone success. Unit inactive/success is supplied task context, not independently queried here. This is a whole-base comparison, not a chest-only intervention.

## Standing-chest and consumer exposure

Counts below are unique runs offered / chosen / returned `ok: true`, derived by the supplied exposure tool and independently checked against raw option/choice/outcome records.

| Arm and option | Offered | Chosen | OK |
|---|---:|---:|---:|
| Integrated mall_slot.passive-provider-chest | 14/60 | 14/60 | 0/60 |
| Integrated produce.passive-provider-chest | 14/60 | 14/60 | 0/60 |
| Integrated requester-chest, either family | 0/60 | 0/60 | 0/60 |
| Reference mall_slot, either logistic chest | 0/60 | 0/60 | 0/60 |
| Reference produce.passive-provider-chest | 46/60 | 44/60 | 7/60 |
| Reference produce.requester-chest | 15/60 | 14/60 | 0/60 |

Integrated's same 14 runs selected both provider options: nine reached C2 (of 13 C2 runs), five never reached C2 (of 47 misses). No integrated run reached C5 or C6. The 150 standing-provider selections returned **139 chemical_capability_handoff, 5 production_prerequisite_deferred, 6 material_shortage**; all 111 produce-provider selections returned chemical_capability_handoff. These are 261 attempts, not independent runs. Reference provider production had 907 choices, including eight `producing` successes in seven runs; requester production had 56 choices. Reference provider exposure by endpoint group: C6 13/13 offered and chosen, seven OK; C5-no-C6 24/26 offered, 23 chosen, zero OK; C2-no-C5 4/14 offered, three chosen; never-C2 5/7 offered and chosen. Requester exposure was 5/13 offered and four chosen in C6, 10/26 offered and chosen in C5-no-C6, zero elsewhere.

**Interpretation:** integrated genuinely exposes the standing-chest option, but C39 does not demonstrate a working standing chest consumer. An option name or prerequisite handoff is not an owned, retained, output-producing consumer. Neither `ok: false` nor no requester option proves no physical chest crafting: async completion and ownership/retention need separate entity/output evidence. Existing reference temporary production can satisfy downstream demand without a standing option. No new consumer-service treatment was isolated here.

Representative raw evidence (paths relative to each job's `world/evidence/decision-states.jsonl`, zero-based journal `index`):

- **integrated-s80248**, index 66, tick 327804: standing-provider choice hands off to plastic-bar, `progress: true`, `ok: false`; index 68 produce-provider repeats same handoff with no progress. Its final brief shows iron foundation missing provider chests, bootstrap slots 8/8 occupied, chest option repeated 19 times, and plastic waiting for iron/steel retirement. This is evidence of a dependency/allocatable-supply cycle, not proof a chest option is absent.
- **integrated-s80252**, index 169, tick 457794: standing-provider build requires one advanced circuit and one steel chest, both short one, despite network availability of four passive providers and seven requesters. This exposes the chest/advanced-circuit/steel prerequisite chain before successful consumer service.
- **integrated-s80266**, indices 49/56: standing/temporary steel-chest work waits for verified iron and starter retirement; index 68 temporary steel-chest batch is launched. Steel-chest attempts are distinct from logistic-chest success.

## Fault accounting, including typed refusals

| Measure | Reference /60 | Integrated /60 |
|---|---:|---:|
| Caught runner exceptions (`failures`) | 0 | 0 |
| Explicit `tool_error` outcomes | 0 | 0 |
| Safety-classified decision outcomes | 0 | 0 |
| All non-OK outcomes, each with a code | 16,073 | 3,407 |
| Intended-difficulty classifications | 15,389 | 2,974 |
| Unclassified coded outcomes | 597 | 342 |
| Bug-classified outcomes | 87 | 91 |
| `untyped_stuck` events (affected runs) | 37 (21) | 88 (6) |
| `power_route_unavailable` | 35 (21 runs) | 0 |
| `infrastructure_collision` | 1 | 0 |
| Report's selected tool-fault event total | 73 | 88 |
| Maintenance failure events | 5 | 0 |

The report's tool bucket includes only tool_error, untyped_stuck, infrastructure_collision and power_route_unavailable; it is not the full bug or refusal count. Bug totals independently reproduce the job summaries. Reference additional bugs: producer_bootstrap_seed_shortage 8, stage_power_connection_failed 3, ghost_placement_blocked 1, mall_cell_lifecycle_conflict 1, starter_deconstruction_failed 1. Integrated additional bugs: mall_slot_occupied 2 and stage_power_connection_failed 1. No safety-classified decision outcome was observed; that is not an independent proof of every world invariant.

**Integrated untyped faults:** **73 unsupported petroleum-gas requests** in **four runs**: s80252 20, s80261 19, s80277 19, s80296 15. Every message says `No recipe knowledge for 'petroleum-gas'`. Example s80252 index 114/tick 402132 chooses `op.produce.petroleum-gas` for `stock:petroleum-gas`; index 245 repeats near termination. This is an exposed fluid-versus-item capability/operation mismatch, not ordinary scarcity. Correct option facts or route to supported chemical capability; do not repeatedly offer an impossible item-builder action. Remaining **15 full_output health faults**: s80259 four, s80279 four, s80296 seven. Thus six affected runs overlap: s80296 contains both types. Reference's 37 untyped faults are all full_output health results (24 stone-brick, 13 copper); representative s80240 index 296, s80270 13 total untyped events. Full output should be distinguished from construction failure before changing geometry or suppressing errors.

Significant typed examples: integrated-s80252 index 35/tick 283415 reports a copper-foundation power anchor outside a generating network after repair. Reference-s80270 index 318 safely refuses nine real-infrastructure belt blockers; reference-s80290 index 184 refuses changed recipe on bootstrap-loan release; reference-s80294 index 205 times out recovering one stone starter entity. Refusal evidence supports guard operation, not a claim that the corresponding plan is correct. Integrated's 565 steel_seed_site_wait outcomes occur in 14 runs, versus reference 168 in 20; repeated supply/planning waits remain material mechanism evidence even when classified intended difficulty.

**Gate implication:** zero caught exceptions must not be described as zero faults. Integrated increases selected tool-fault events 73→88 and all bug events 87→91, with far fewer decisions (20,995→5,147). Affected untyped runs decrease 21→6, but a new repeated unsupported-fluid mechanism appears. Neither event totals nor run prevalence alone establishes safety improvement.

## Earlier-service regression and idle mechanism

Strict C2 falls **53/60→13/60**; 42 matched seeds lose C2 and two gain it. Strict C3 falls **50/60→7/60**, C4 **39/60→0/60**. These independently defeat the no-earlier-service-regression gate. The final plastic service observation is no_output_in_window in **60/60 integrated**, versus reference 22 no-output, 31 stocked_plateau, seven producing. These are final window statuses, not sustained-service acceptance or whole-run production counters.

Integrated ends stalled 60/60; reference 57 stalled, three window_timeout. **38/60 integrated final option sets contain only wait.300/wait.1800**, versus 3/60 reference. This is an observable option-admission dead end, not sufficient causal attribution to the new idle clock. Example **integrated-s80240**, 37 decisions, ends after waits only: final brief remains C1 with gear/cable output blocked at measured zero despite stock 27/125, three bootstrap slots free, ready iron/copper starters, copper mine missing six drills, and no actionable option offered. Last non-wait is index 25 raising the copper mining stock gate; indices 26–36 wait. Earlier stone foundation index 21 refused collision with a real pole. Investigate missing recovery/shortage options and decision-idle progress semantics together; simply extending the timer is not a demonstrated repair. C39's whole-base divergence and uncontrolled timing do not isolate which merged change caused the loss.

## C5 predicate/version break

Serialized reference C5 versions are **unknown 60/60**; integrated records **c5-v2 60/60**. The report correctly excludes **all 60 pairs** from both C5-ever and C5-final paired comparison. Frozen reference source identifies its actual implementation as **c5-v1**: at least 120 seconds after the first positive craft/provider sample, craft advances at every subsequent observation and provider counts remain present/nondecreasing. Integrated **c5-v2** instead uses an episode-armed production-startup-v2 sampler, exact 1,800-tick intervals, positive adjacent 30-second rates within 20% of each other during the first 120 seconds, retaining the startup achievement despite later pauses. It uses different counters, sampling and retention semantics. Source identification does not repair missing serialized per-run version evidence or make predicates equivalent.

Therefore reference C5-ever 39/60 versus integrated 0/60 and final 7/60 versus 0/60 are descriptive **noncomparable** numbers, not a measured C5 treatment effect. Replaying old sparse journals cannot establish the new sampler contract. C1 is also version-sensitive: integrated source retains C1_V1 as a retired comparable predicate; avoid treating the headline C1 accepted counts (2 vs 34) as a clean earlier-service improvement. C2/C3/C4 supply direct regression evidence without relying on C5. Strict deterministic C6 consumer-output acceptance remains separate from Laya choice/output v2 (reference 36/60 observed, integrated zero), and neither should be substituted for the other.

## Actionable disposition and limits

C39 provides no mechanism basis to promote integrated: earlier services regress, standing-chest exposure has no demonstrated success, and a new unsupported-fluid loop is visible. Parent should make the explicit base choice using this report plus the independent headline audit, then isolate reusable repairs before another consumer experiment. Priorities: restore actionable early dependency/recovery choices; prevent unsupported fluid item-production options; explain steel placement/retirement and allocatable chest dependency cycles; distinguish full_output service pauses from unhealthy construction; standardize predicate evidence before matched C5 claims. Do not add a special-case chest layout or infer a working retained consumer from a selected option.

Remaining uncertainty: no per-entity ownership/retention/output census was reconstructed for all 120 worlds; no causal ablation of merged changes or idle semantics; no terrain generalization from matched policy seeds on the copied world; maintenance failures were counted from terminal summaries rather than individually replayed. No final report other than the cohort's own report was found at initial inspection. Highest evidence: complete frozen-cohort journals and targeted raw trace validation, not production deployment. Lifecycle **nothing**.
