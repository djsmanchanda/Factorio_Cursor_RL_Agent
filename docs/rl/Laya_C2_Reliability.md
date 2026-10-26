<!-- Path: docs/rl/Laya_C2_Reliability.md | Purpose: Define the strict C2 reliability objective, reporting denominators and the October 21 validation campaign. -->

# C2 reliability

The October 21 user objective is **more than 80% of fresh runs reaching strict
C2**, with 10–12 hours of testing on Omarchy/Starship as well as Miniship.
This records the October 21 objective; later host limits and lifecycle decisions are indexed in `Laya_Current_Handoff.md`. It does not authorize
real-base action, weaker checkpoint predicates, item grants or edited saves.

Use `orchestrator/checkpoint_milestones.py` predicate `c2-v1`: healthy,
productive iron and copper foundations with at least six machines and delivered
output, and both metal starters absent. Rate-proxy C2 is separate. Report
accepted-ever, fresh final acceptance and unknown evidence independently.

## Causes and candidate changes

The linked Miniship thread is `mcp:94f918da-f279-4342-bc00-c5d125c67e16`.
Its C12-seed/C13-ref A/A comparison disagreed on C2 in 17/33 seed pairs despite
identical code/save. Policy seeds do not control live timing or host load.

At the October 21 12:22 IST snapshot, C12 had 144 completed of 192 planned,
C13 113 of 144; 177 completed jobs had not reached strict C2. Of those, 51
had both foundations ready but one or both starters present. These pooled
counts diagnose missed checks, not the performance of a single policy arm.
Canonical evidence is under Miniship's `data/laya-experiments/` directories
`laya-c12-2123-miniship-20261021` and `laya-c13-2123-miniship-20261021`.

Independent candidate factors are:

1. Reuse released recipe-less mall capacity (`ef38247`, preexisting candidate).
2. Survey the actual complete steel layout, rather than one paired iron site.
3. Keep submitted district cleanup selectable from its scoped ledger when a
   nested action defers; the retirement tool still verifies output and power.
4. Let a fully funded steel seed use verified owned pioneer output before its
   dependent foundation is submitted. This breaks the foundation-chest →
   plastic → steel → foundation-submission cycle without early retirement.

Historical blocker occurrences overlap and are not causal success estimates.
Each new factor needs its adjacent matched frozen comparison; none is adopted
from unit tests or smoke. Existing Miniship campaigns remain untouched.

## Reports

New batches retain the v3 `report.json` envelope and add `strict_c2_reliability`; `report.md` includes the same section with every status,
strict C2/terminal and confirmed/planned counts, final observations, unknowns,
Wilson intervals, failing C2 check counts by job, recent typed blockers by job,
tool-fault events and maintenance failures. Terminal denominators retain
crashes, preparation failures, interruptions and wall caps. Pending/unstarted
jobs remain explicit, rather than being called completed failures. Repeated
blockers are observed associations, not confirmed root causes.

Refresh an old campaign read-only, writing outside its frozen directory:

```bash
PYTHONPATH="$PWD" .venv/bin/python -m tools.laya_cohort_report \
  data/laya-experiments/NAME --refresh-evidence \
  --output-dir data/laya-orchestration/c2-reliability-20261021/reports/NAME
```

Compare adjacent arms on terminal matched seeds, then inspect safety/tool
outcomes and earlier/later service. Never pool engines or hosts into a matched
contrast. A high point estimate in a partial cohort is not adoption evidence.
The normal rule in `laya_variants/README.md` still applies. Exact launch state,
code hashes, ports, source/mod identity and deadlines live in each `freeze.json`.
The local orchestration state and hourly evidence are under
`data/laya-orchestration/c2-reliability-20261021/`.

Reports also expose per-arm C5 predicate-version coverage. C5 pairs require one
known equal version; missing or mixed historical versions are counted as
excluded. The time-to tool labels its RMST completed-only and lists excluded
terminal statuses, so it must be read beside the full job/status denominator.
Journal-confirmed passes survive crashes and missing runner logs, while a fresh
final observation is never invented.
