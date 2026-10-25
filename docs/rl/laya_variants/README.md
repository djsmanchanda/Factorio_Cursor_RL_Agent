<!-- Path: docs/rl/laya_variants/README.md | Purpose: How Laya variants and experiments work, and the adoption rule. -->
# Laya variants

A variant is one small, recorded tweak to what Laya reads or how it runs,
tested against the current workflow (the baseline) on the same seeds.
Format and fields: `training/laya_variants.py`. Runner: `tools/laya_experiment.py`.

Declare its action path (`geometry` or `deterministic`) and predicate/window.
Matching policy RNG seeds on one copied save is not unseen-terrain evaluation.
What a variant may change:

- **Docs and rules:** `catalog` (another catalog quoting the docs) or
  `catalog_patch` (per-rule overrides, usually `prefer` weights). Quotes are
  still verified against their source documents.
- **Context sent to Laya:** `settings.guideline_limit`, `per_need_checked`,
  `per_need_shown`, `stock_rate`, `rate_window_ticks`.
- **Policy:** `policy.epsilon` (exploration floor).
- **Runtime:** `runtime.speed`; geometry uses `settings.stall_decisions`.
  Ops has a separate 18,000-tick stall constant; that setting does not tune it.
- **Optional ops mechanics:** `settings.mall_loan_reconciliation` exposes a
  measured ready-loan choice; `settings.decision_wait_schedule` shortens selected
  waits on the elapsed game clock. Defaults are false/off. See the
  [October 23 contracts and proposed contrasts](../Laya_Mall_Utilization_2026-10-23.md).
  `ops-ready-loans` and the user-selected `ops-wait-linear3` isolate those factors;
  `ops-wait-growth5` and `ops-wait-sqrt` retain earlier timing alternatives.
  These files use the standard ops catalog. To compare against another admitted
  baseline, overlay only their named settings onto that baseline's full variant.

Windows:

- **Local:** `--until-phase C0` or `C1` with a tick cap; fast, many seeds.
- **Long:** a later phase and a larger cap; fewer seeds.

Matched cohorts run under `tools/run_laya_batch.py` with a fixed worker pool
and the shared host admission gate described in [the scheduler runbook](../Laya_Host_Scheduler.md).
Arms rotate within successive seeds. Every planned job remains in status and
`report.json`, including interrupted, failed and unstarted jobs. `until` is the
hard cohort deadline: the harness stops admitting a job unless its wall window
fits and caps running jobs at that deadline. A partially admitted seed is not
a complete pair; the report explicitly counts complete pairs. Do not claim
whole-seed completion from job order alone.

Strict ever-passed, strict fresh-final, and `laya-c6-choice-output-v2` are
reported separately, with observed denominators. Missing observations remain
unknown. `failures` is a caught-exception count; typed outcome codes and bug
classifications are separate evidence. Archived independent per-cohort scaling
is under `experimental/old_workflows/per_cohort_adaptive_batch/`.

Run retention: when a host-budget cohort finishes, its controller runs
`tools/laya_run_retention.py` over the registry. The newest 300 runs on the
host stay whole, as do each cohort's best and median runs (strict final
milestones, then proxy phases, then game time to the top phase). Older runs
of terminal cohorts keep only their fine-tuning data (`evidence/`, decision
records, outcomes and manifests), zstd-compressed and hash-verified, in
`data/laya-finetune-corpus/<hostname>/`; the world directory becomes a
`CONDENSED.json` pointer. Cohorts with active or resumable (interrupted) jobs
are never touched, so their checkpoints survive. The `zstd` CLI must be on
`PATH` (the Python 3.11 campaign venv has no `compression.zstd`); retention
preflights it and refuses to copy or delete anything when it is missing. Override
with batch `"retention": {"keep_recent_runs": N}` or `{"enabled": false}`, and
preview with `--dry-run`.

Per-file index rows retain the corpus's byte counts and SHA-256 format. The
source ledger may contain archive summaries from the initial cleanup and
per-run records from retention (`files`, `bytes`, `index`, `condensed_at`).
Refresh mirror-comparison manifests after adding runs; an old snapshot
manifest intentionally rejects new rows. If a run fails verification, the pass
stops before deleting it and the controller logs `retention_error`; repair the
reported data before retrying.

Before comparing, inspect effective option weights: every matching preference
multiplies, including overlapping entries in one rule. Change only the intended
factor, or declare all affected groups. Current other-slots-1 arms also reduce
gear/cable/circuit factors from 180 to 30; they do not isolate other slots.

Default adoption requires more long-window target completions on matched
conditions, no increase in safety/tool failures, and no earlier-service
regression. Preserve every planned job's terminal status; omitted crashes or
interrupted jobs cannot silently improve a denominator. The harness reports all planned statuses and complete pairs; legacy exports may
lack final, C6 or typed-outcome evidence. Check raw outcomes and observed
denominators alongside the report. Changing rate windows
changes acceptance conditions and is not a like-for-like promotion comparison.

An explicitly recorded early-milestone default is **provisional**, not evidence
that the long-window gate passed. Record the limited objective, full cohort,
known missing gates and required recheck. The October 7 mf30 adoption is such
an exception: Laya C1 improved, C2 remained zero. This cleanup preserves its
weights; it grants no additional variant adoption or production promotion.

Record experiment, report, decision and predicate version in `CURRENT_STATUS.md`,
then archive the variant. Generated catalogs (`generated/`) are disposable and
must be regenerated from synchronized sources for new runs.
