<!-- Path: docs/rl/Laya_Cleanup_2026-10-25.md | Purpose: Record verified cross-host cleanup, retained training evidence, adoption convergence and future retention. -->
# Laya cleanup and convergence — 2026-10-25

Verified at 2026-10-25 22:35 IST. The user authorized retiring unnecessary branches,
worktrees, threads and data on Starship/Omarchy and Miniship, then requested
fine-tuning data, recent full runs, and each old cohort's best and median.
OpenCode Flash Free/high and Go Flash/max helpers audited both hosts, adoption,
retention, data integrity and the final implementation.

| Measurement | Starship before → after | Miniship before → after |
| --- | --- | --- |
| `data/` directory total | 401.34 GB → 10.41 GB | 1,299.87 GB → 46.02 GB |
| Entire repository now | 16.67 GB | 47.57 GB |
| Registered worktrees | 91 → 1 | 235 → 15 |
| Local branches | 30 → 7 | 11 → 9 |
| Filesystem space used | 622.39 GB → 428.18 GB | 491.13 GB → 161.68 GB |

GB is decimal. Directory totals count shared Btrfs blocks; filesystem use
includes other applications and continuing campaigns. Starship's item count
fell from the user's 2,641,991 to 385,575. Miniship measurements change
while C36 runs. Exact snapshots are in `data/cleanup-20261025/*-final-metrics.json`.

Twenty old GitHub branches were archive-tagged on origin before removal;
remote heads fell from 22 to five canonical branches. Forty-nine settled
Starship T3 threads were archived. Miniship thread metadata was left intact:
this T3 connection cannot manage that server's threads, and its CLI exposes no
thread-archive command. Seven idle Miniship T3 worktrees were removed after
verifying their untracked-file backups.

`Factorio_Cursor_RL_Agent-review-fixes` was a redundant integration checkout
at `f474d58`. Its committed changes were already contained in later integration
history; its two helper reports were backed up and the checkout removed.
Unrecognized dirty files in both root checkouts were preserved. Removed local
worktree tips remain reachable through archive tags; dirty work is rescued
under `data/cleanup-20261025/worktree-dirty/` and backup artifacts.

## Retained evidence and historical limits

Both hosts hold `data/laya-finetune-corpus/{omarchy,miniship}/`: evidence,
decision records, outcomes, manifests and per-file SHA-256 indexes. The corpus
contains 142,663 Starship files and 140,723 Miniship files, with 1,908,453 indexed
decision-record lines before training-time deduplication. Full content checks
on both Miniship copies found zero missing, extra or corrupt files; unchanged
Starship copies had already passed their full hash checks. Counterfactual
replay journals remain in `data/laya-counterfactual/runs.tar.zst` beside labels.

All 91 remaining Miniship cohort archives were independently hash-checked
against the freeze manifest and retired after verifying corpus coverage and
exemplars: 70 cohorts have selections, 21 had no started runs. Corrected
ranking changed C12 to best `ref-s79401` and median `ref-s79400`. An empty legacy
status made one median ambiguous, so both candidates were restored. Canonical
`exemplars.json` files hold the corrected choices; old metadata is retained as
`exemplars.legacy.json`. The final archive deletion released 31,523,833,571 bytes.

These are compact restored worlds: available evidence and non-autosave saves
remain, while replay snapshots, autosaves and runtime FIFOs are unavailable.
Starship-only full worlds and Miniship C32-C34, the old loop and imported S30r
worlds had already been purged before the revised full-run retention request.
Their training records and summaries survive; exact full worlds cannot be
recovered. The corpus README and corrected exemplar ledger state these limits.

## Experiment convergence

| Canonical line | Revision | Meaning |
| --- | --- | --- |
| `laya/release` | `5a5ed28` | C29-adopted decision policy |
| `laya/measurement` | `764ed6b` | Validated census measurement plus verified retention tooling |
| `laya/records` | `74975be` | C24-C34 decision records |

All endpoint-adopted changes through C23 converge into the C29 release.
Starship's C6, loan and menu development tips were patch-identical cherry-picks
already present there, so their worktrees were redundant. The misleading
`f765d30` integration tip included the rejected C30 site experiment and was
never the adopted policy. C24/C25 commits are ancestors but were explicitly
reverted; ancestry alone does not establish adoption.

C23 receipts, C2 robustness/reporting and frontier policy remain preserved
candidates for further review or replication. C35 finished 120/120 with a
successful service exit during final verification; its adoption decision is
pending. C36 was running 18 jobs with 102 queued at the final status read.
Both retain their original code and world data. No experiment was promoted
into the divergent deterministic root or GitHub `main`; matched one-save
cohort evidence grants no real-base promotion.

## Automatic retention and validation

Retention fix `764ed6b` is committed and published on `laya/measurement` on
GitHub and both hosts. New host-budget cohort freezes from that base retain
300 newest host runs plus each cohort's best and lower-middle median. Only
older terminal runs are condensed to training data. Active, reserved and
resumable cohorts are protected. Unexpected legacy world layouts fail closed.

Final acceptance falls back to ever-accepted milestones only when unknown;
an explicit empty final set remains a regression. The implementation validates
run paths and current cohort state, serializes resume publication, and makes
corpus files, indexes, source records and pointers durable before deleting a
world. Retry verifies and reuses complete indexes instead of shrinking them
from surviving files. Missing or corrupt data aborts safely with an error.
The `zstd` CLI dependency and corpus ledger variants are documented.

Validation: 48 focused tests before the extra resume-lock regression;
1,366 final fast-gate cases, including one skip, exit 0; independent review
found no serious remaining issue. The reviewed runtime module hashes were
unchanged after review. A read-only Miniship retention plan selected zero
C35/C36 worlds. Corpus and exemplar restoration were checked on disk; no
retention mutation was run against active campaigns.

Lifecycle: no runner, mod, server or scheduler restart/deployment is required.
Existing frozen harnesses keep their original behavior. Future cohorts must
freeze from the updated measurement base to gain automatic retention.

Recovery: `git branch recovered/<name> <archive-tag>` restores a retired label.
Origin's retired heads use `archive/20261025/origin/*`; local heads and WIP use
host-specific archive tags and recovery bundles. Detailed evidence is under
`docs/cleanup_helper_runs/20261025/`; deletion plans, corrected selections,
helper backups and verification logs are under `data/cleanup-20261025/`.

## Dependency recovery — 2026-10-26 02:24 IST

The Laya controller reported two cleanup regressions: removing C24's reusable
`source.zip` broke C37's first freeze attempt, and removing C32's harness broke
the `time_to.py` and `release_counts.py` wrappers. Terminal cohort status did
not establish that other work had stopped depending on those files.

The controller restored both analysis modules outside C32's retired harness,
under `data/laya-orchestration/takeover-20261021/tools-c32-metrics/`. This
follow-up verified both byte-for-byte against commit `ab7e8d30`. It also restored
C24's 3,957,377-byte source save from C36 with a Btrfs reflink, matching C24's
recorded SHA-256 `347eba65065014f82261330d84d5ca2fa3e3427cad3ab18dcfdf496dd9c442b0`.
Every recorded source, batch, launcher, harness and mod hash checked for C36
and C37 matched: 1,145 hashes per cohort. Their arm revisions also matched.
Evidence: `data/cleanup-20261025/dependency-recovery-20261026.json` on Miniship,
mirrored under the same path on Starship.

Future cohort retirement must check consumers in controller tools and freeze
scripts, preserve shared source saves, and move reused analysis modules out of
retired freezes before removing their paths. This is an additional manual
cleanup requirement; the published per-run retention implementation is
unchanged. The removed mall checkout remains recoverable from its branch; the
audited current tools and freeze scripts do not require that checkout path.
Previously retired full historical worlds remain unavailable.

Lifecycle: nothing to restart or deploy. C36/C37 freezes, scheduler state,
policy adoption and the controller's supervision ownership were unchanged.
