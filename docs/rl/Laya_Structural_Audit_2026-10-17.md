<!-- Path: docs/rl/Laya_Structural_Audit_2026-10-17.md | Purpose: Evidence-backed structural audit of Laya over deterministic tooling and proposed changes to improve factory progress. -->

# Laya and deterministic tooling: structural audit

Audit date: **2026-10-17, 07:25 IST**. Recommendations, not an adopted architecture change or deployment.

**Expanded inspection:** read the [weekly reset audit](Laya_Weekly_Reset_Audit_2026-10-17.md) before implementing these recommendations. This initial report primarily describes the compact ops lineage and its recent experiment arms. Research/lab operations, richer progress accounting and a durable queue already exist on another branch; the weekly audit evaluates their evidence and limits. The user's clarified fresh-start rule remains in force: offline replay tests do not authorize live checkpoint/resume experiments.

**The main constraint is the interface between choosing work and completing it.** Laya chooses broad commands whose implementations still choose prerequisites, lenders, sites, construction scale and recovery. The runner reconstructs work from return codes and past shortages. This creates repeated local fixes because neither layer has a complete, durable representation of the work actually required to finish the factory.

Preserve the deterministic geometry, validators, bot construction, ownership checks and working incumbent. Replace the opaque command boundary with explicit, measured transactions and a graph of outstanding material demand. Implement one complete path to sustained plastic before attempting a general director rewrite.

## Scope and limits

Reviewed architecture, invariants, runner and strategy contracts, authority inventory, implementation plan, variant adoption rules, deterministic planning and checkpoint fleet contracts; current ops/session/policy/mission code; frozen frontier chemical, extraction and service paths; observation, reservation, oil transaction, restart and checkpoint interfaces; recent experiment manifests, terminal logs and existing Luna reports.

This is a structural audit across those subsystems, not an exhaustive review of every source line, a new full-factory execution, or a security certification. The active checkout is `4764dfe` with pre-existing changes. The latest experiment control is **`7bbb1d5`**, not that checkout. Findings distinguish both.

New Luna reviewers were requested, but the required T3 `delegate_task` and `task_status` tools were absent. [AGENTS.md](../../AGENTS.md) says: **“If either is unavailable, read the evidence directly and report the fallback.”** No substitute native agents were launched. Existing Luna October 16–17 reports were reviewed, and key claims were checked directly against raw logs and decision records.

There was no `logs/latest-context.md` in this checkout. Run-scoped manifests, summaries and terminal evidence supplied the index instead. User-systemd inspection returned `Operation not permitted`; service liveness is unknown. No server, campaign, save, mod, policy or production state was changed.

## What the recent runs establish

Counts below were recomputed from `job-status.json` and checked against terminal runner summaries for **all 272 completed-cohort trials**. Source save SHA-256: `347eba65065014f82261330d84d5ca2fa3e3427cad3ab18dcfdf496dd9c442b0`; Factorio 2.1.21; guided ops; speed 2; eight concurrent workers; fresh copies of the same save with matched policy seeds within each batch. These are not 272 independent maps, or a single fixed-revision cohort.

| Batch | Finished / planned | Proxy C2 / C3 / C4 | Recorded strict C2 / C3 / C4 | Stalled / window timeout | `untyped_stuck` outcomes |
|---|---:|---:|---:|---:|---:|
| October 16 morning | 96/96 | 59 / 50 / 0 | 48 / 15 / 0 | 92 / 4 | 77 |
| October 16 night | 80/80 | 67 / 59 / 0 | 56 / 13 / 0 | 79 / 1 | 171 |
| October 17 early | 96/96 | 79 / 72 / 0 | 66 / 17 / 0 | 94 / 2 | 175 |
| Total, descriptive only | **272/272** | **205 / 181 / 0** | **170 / 45 / 0** | **265 / 7** | **423** |

Summed per-trial wall runtime was **85.407 hours**, spread across concurrent workers; this is neither host elapsed time nor CPU time. All cohorts report zero caught tool exceptions. That does not mean zero defects: typed failures, including 423 `untyped_stuck` outcomes, are recorded separately. Raw logs also contain 12,471 material-shortage outcomes, 6,021 pending-system outcomes, and 7,326 temporary-mall-batch outcomes. These are event counts, not independent failures or a causal allocation of runtime.

The October 17 day batch was **incomplete at its 07:20:13 IST snapshot**: 12/64 completed, eight recorded running, 44 queued; all 12 completed trials stalled and none reached C4. Keep this partial cohort separate. Its frozen window is 4,500 wall seconds / 420,000 game ticks, with a 12:00 IST deadline. Earlier batches used 2,700 seconds / 270,000 ticks. A longer cap is not a matched performance improvement.

In the early cohort, control recorded 18/24 strict C2 and 5/24 strict C3; ladder-latch 17/24 and 3/24; coal-wait 16/24 and 5/24; handoff-park 15/24 and 4/24. No arm completed C4. Coal-wait's intended new outcome fired in **0/24**, and the handoff intervention was exercised in only **6/24**. Those are weak grounds for another large full-run comparison of the same mechanisms.

Only three early-cohort trials opened an oil transaction. One released all pipelines but left crude-source machines unpowered; two stopped short on packet materials. This establishes a narrow frontier worth reproducing, not a working plastic subsystem.

Raw evidence index: [audit counts](laya_helper_runs/structural-audit-20261017/counts.json), [early Luna report](laya_helper_runs/luna-batch-20261017-early/report.md), [day Luna notes](laya_helper_runs/luna-batch-20261017-day/notes.md), and `data/laya-experiments/batch-202610{16-morning,16-night,17-early,17-day}/`.

## Structural findings

### 1. Critical: mission completion does not represent the product goal

`training/laya_mission.py:22` defines rate phases C0/C1/C2/C3/C4/C6/S1. It omits C5 and ends at science-pack production. `training/laya_ops_runner.py:574` returns `mission_complete` when that phase list is exhausted. The session's operation map has no research-selection or lab-service action, and the loop does not require researched mining-productivity-4.

The frontier adds goal-producer choices, but does not complete this mission contract. Faster bootstrap alone cannot close this gap. Introduce a capability/measurement coverage check for every required milestone, consumption, sustain window, lab delivery and final technology. Unsupported goals should be reported before spending a full campaign on them.

### 2. Critical: broad operations hide a second controller

`training/laya_ops.py:108` maps choices to `autonomous_builder` wrappers, including recursive `ensure_produced`. The frontier builder is 13,380 lines; size is a symptom, not the diagnosis. The relevant problem is policy authority inside those wrappers: the chemical ladder, mall borrowing, steel admission and site selection still determine what happens after Laya chooses an item.

Concrete frozen trace: **`control-s76403`** recorded **125** `chemical_capability_handoff` outcomes for a provider chest blocked on plastic while pursuing C2. At decision 378, its complete menu contained only iron foundation, provider-chest production, and two waits. There was no explicit plastic prerequisite or alternative construction plan. The same blocked chest action retained **0.8815 probability**, despite `repeat_fails=3`; `waiting=False`, `bill_known=False`.

This cannot be repaired reliably by asking the chooser to reason harder: the useful prerequisite is absent from its menu. The objective and its prerequisite graph must expose legal next work independent of the currently displayed phase. Preserve mechanical preconditions while extracting strategic ordering from wrappers.

### 3. Critical: the stall detector misses productive internal work

The loop at `training/laya_ops_runner.py:641` tracks machine counts, remembered shortages, selected production counters and phase-gap improvements. It watches crafts only for items present in its shortage map. Chemical ladder batches can be productive without entering that map.

Raw trace **`pipe-300-s76401`**: pipe loan telemetry advanced from **147/300 crafts at +1166s to 278/300 at +1232s**. The run declared stalled at **+1237s**. This is a verified false stagnation classification; it does not prove the run would eventually finish C4.

Each active transaction needs measured progress in its own units: crafts, material delivery, ghosts built, connected consumers, remaining bill and next expected event. Keep the independent wall/game deadline. A busy label is insufficient, but useful batch progress must count. A larger global timeout would conceal this defect rather than establish liveness.

### 4. High: quantities, capability and delivery are conflated

The chemical ladder uses static batch targets and serial predecessor checks (`_ensure_chemical_ladder_predecessor`, frontier builder:8117). The latch at :7988 remembers that a target was once stocked; it is process-local state cleared during run initialization. It does not establish that stock is still owned by the oil transaction, delivered, or sufficient for the selected packet bill.

Oil planning already has a durable immutable `OilTransactionStore`, and `_submit_oil_cell_packets` computes a merged actual bill. Material reservations also already carry project identity, expected rates and ETA fields. These are useful foundations. The missing integration is one authoritative demand and fulfillment graph shared by the menu, producer loans, reservations and executor.

Represent “this transaction needs N more pipes delivered here” separately from “pipe can be manufactured” and “a previous pipe batch finished.” Do not replace a stock threshold with another permanent item-specific boolean. Treat reservations, requester WIP, transferable stock and delivered material as different quantities.

### 5. High: legal recovery choices are missing

`orchestrator/stage_extraction.py:1087` returns a pending-system deferral when both straight and parallel six-drill growth are blocked, explicitly retaining the same district. **`no-latch-s76402`** repeated that same rejection **171 times**, alongside **255** repetitions of a missing assembler for an inserter cell.

The compact steel seed searches beside existing iron and then one direct-starter site; failure becomes `steel_seed_site_wait` (frontier builder:4298–4332). A different weight cannot create a legal candidate that is never generated.

Offer bounded alternatives: another owned-compatible extraction site feeding the existing destination, a valid alternate corridor/anchor, an independently owned new district where legal, or explicit exhaustion. Price the complete route and preserve immutable submitted geometry. “Existing infrastructure is authoritative” protects ownership; it should not make an exhausted expansion template the only strategic choice forever. Template and district-growth changes require updating their design contracts and matched evaluation.

### 6. High: observation grows by incident-specific whitelist edits

`training/laya_saved_world.lua:76` exports a fixed list and excludes fluid-input recipes. `LiveWorld` patches that Lua text with `EXTRA_RECIPES` (`training/laya_clean_runner.py:88`). The morning goal-producer arm added plastic/advanced-circuit counters; oil-parts later added pipe-to-ground, offshore pump and pole recipes. The fixes were necessary, but the observation interface guarantees another discovery cycle when an unlisted dependency is encountered.

Generate a versioned catalog from live prototypes and operation capability declarations, including item/fluid ingredients and required technology. Project only the selected objectives' dependency closure into the decision. Keep research-unavailable, unknown recipe, missing executable builder, no producer, and measured-zero output distinct.

The runner also sums inventories from every network into one synthetic network (`training/laya_ops_runner.py:582`). This loses reachability and ownership information. It is a structural risk; this audit did not establish that multiple disconnected networks caused the cited runs. Keep network identity and transaction access in the normalized facts.

### 7. High: the active ops policy is not learning from these runs

`tools/run_laya_clean.py:73` supports guided or uniform exploration. Its guided policy is a seeded multiplicative feature-weight sampler, not a trained director checkpoint. `GuidedPolicy.distribution` reads guideline IDs and structured features; it does not interpret the brief's prose. Replaying the real `control-s76403` decision with completely changed brief text produced an identical distribution.

Thus logging more decisions, editing prose, or expanding history does not itself improve the running policy. Useful changes must reach its features/catalog, or a compatible learned policy must actually be trained and served. Existing lab/work-graph machinery belongs to separate paths; its presence does not establish ops integration.

Retain exploration, but evaluate it at meaningful choices among viable transaction alternatives. Compare a small learned scorer with the guided prior using the same representation and menu. Train from measured delivered output and controlled alternatives, not copied choices or non-None function returns. Do not spend the next tranche primarily tuning overlapping weights.

### 8. High: receipts and restart memory are incomplete

`training/laya_ops.py:45` now distinguishes prerequisite progress from submission and producing; the old documentation claim that every normal return is successful is stale. Nevertheless, a non-None return still assigns `producing` without attaching transaction-level measured postconditions. Production, delivered output and readiness remain different facts.

`lifecycle_receipts` restores successful historical operations for jumpstart, while demand/failure/wait state is discarded. This is not full reconciliation of world, transaction, lender and reservation state. `training/laya_checkpoint.py` captures a geometry-oriented earned-C1 bundle; it is not a verified ops C2/C3 checkpoint protocol.

Generalize existing durable stores rather than add another parallel ledger. Persist pending intent before side effects; reconcile after restart; make retries idempotent; keep exact selected parameters and plan hashes; close work only on measured postconditions. Reuse `laya_work_graph.py` and `laya_operations.py` where their contracts fit, after establishing an integration path.

### 9. High: evaluation rewards a different trajectory than acceptance

Across the three full cohorts, **268/272** recorded proxy C1 but only **9/272** recorded strict C1. This does not prove that 263 factories never met strict C1: acceptance is sampled at phase events and every ten decisions, and C1 requires simultaneous healthy starters and four working assemblers. Retirement and stock gating can remove that evidence before a later sample.

Use event-time milestone evidence and explicit sustain windows. Preserve achievement history separately from current service health. `rolling_measurement` can use a shorter-than-requested window before enough history exists. The rate phases omit C5 and C6 consumption; strict evaluation already implements richer predicates. Share those predicates for acceptance, while retaining rate diagnostics for choosing work.

The completed batches compare guided arms, not Laya against the actual incumbent controller. Their RNG seeds vary policy choices on one initial map. Add a frozen incumbent comparison, then independent held-out maps/states before claiming replacement or generalization. Conditional median time among successes alone cannot establish improvement.

### 10. Medium: development evidence and provenance are fragmented

The current evidence index still describes October 9 and the clean-runner contract October 8. Some gaps it lists have since been partially fixed. Frontier branches contain experiments absent from `HEAD`; their incorporation into a later experimental control is not automatic adoption under the documented gate.

The active checkout's modified `saves/mod_playground.zip` hashes to **`9763436071d2ad138e2844a81c7bbd6891218f612c9bf69dc5bede6517c6d5bb`**, different from the frozen batch input above. Future comparisons must select an explicit starting contract instead of silently using whichever save is in the checkout.

Some recent deterministic helper reports concern pytest-fixture paths rather than observed factory episodes; `direct-20261015T095027Z/findings.md` explicitly identifies this limitation. `CURRENT_STATUS.md` also contains repeated synthetic `FIX-0001`, `fix.py`, “sustained plastic” entries. These must not become live-performance evidence. `pytest.ini` has no `testpaths`, while frozen code trees live under `data/`; prior status entries document collection collisions. Scope collection and isolate test writes/helper launches before treating broader test output as reliable campaign evidence.

## The changes worth making

### A. Replace recursive item requests with a graph of transaction work — highest priority

For one oil-to-plastic build, persist the actual selected packet plans and bills. Create dependencies for manufacturing and delivery, reference their owning transaction, expose runnable alternatives, and represent blocked edges with measured wake conditions. Let Laya choose work; let mechanics realize the selected plan. Detect cycles and expose alternative construction/bootstrap plans rather than repeatedly handing the same item back to its parent.

Expected benefit: removes whole classes of ladder re-entry, stale shortage, lender churn and repeated packet-shortage fixes. Cost/risk: the deepest interface change; reservation double counting and restart behavior need explicit replay tests. Start with oil and reuse existing ledgers, not a repository-wide rewrite.

### B. Make execution event-driven and resumable

Replace repeated broad `produce` calls with start/reconcile/cancel/measure transitions on a named transaction. Observe craft/delivery/construction events; wake only when a relevant fact changes, work completes, a lease expires, or a fault appears. Keep all legal choices visible; unchanged-effect metadata and policy preferences should discourage futile retries without turning strategy into a safety validator.

Expected benefit: useful batches survive; unchanged failures stop consuming hundreds of decisions; restart no longer reconstructs reality from successful-history heuristics. Keep an absolute experiment deadline and bounded no-progress detection per transaction.

### C. Replace a single chosen site with a bounded set of complete legal plans

Expose site/route/scale alternatives with full material bills, source/destination identity, power/coverage consequences and expected throughput. Choose among those alternatives before committing immutable geometry. Begin with blocked collector expansion and compact steel, the two recurring spatial failure families.

Expected benefit: the controller can recover from occupied growth corridors rather than select the same doomed operation. Cost/risk: more planner search and legitimate layout variation; bound search, validate first, and measure planning cost alongside delivered output.

### D. Move most iteration to earned frontier replays

Capture coherent **ops** bundles containing world, Lua state, transactions, material/district/work ledgers and provenance. Build a small corpus around chest→plastic handoff, productive pipe loan, blocked collector, and stranded crude-source machines. Use it for rapid mechanism tests, then fresh C0 runs for regressions. Existing deterministic fleet checkpoint design is the template; geometry earned-C1 is not sufficient.

Expected benefit: stop spending almost all experimental volume rediscovering early bootstrap failures. Frontier results remain segment evidence. They never substitute for complete fresh-start acceptance or authorize item grants.

### E. Install a real policy improvement loop after the interface works

Add a representation-compatible learned scorer as a challenger to the guided prior. Use transaction outcomes, delivered progress, completion cost and controlled counterfactuals; group replay relatives into the same dataset split. Measure real choice-point volume, policy contribution and held-out completion. Add research/lab operations and final-technology acceptance as explicit capability work.

This is how campaigns become learning rather than repeated stochastic execution of edited rules. A larger model before A–C cannot select missing options or repair misleading effects.

## Recommended next development tranche

1. **Freeze the incumbent and frontier separately.** Preserve input/mod hashes and all arms. Publish one current evidence index. Keep the running day batch intact; its recorded deadline is not evidence that the service has exited.
2. **Deliver the oil vertical slice from A+B.** Required demonstrations: the 125-handoff case exposes its missing prerequisite or an alternate feasible plan; productive pipe crafting cannot trigger stagnation; packet production uses remaining owned bill quantities; restart reconciles the same transaction; target-machine power is verified.
3. **Prove it in frontier replays, then fresh starts.** First use several coherent earned states to expose mechanism failures quickly. Evaluate corrected progress accounting with unchanged scheduling first; then pin that repaired control and evaluate graph-based scheduling as the next factor. Compare each challenger on 24 predeclared paired seeds at identical wall/game limits. Include the actual deterministic incumbent as a separately identified reference. Count every planned job and publish treatment-exposure counts. If an interface migration changes multiple behaviors, separate their experiments rather than attribute a bundled result to one cause.
4. **Add spatial alternatives and the learned scorer as separate subsequent factors.** Avoid mixing site search, mall cap, batching, exploration and model changes into the first result.

Suggested engineering targets, **not promises or adopted promotion rules**: at least **12/24** strict C4 deliveries and **10/24** strict C5 sustained-plastic passes in the first fresh-start structural cohort, with zero invariant violations. Adoption must also satisfy [the existing rule](laya_variants/README.md): more long-window target completions under matched conditions, no increase in safety/tool failures and no earlier-service regression. A target count alone is insufficient. Full replacement still requires C6 consumption, completed mining-productivity-4 and held-out comparison.

Publish strict milestone counts and time-to-event with non-completers retained; useful-work progress at stop; remaining bill; delivered output; unchanged retries; typed defects; lender utilization; branch/plan changes; and safety outcomes. Keep production, delivery and consumption separate.

Defer further cosmetic brief tuning, another series of arbitrary weight/batch-size changes, blanket timeout increases, universal mall-cap increases, and a wholesale geometry rewrite. Any of these can be a later isolated experiment once a measured bottleneck justifies it.

## Verification and lifecycle

- Recomputed four batch snapshots and inspected all **284 available completed trial summaries**, including the 12-trial partial snapshot. The 272-trial completed-cohort result is the primary aggregate.
- Checked raw decision records and terminal logs for `control-s76403`, `no-latch-s76402` and `pipe-300-s76401`; reviewed the early Luna oil-transaction traces and frozen source paths.
- Replayed a real decision distribution with changed brief prose; confirmed no change. Checked mission phase exhaustion and operation coverage locally.
- Ran `.venv/bin/python -m pytest tests/test_laya_ops_runner.py tests/test_laya_guided_policy.py tests/test_checkpoint_milestones.py tests/test_chemical_packets.py tests/test_laya_batch.py -m 'not loopback'`: **46 passed**. These checks exercise the current checkout, not a new frontier deployment or end-to-end success. No full fast gate was claimed.
- No production code, runtime configuration, save, deployment or process lifecycle was changed. This diagnosis is not committed under the repository's diagnosis-only rule. Existing unrecognized changes were preserved.

**Lifecycle required now: none.** Implemented Python changes would require new/restarted isolated runners; Lua changes require deliberate mod deployment and Factorio reload. Queue such actions behind an active campaign. No production promotion is proposed here.
