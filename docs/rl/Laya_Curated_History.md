<!-- Path: docs/rl/Laya_Curated_History.md | Purpose: Human companion to the curated dashboard history JSON: scope, reading rules, selected milestone rows and provenance limits. -->
# Laya curated cohort history — 7–28 October 2026

Companion to [`laya_cohort_curated_history.json`](laya_cohort_curated_history.json),
schema version 1. The JSON is the dashboard feed; this page explains what was
kept, what was deliberately left out, and where the sources disagree.

This is **curated history, not a registry dump**. It keeps every numbered
cohort C1–C43, the cancelled parents C26/C27 and their replacement result rows,
the no-start/invalid/stopped rows, the three pending cohorts without ranking
them, and a small representative set of earlier named campaigns. Arms are the
frozen factors the cohort actually compared, not every raw job. Numeric cells
appear only where the cited source reports them; an unknown count is `null`, not
zero.

## Coverage and shape

- **Coverage:** 2026-10-07 → 2026-10-28 (IST dates), 22 days.
- **Rows:** 57. That is 45 numbered/retry rows (C1–C43 plus C26b/C27b split from
  their no-start parents), 3 pending rows (C44–C46), and 9 named campaigns or
  trials.
- **Arms:** 150 curated arms, 144 in the strict deterministic family and 6 in the
  proxy family. Proxy counts are never placed in a strict row.
- **Engines:** C1–C6 Factorio 2.1.21 build 87673; C7–C11 2.1.22 build 87719;
  C12 onward 2.1.23 build 87750. These are not one matched series.

| Period | Rows | Content |
|---|---:|---|
| 7–8 Oct | 2 | `ops-history-20261007`, `next-20261008` |
| 15–17 Oct | 5 | epsilon pilot and four fresh batches |
| 18–20 Oct | 11 | C1–C11 legacy deterministic cohorts |
| 21–23 Oct | 12 | C12–C23 (endpoint set changes at C12) |
| 23–26 Oct | 17 | C24–C38 plus C26b/C27b |
| 26–28 Oct | 5 | C39–C43 converged strict cohorts |
| 27–28 Oct | 2 | model-base and GPU-Kev trials (separate lines) |
| 28 Oct | 3 | pending C44–C46, not ranked |

### Status vocabulary

| Status | Meaning |
|---|---|
| `completed` | Terminal accounting closed for the reported denominator. |
| `completed_with_*` | Closed, with the named exception retained (deadline loss, invalid arm, abandoned slots, reruns, prepare failure, unverified terminal logs). |
| `no_start` | Zero jobs launched; not a 0% policy result. |
| `stopped`, `stopped_superseded` | User stop or replacement before terminal accounting; no endpoint claim. |
| `invalid` | Measurement defect; the counts are not policy evidence. |
| `pending` | Running/queued snapshot only; deliberately unranked. |
| `trial_completed` | Separate model/infrastructure trial, not a numbered cohort. |

## Reading rules

1. **`predicate_family` is exclusive per arm.** `strict` means the deterministic
   checkpoint predicate; `proxy` means a phase was reached. A proxy phase can
   occur while strict C2–C5 fail. The only proxy rows here are the six
   `ops-history-20261007` arms. Proxy numbers reported elsewhere (for example
   C9's proxy C2 24/27/23/23/27 or the 16 Oct batches' proxy C2 12/15/17/15)
   are cited in the source but are not written into strict cells.
2. **`ever` is not `final`.** `final` is the last sampled acceptance, not
   sustained service. For C1–C9 the historical `accepted_final` was monotonic
   and latched, so final is `null` unless the report published a separate fresh
   final (C9 C3/C5). C6/C7 finals are `null` for the same reason.
3. **`null` means unknown or not reported, never zero.** Explicit report zeros
   are written as `0`. This matters for C4 (terminal statuses unrecoverable),
   C10's invalid arms, C19–C21 (stopped), C31 (invalid), and C38 (strict C2–C6
   are explicit zeros, but strict C1 is 20/18 ever and 1/0 final).
4. **`planned` is per arm**, except where a caveat states the analysis
   denominator. Campaign-level abandoned slots are recorded in caveats so they
   are not silently dropped: C5's 360 attempt slots, C3's superseded 96, C14's
   attempt 1, C15's attempts 1/b/c, C19's 48 pre-admission cuts.
5. **`predicate_versions` is `null` unless the source states the version.**
   C33/C34/C37 label C5 as `c5-v1`; C35/C36 add `c1-v1` and `c6-v1`; C39–C42
   label C2 as `c2-v1` and C5 per arm; C43 and C41-reference leave C5 unknown
   because no per-record labels were observed. A missing label makes a paired
   C5 comparison invalid, not zero. Laya C6 v2
   (`laya-c6-choice-output-v2`) is a different endpoint and is never written into
   the strict C6 cells.
6. **`comparison_eligible` is about valid comparison, not about winning.**
   A matched-arm design alone does not establish the runtime eligibility flag.
   `true` requires explicit source qualification; `false` records invalid,
   stopped, pending, proxy-only or explicitly ineligible evidence; `null` means
   the source did not establish it. C5/C6 sample runtime manifests explicitly
   report false. Descriptive results remain usable without an adoption grant.
7. **Do not pool rows.** Different seed cohorts, engines, tick caps (C40 used
   216k) and hosts (C4, S30r) are not additive replications. Exact paired
   p-values in the sources are exploratory and not multiplicity-adjusted.

## Selected strict milestone rows

These are the clearest reference/control rows across the 2.1.23 window. They are
a reading aid, **not a trend line and not a lineage**: the baselines, seed
cohorts and exposure differ.

| Cohort | Arm | Date | N/arm | C2 ever | C3 ever | C5 ever | C6 ever/final | Source |
|---|---|---|---:|---:|---:|---:|---:|---|
| C16 | ref `c1b8680` | 22 Oct | 50 | 15 | 6 | 13 | 6/3 | `data/laya-experiments/laya-c16-2123-miniship-20261022/report.md` |
| C18 | credit `e37464d` | 22 Oct | 60 | 51 | 35 | 44 | 10/9 | `data/laya-experiments/laya-c18-2123-miniship-20261022/report.md` |
| C29 | release `5a5ed28` | 24 Oct | 60 | 50 | 38 | 38 | 15/13 | `data/laya-experiments/laya-c29-2123-miniship-20261024/report.md` |
| C33 | control `5a5ed28` | 25 Oct | 60 | 50 | 44 | 35 | 10/8 | `data/laya-experiments/laya-c33-2123-miniship-20261025/report.md` |
| C35 | census `9d9b279` | 25 Oct | 60 | 48 | 42 | 31 | 8/4 | `data/laya-experiments/laya-c35-2123-miniship-20261025/report.md` |
| C36 | control `d9402da` | 26 Oct | 60 | 47 | 31 | 31 | 5/2 | `data/laya-experiments/laya-c36-2123-miniship-20261025/report.md` |
| C39 | reference `9d9b279` | 26 Oct | 60 | 53 | 50 | 39 | 13/8 | `data/laya-experiments/laya-c39-2123-miniship-20261026/report.md` |
| C41 | reference `9d9b279` | 27 Oct | 40 | 32 | 30 | 23 | 8/7 | `data/laya-experiments/laya-c41-2123-miniship-20261027/report.md` |
| C43 | reference `9d9b279` | 28 Oct | 60 | 56 | 55 | 44 | 12/10 | `data/laya-experiments/laya-c43-2123-miniship-20261028/report.md` |

The recurring late-stage story is C5 → C6, not C2: in C43 reference 44 runs
reach C5 but only 12 reach strict C6. C41's reference C5 ever is descriptive
because its version was unobserved, and C43's C5 labels were absent in all 60
pairs. The 216k-tick C40 result (C2 12→32 with no C3/C6 gain) is deliberately
**not** in this table because its window is not comparable.

## Explicit non-result and stopped rows

| Cohort | Planned | Terminal accounting | Status / why it is not a result |
|---|---:|---|---|
| C8 | 1,000 | 0 launched | `no_start`; superseded by C9 |
| C26 | 60 | 0 started | `no_start`; replaced by C26b |
| C27 | 60 | 0 started | `no_start`; replaced by C27b |
| C32 | 120 | 0 started | `no_start`; replaced by C33 |
| C19 | 150 design | 102 registry: 70 completed + 32 not started; 48 design slots cut pre-admission | `stopped`; 35 full pairs only |
| C20 | 150 | 33 completed + 117 stopped | `stopped_superseded` by C22 |
| C21 | 150 | 70 completed + 80 stopped | `stopped_superseded` by C22 |
| C31 | 120 | 85 completed + 35 cancelled queued | `invalid`; every completed run strict C0 only |
| C38 | 120 | 119 completed + 1 `prepare_failed` | `completed_with_prepare_failure`; both arms fail C2 |
| C44 | 200 | 36 completed / 15 running / 149 queued at 14:58 (later 42/16/142) | `pending`, unranked |
| C45 | 120 | 120 queued | `pending`, unranked |
| C46 | 120 | 120 queued | `pending`, unranked |

C31 is the clearest trap: 85 completions with only strict C0 is a broken
measurement, not a 0% policy result. C34 later reran the census comparison after
repair. C38 is the second trap: zero C2/C3 is genuine, but strict C1 was reached
by 20/18 runs (1/0 at final), so it is not a universal zero.

## Named campaigns before C1

- **`ops-history-20261007`** (312/312, six arms × 52) is proxy-only: proxy C1
  2/20/19/20/22/24, proxy C2 **0 in every arm**. Mall-first-30 beat the baseline
  on proxy C1 19:1, but more history alone was 10:6 (p≈0.45). No adoption.
- **`next-20261008`** (320/320, 160 per arm) retained the baseline weights:
  strict C1 25/16 and strict C2 12/9 (C2 `c2-v1`), while the candidate's proxy
  C2 looked better at 48/58. A proxy lead with lower strict counts is not a win.
- **15–17 October batches** used strict C2/C3 and unlabelled versions. None
  produced an adopted winner: 16 Oct morning 9/12/13/14; 16 Oct night 12/17/12/15;
  17 Oct early 18/17/16/15; 17 Oct day 8/7/9/12. All C4 counts were zero. The
  15 Oct epsilon pilot (six epsilons × four seeds) is too small to rank.
- **Model trials** are separate lines. `model-base-20261027` kept guided
  (strict C6 7/5 vs 0/0 for three learned arms), but every original job is
  `comparison_eligible:false`; `gpukev-20261028` ended at 8 completed of 90.
  Eight completions cannot rank weights.

## Provenance limitations and known discrepancies

- **Untracked evidence.** `data/…` and `docs/rl/laya_helper_runs/…` are local
  evidence in the parent checkout, not tracked files in every worktree. Paths
  are repo-relative but may not resolve in a fresh clone.
- **C19 accounting differs between the maintained index and the local registry.**
  The index says 150 planned = 70 completed + 48 cut + 32 stopped; the local
  `batch.json` holds 51 seeds × 2 arms = 102 jobs, of which `job-status.json`
  records 70 completed and 32 not started. The JSON keeps the 75/arm design
  denominator and records both accounts in the caveats.
- **C28 final C6 differs from the index.** The index writes legacy C6 1/1 for
  every arm; the C28 report gives final C6 = 0 for `control_a` (ever 1) and 1 for
  the other three. The JSON follows the report and flags it.
- **C4 is low confidence.** The ledger says 96/96 completed, but the local C4
  raw-root is absent and terminal statuses/timings are unrecoverable. Counts are
  retained as reported; `comparison_eligible:false`.
- **C9 has an interim/final difference.** The C1–C9 audit preserved a 12:19
  snapshot (218 completed, 20 running, 2 queued) with lower interim C2 counts.
  The curated row uses the terminal C9 result (240/240, C2 22/19/16/20/20).
  Do not mix the interim snapshot into cohort rates.
- **C5 denominators overlap.** The campaign held 360 attempt slots (A 24, B 72,
  C 86, D 82, E 96); 193 completed, 8 interrupted, 2 runner errors, 157 never
  started. The row's planned value is the 95/arm matched-pair analysis N, not 360
  independent trials.
- **Version gaps are real.** C1–C9 legacy origins mostly do not publish
  per-endpoint versions in the result text, so versions stay `null`. C35/C36
  explicitly state `c1-v1`/`c5-v1`/`c6-v1`; C33/C34/C37 state `c5-v1`; C39–C42
  state `c2-v1` and per-arm C5; C41-reference and C43 C5 are unknown. Do not
  create a version-qualified C5 pairing from `null`.
- **C30 strata are not pooled.** C30a (40 planned) and Starship S30r (48
  planned) are separate; per-arm endpoints for those strata are not in the
  index, so the JSON keeps only the 120-job release/site comparison and names
  the separate denominators in a caveat.
- **Pending snapshots go stale.** C44's 14:58 and 15:11 reads differ. C45/C46
  are queued. They are present as `pending` so history is complete, but they
  carry no `ever`/`final` values and are not ranked.
- **`completed` is not a mission win.** Most completed runs are stalls or window
  timeouts. `failures=0` counts caught exceptions only; typed refusals and
  untyped faults are separate and are reported in the source findings.

## Maintenance

Update the JSON and this page together. Add a cohort only when its terminal
accounting is closed; keep every planned class in the caveats; keep active queues
in the pending section; and never move a proxy count into a strict cell. The
maintained index remains the retrieval authority:
`docs/rl/Laya_Cohort_Results_Index.md` (C1–C11 rows L112–122, C12–C23 L133–144,
C24–C38 L153–167, C39–C43 L177–181, trials L189–192, pending L201–203).

## Parent metadata checks

The C1–C3 audit records 360,000 elapsed ticks and 4,500 wall seconds;
the C5/C6 audit records the same local budgets. Their feed metadata uses
360,000, correcting the initial general 540,000 default. C4's remote budget
remains unknown. Matched design was not treated as an explicit
`comparison_eligible:true` flag; unestablished true values were changed to
unknown, with source caveats retained. Numeric milestone counts were unchanged.
