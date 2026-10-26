<!-- Path: docs/rl/Laya_Branch_Convergence_2026-10-26.md | Purpose: Record source reconciliation, policy decisions and the next controlled comparison. -->
# Branch convergence, 2026-10-26 IST

The user requested one clean integration on main, an evidence-based README, and ownership of the next cohort after C36, then explicitly asked to replace the squash with a proper merge. Source integration and policy promotion are separate decisions.

Main's first parent remains `23bc988`. The integration is a real merge of measurement `764ed6b`, controller/results `8e5280e` (including records `74975be`), deterministic fleet `25ee8e1`, and the five recovered source histories listed in the coverage audit. The reviewed reconciled tree is the merge resolution: original commits remain ancestors, while superseded implementations and unadopted policies remain excluded from the runtime. The original source comparison had 53 main-only and 122 measurement-only commits relative to merge base `32f7d2d`. There is one new integration commit on main's first-parent line, with the original histories retained beside it.

The deterministic fleet's three unique commits need no additional port: `fe620bc` equals `e18d8d0` and is superseded by Factorio 2.1.23; `500b17f` supervision guidance is already present; `25ee8e1` fast scaffolding behavior equals `46b3878`. The unrelated dirty root remains on its original branch. Active C37 code, mods, source save and unit remain immutable. Recovery tags under `archive/20261026/convergence/` retain every original integration head. After verification, `laya/measurement` and `laya/records` follow the canonical main commit; `laya/release` continues to identify the historical C29 policy, and bound controller/experiment branches retain their frozen provenance.

The shared operation session owns geometry/execution and preserves main's research goals, scoped retirement obligations, funded-loan facts and runtime namespaces. The Laya decision loop adds measurement-line bills, fluid observations, Laya choice/output C6, coherent recovery, complete job accounting and verified retention. Shared checkpoint evidence lives in `orchestrator/checkpoint_evidence.py`; the old training import remains a compatibility API. Main's demand-derived caps remain able to fall, while an explicit guarded raise retains its separate floor. The existing plastic packet fence uses the shared dependency budget so main's whole-bill allocator cannot give its chest away when the packet bill expands. Coherent recovery preserves terminal goals and acceptance sample numbering; a resumed run neither changes its objective nor reuses an earlier evidence filename. Runtime configuration retains operator settings while accepting explicit thread limits.

Main retains `c1-v2` and exports historical `c1-v1` on the same samples. C29-C37 used `c1-v1`; direct C1 comparison across this boundary is invalid. The structural furnace census changes C2/C3 observation and policy facts. The follow-up [coverage audit](Laya_Convergence_Coverage_2026-10-26.md) recovered C5-v2 startup telemetry and cohort analysis tools from omitted archived commits. C5-v1 remains separately evaluable and all C29–C38 freezes retain it. New source is verified by repository checks; that does not establish matched runtime non-regression or production promotion.

## Policy decisions

- C29 `5a5ed28` remains the adopted disposable-cohort policy reference; C34 verifies the corrected census as measurement.
- [C35](Laya_C35_Result_2026-10-26.md): 120/120 complete. Long-handed batches relieve their kit shortage, but strict C6 falls 8→6. No adoption.
- [C36](Laya_C36_Result_2026-10-26.md): 120/120 complete. Clearance raises strict C6 5→15, but `untyped_stuck` rises 12→36 and final C5 falls 17→7. The stocked-plateau explanation is a fact to investigate, not a retroactive acceptance change. Clearance remains experimental.
- C30 site fallback and C34 output-backpressure intervention remain outside the default, as do C35 LHI batching, C36 clearance and C37 robots. No exception or real-base promotion is taken.

## Next comparison: C38

C37 tests robot occupancy without clearance. C36 showed that clearance removes the actual pole and then encounters flying robots. The next comparison tests **clearance conditional on corrected robot occupancy**, using two arms on the same converged source:

| Arm | Common source | Experimental addition |
|---|---|---|
| robots | Converged main + flying-robot occupancy exclusion | None |
| clearance_robots | Same | Connected refinery pole clearance |

Only clearance differs between arms. Robot exclusion is a common experimental condition, not an adopted default. The result can establish a conditional clearance effect; it cannot alone establish unconditional combination-versus-C29 improvement or a statistical interaction. The outstanding consumer-cell bill/affordability facts are the next investigation after this conditional comparison.

Plan: 60 fresh matched seeds **80160–80219**, 120 jobs; game/RCON bases **39900/32900**; 540,000 ticks and **7,200 seconds** per trial; shared host admission gate; no dependency on C37. Both experiments may share the host because C38 does not need C37's result to define its conditional question. Separate port ranges and the global admission cap preserve isolation. The fixed deadline remains **2026-10-27 18:00 IST**, with no full trial admitted after 16:00. Every planned job remains in the denominator, including deadline omissions. A full cohort is planned, not guaranteed by a runtime estimate. Scheduler allocation tables are diagnostic; the harness admits against the global gate. No scheduler deployment or deadline extension is part of this work.

Primary: strict C6 ever (`c6-v1`), with strict C3/C2 earlier-service guards. Report final C6, old C5 ever/final with observed plastic stock/production/consumption, C1 versions separately, Laya C6-v2, paired game times and typed failures. An increase in typed faults is still an adoption blocker. Freezing the arms grants no adoption.

The local freeze recipe is `data/laya-orchestration/convergence-20261026/freeze_c38.py`. It records the convergence commit, complete candidate diffs, source and mod hashes, the shared save hash, deadline, all seeds and dependencies. Both runtime mods are built from shared source and frozen with the training mod. Actual queue/unit evidence belongs in the cohort's `freeze.json`, `job-status.json` and local session handoff; check those before any lifecycle action.

Lifecycle: new frozen source is required for future runners. The active C37 and installed host scheduler need no restart. Existing deterministic/GUI processes are not upgraded by this commit; using new Lua there requires separately authorized mod synchronization and runtime restarts.

## Coverage correction

The initial squash did omit committed functionality: C5-v2 (`1c03d47`,
`515d1a5`, `ab7e8d3`) and strict-C2/timing/release-refusal reports (`bedd7cb`,
`14e52bc`). The repair ports those behaviors onto the reconciled namespaces,
C1-v2, recovery and v3 batch report rather than replacing main with an older
branch. C37 result `8e5280e` postdates the original convergence and is now
indexed. The coverage audit distinguishes recovered work, equivalent behavior,
superseded workflows, unadopted candidates and uncommitted root changes.

C38 remains byte-for-byte on its original freeze; it does not inherit the
coverage correction or C5-v2. No running campaign, server or scheduler is
restarted by this repair.
