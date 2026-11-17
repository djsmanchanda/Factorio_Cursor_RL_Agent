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
- **Policy:** `policy.epsilon` (exploration floor, 0–1). Zero removes the uniform component but preserves seeded weighted sampling; the default remains 0.15.
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
- **Shared acceptance:** `--actions deterministic --until-accepted C2` with
  a larger tick cap continues through proxy C2 until the unchanged shared
  predicate passes or another bounded stop occurs. Keep these results
  separate from earlier proxy-terminated windows.

The harness supports up to 40 isolated worlds with explicit disjoint game/RCON
port ranges. Capacity is not proof of sustained throughput: check runtime
health before increasing a live fleet. Generated catalogs are atomically
replaced so simultaneous episode startup cannot read a partial file.

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
interrupted jobs cannot silently improve a denominator. The harness reports all planned statuses and complete pairs;
contract-bound reports retain every planned job and keep missing or inconsistent
evidence unknown. Legacy exports may lack final, C6 or typed-outcome evidence, and
legacy counters may include waits and shortages; keep them separate from typed
faults and caught exceptions. Check raw outcomes and observed
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

### Declared contracts

A regression spec may opt in to `experiment_contract` version 1. It declares
`hypothesis`, `factor`, `arms` (one `control` and a `treatment` list covering
all other arms), `action_path` (the scheduler currently dispatches only `deterministic`),
`eligible_start`, `endpoint`, `predicate_version`, `window` (`game_ticks` and
`wall_seconds` matching the spec budgets), `sampling_unit`, `sampling_rule`,
`missing_outcome_rule` (`retain_all_planned_jobs`), and `adoption_rule`.
The scheduler binds canonical declaration bytes to the ordered plan and frozen
arm source identities before ready, and checks the binding before reservation.
The artifact is published exclusively after flushing its complete bytes. Reports
group jobs by the bound plan, preserving missing, conflicting and duplicate
status evidence as unknown. A damaged plan projection cannot change this
denominator; an unreadable binding makes the denominator unavailable explicitly.
A valid declaration does not establish effective factors, exposure, efficacy or
adoption eligibility. Older roots remain descriptive with eligibility unknown.
An optional contract `assignment` declaration uses `{"method":
"sha256-rotations-v1", "seed": 123}` (nonnegative integer seed; booleans are
invalid). Without it, existing ordering remains unchanged. The method groups
ordinary rows by origin, immutable checkpoint digest and seed, and mixed rows by pair ID; every
block must contain exactly one row per arm with matching remaining source,
seed and target identity. It sorts blocks and arms by SHA-256 of compact UTF-8
JSON arrays `[seed, "block", canonical_block_key]` and `[seed, "arm", arm]`,
with lexical tie breaks. The canonical block key is the compact JSON string
`[origin, bundle_sha256, seed]` (null digest for a full origin) or `[pair_id]`.
A checkpoint directory may relocate during freezing without changing its digest.
The sorted arm sequence rotates left by the zero-based
block index modulo arm count. Thus each arm occurs in each position equally
across every complete cycle of N blocks; across a remainder, position counts
for any two arms differ by at most one. This is a global guarantee, not a
per-origin guarantee. The declared method and seed and resulting exact plan
are bound before ready and the assignment is recomputed before admission.
This scheduling assignment does not establish exposure, effective factors,
inference eligibility, efficacy or adoption. Effective-factor validation and
runtime exposure telemetry remain follow-up work; RC11 is incomplete.

### Manipulation check

A treatment arm may declare `manipulation_check` in its `arms` entry: the
exposure that must be observed for the treatment to count. Forms, all measured
per job from `evidence/decision-states.jsonl` (`tools/laya_manipulation_check.py`):
`offered`, `chosen` or `succeeded` (`outcome.ok`) with one `option_id` or
`family`; `rule_applied` with `rule` (in `choice.rules`); `network_stock` with
`item` (largest provider network stock in any decision record). A job shows the
exposure when its measure reaches `at_least` (default 1). After the arm's first
`after_jobs` (K, default 6) finished jobs that have decision records, fewer than
`min_jobs` (default 1) showing it fails the check: the arm's queued jobs become
`cancelled` with `manipulation_check_failed`, running jobs are untouched, and
the verdict is kept in the status `manipulation_checks` and the report's
`manipulation_check`. Freeze refuses an unknown form, K above the arm's planned
jobs, and a family, option family or rule absent from the frozen arm. It cannot
see a default-off switch, so declare an exposure the control cannot produce.
Arms without the field, and cohorts with none, behave as before. A passed check
shows exposure only, not efficacy or adoption eligibility.
