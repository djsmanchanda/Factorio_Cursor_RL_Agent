<!-- Path: docs/rl/Laya_Current_Handoff.md | Purpose: Current Laya convergence, cohort evidence and lifecycle index. -->

# Laya current evidence

Updated 2026-10-26 IST during branch convergence. Read the [runner contract](Laya_Clean_Runner.md), [strategy rules](Laya_Strategy_Guidelines.md), [variant adoption rules](laya_variants/README.md), and [system invariants](../system_invariants.md).

The decision-policy release is `5a5ed28`, adopted in [C29](Laya_C29_Result_2026-10-24.md). The fixed foundation census (`9d9b279`) passed all 4,323 acceptance checks in [C34](Laya_C34_Result_2026-10-25.md). It changes both acceptance observation and policy facts, so pre/post-census counts are not directly comparable. Measurement, crash-safe run retention and named obstruction evidence are being integrated with main's independent runtime isolation and research-goal work. Integration tests do not establish a matched live improvement.

| Cohort | Result and decision |
|---|---|
| C29 | Stale mall-reservation release adopted; [result](Laya_C29_Result_2026-10-24.md). |
| C30 / C30a / S30r | Steel-site candidate not adopted; [result](Laya_C30_Result_2026-10-25.md). |
| C31 / C32 | Census survey defect: C31 85 completed + 35 cancelled; C32 120 cancelled before admission. Strict endpoints invalid. |
| C33 | Pole clearance removed its named obstruction but regressed strict C3; not adopted. [Result](Laya_C33_Result_2026-10-25.md). |
| C34 | Census verified, output-backpressure candidate not adopted. [Result](Laya_C34_Result_2026-10-25.md). |
| C35 | 120/120 complete; not adopted (strict C6 8→6). [Result](Laya_C35_Result_2026-10-26.md). |
| C36 | 120/120 complete; C6 5→15, but faults/final C5 guard prevent default adoption. [Result](Laya_C36_Result_2026-10-26.md). |
| C37 | 120/120 complete; robot-only exclusion not adopted (strict C6 11→7). [Result](Laya_C37_Result_2026-10-26.md). |

Every planned job counts. Strict ever/final, proxy phases and Laya circuit-choice/output acceptance are separate. C29–C38 use Factorio 2.1.23 with c5-v1. Main restores the requested [C5-v2 startup contract](Laya_C5_Startup_Contract_2026-10-25.md) for future committed freezes; historical runs are never rescored. Main's c1-v2 and historical c1-v1 must be reported separately after convergence.

C36 clearance and C37 robot exclusion are separate interventions; their combination has no matched validation. No prepared C2 migration is authorized here. No real-base policy promotion is established.

Cohort roots and scheduler registry: `data/laya-experiments/`. The shared deadline is **2026-10-27 18:00 IST**; a 7,200-second trial must fit before it. New freezes require hash-recorded committed code, the integrated retention harness, distinct ports and explicit dependencies. C37 used game/RCON bases 39800/32800; C38 owns 39900/32900. Check current units and job status before allocating another pair.

The canonical shared source save is hash-verified at SHA-256 `347eba65065014f82261330d84d5ca2fa3e3427cad3ab18dcfdf496dd9c442b0`. Preserve it independently of historical cohort retention. Versioned analysis tools are `tools/laya_cohort_report.py`, `tools/laya_cohort_time_to.py` and `tools/laya_release_counts.py`; local historical scripts remain preserved.

The [cohort ledger](Laya_Cohort_Ledger_2026-10-21.md) and result documents carry denominators and predicate details. Historical indexes are preserved for provenance: [main](../archive/rl/Laya_Current_Handoff_Main_2026-10-26.md), [controller](../archive/rl/Laya_Current_Handoff_Controller_2026-10-26.md). Dated evidence does not grant current lifecycle authority.

C38's queued comparison is converged source + common experimental robots, varying only clearance (60 pairs, fresh seeds80160–80219, ports39900/32900). It was queued at 05:17 IST on 2026-10-26 from immutable base `9764fb6`, retained by tag `archive/20261026/convergence/c38-base-9764fb6`. Later main corrections do not change this freeze. See the [convergence record](Laya_Branch_Convergence_2026-10-26.md) and check its actual freeze/job status before lifecycle actions. Clearance and robot exclusion remain experimental.

## 2026-10-26 15:56 IST: C38 closed; next factor selected

[C38 is terminal](Laya_C38_Result_2026-10-26.md): 119 completed plus one
preparation failure; 117 early choice-count stops, no C2 and no clearance
exposure. Repair `c71d826` uses `decision-idle-v3` with no ops choice-count
cutoff; 116 focused tests and the fast gate pass. Historical freezes stay fixed.

The [executive decision](Laya_C37_C38_Decision_2026-10-26.md) prioritizes consumer
ownership/service for the next policy comparison and defers clearance
confirmation. The two-job `laya-c39-preflight-2123-miniship-20261026` baseline
smoke is running from `c71d826`, with a 35-minute unit limit. No full C39 cohort
is queued. Read `data/laya-orchestration/c39-20261026/state.json`; require C2 and
then a tested, exposed consumer operation before admitting 120 jobs. If the
base fails, investigate allocatable-stock facts first. C29 remains the policy
reference; source integration and sampler-fixture success are not adoption.

## 2026-10-26 17:30 IST: C39 base smoke fixed and passed on branch

- **Smoke 1 failed its gate.** `laya-c39-preflight-2123-miniship-20261026` ran from `c71d826`. Neither run reached strict C2; both idle-stalled under `decision-idle-v3`, with zero tool or safety faults.
  - Cause: `_prepare_operation` refreshed the deterministic prospective LOGISTIC CHEST BUDGET before every Laya operation.
  - That held all 7 kit passive-provider chests from +3 s for the stage-order claimants.
  - Foundations and early belt/drill/inserter cells are not claimants, so they refused in a loop (`passive-provider-chest: need 6, short -1`).
- **Fix:** `8eb4690`, branch `fix/rl-laya-chest-hold-20261026`, based on `9224ce4`.
  - Laya refreshes explicit fences only (the plastic output packet). The deterministic runtime keeps its hold.
  - A regression test fails on the old behaviour. 281 focused and 1,626 fast tests pass.
- **Smoke 2 passed.** `laya-c39-smoke2-2123-miniship-20261026` ran from `8eb4690` on the same seeds and ports 40100/33100.
  - Both runs reached strict C2: 27.3 and 25.9 game minutes from the first decision.
  - Zero tool, safety or untyped faults; no prospective budget line; no negative-shortage loop.
- **Not yet on main:** merging or pushing `8eb4690` needs the user's go-ahead.
- **Next:** the consumer-service candidate (shared facts plus one operation) needs implementation, regressions and an exposure smoke before any 120-job freeze. State: `data/laya-orchestration/c39-20261026/state.json`.
