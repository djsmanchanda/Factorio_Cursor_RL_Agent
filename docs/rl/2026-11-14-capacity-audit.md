<!-- Path: docs/rl/2026-11-14-capacity-audit.md | Purpose: Identify pinned audit sources, preserved evidence, and the limits of the November 14 capacity fixes. -->

# November 14, 2026 audit entry point

Prepared at **19:39 IST**. Start the factory/geometry audit from
`audit/laya-capacity-20261114`. This ref adds audit documentation to source
snapshot `c262f69def23a03ffb61fbbb6d1f0f6f18588fba`; the last implementation
commit is `c1be3824`. Resolve the final ref with
`git rev-parse audit/laya-capacity-20261114` and use that exact commit in each
auditor's separate checkout. Keep these audit refs fixed; put any subsequent
fixes on new branches.

The local checkout is
`/home/miniship/Github_Desktop/Factorio_Cursor_RL_Agent/data/laya-orchestration/capacity-fixes-20261114`.
No audit agents have been launched. The requested audit is read-only: report
findings and proposed changes before changing runtimes, merging branches or
starting successor experiments. Existing disposable campaigns remain separate.

## Source scope

The branches below are separate audit targets; the capacity branch does not
contain the companion tips. No consolidation merge was performed.

| Scope | Fixed audit ref | Captured source commit |
| --- | --- | --- |
| Geometry, capacity, mall demand, retirement and saved-world continuation | `audit/laya-capacity-20261114` | `c262f69def23a03ffb61fbbb6d1f0f6f18588fba`, plus this documentation |
| Shared scheduler and controller follow-through | `audit/laya-controller-20261114` | `06dbbe0bb0ed77102d48d119a88362d4dcce16cf` |
| Controller A PM feed, delivery and replay work | `audit/laya-pm-20261114` | `607c0c4ae3b3c8445645b1d6553ff0a112d7885c` |
| Other Track B cohort lineage, including stone-first | `audit/laya-trackb-20261114` | `afc61b594be4043585f299d023bff2d505185e00` |

Original development branches are `fix/rl-capacity-lifecycle-20261114`,
`fix/rl-controller-followthrough-20261113`,
`fix/rl-pm-feed-contract-20261113` and `trackb/arm-c0c9`, respectively.
Their later movement is outside this snapshot. The refs are local; no push to
`origin` was performed.

For the capacity changes, compare against frozen Track B base
`bea66974c9e67153effdd03f57cdb0ad85eefc63`. Before this audit record, the diff
was **44 files, 2,150 insertions and 72 deletions**. Relevant implementation
commits are `25df1cb8` (construction demand, staged oil, ownership and mining),
`29cbf166` (automatic overdue-request allocation), `aed6615b` (pole-aware belt
routing), `7dc3cbf1` (provider inventory guard), `8648f9b3` (shared electric
steel lines), `b3282fd5` (complete line growth, collection and oil packets),
and `c1be3824` (explicit regression-prefix continuation).

## Saved runtime and evidence

The fresh Starship run `starship-capacity-fresh-20261114` was stopped for this
audit after completing its current decision. Its wrapper exited **0** at
**19:34:14 IST**. The owned runner/server unit is gone; the spectator GUI was
left open. The factory was saved with its clock paused at **tick 322797**,
last decision **19**, **0 runner failures** and **0 pending ghosts**.
Proxy progress was **C0/C1**; the independent strict sidecar reached **C0
only**. The run stopped early and cannot establish later growth or C6 success.

Frozen runtime: `4b4c70d0e77cffc1d269a8d16d2dec209bb82599`, seed 84218,
patience96, Factorio 2.1.24, 2x game speed (120 UPS target), game/RCON
34345/27345. Disposable saved-world authority only; no inherited phases or
additional item grants. Neither comparison nor acceptance eligible.

The Starship experiment is
`/home/djsm/Github_Desktop/Factorio_Cursor_RL_Agent/data/laya-experiments/starship-capacity-fresh-20261114`.
Its `audit-checkpoint-20261114/` contains the native save and lifecycle facts.
Native-save SHA-256:
`e0e83a4a4dfef778cab508250898e29ca84aad75d51bcfc771e553c086cbec2a`.

The Miniship audit packet, relative to this checkout, is
`data/laya-orchestration/capacity-audit-20261114/`. It is local, gitignored
evidence and does not travel with a branch checkout. Its contents are:

- `starship-checkpoint/`: save, facts, status, site plan and persistent ledgers;
  all checkpoint file hashes verified after copying.
- `starship-final/`: final result, freeze, stop request, manifest, initial state,
  logs and complete decision/transaction evidence, including `strict-sidecar.json`.
  The same files are preserved in `starship-run-evidence.tar.gz`.
- `validation/`: focused and fast-gate logs for expansion and prefix resumption.
- `helpers/`: iron-growth receipts and oil-packet diagnosis, kept as evidence.
- `active-miniship/`: captured cohort freeze/spec/job status and unit state.
- `pending-miniship-primary/` and `pending-starship-primary/`: separate,
  unreviewed tracked diffs and base/status metadata; see exclusions below.
- `manifest.json`: source refs and SHA-256 index for this packet, finalized
  after creating the audit commit.

Use the original run directories for any evidence beyond these copies.
Do not infer resumption eligibility from the existence of a native save;
verify the relevant saved-world schema, ownership and hash contracts first.

## Verification and open questions

Expansion focused checks: **151 passed, 2 skipped**. Prefix/adapter checks:
**17 passed**. Final fast gate: **1,110 passed, 6 skipped in 20.23 seconds**.
These results apply to the unchanged implementation; audit documentation
requires no runtime restart or repeated simulation tests.

The prior saved-factory continuation built **90/90 oil belt targets** with
zero failures at that inspection. This demonstrates removal of the oil
infrastructure packet's `no_op` rejection. It does not prove plastic/C4 success.
The four-drill/right-terminal iron repair passed read-only engine validation,
but was not observed placed: a provider reserved for plastic prevented funding.
Steel extension and staged cutover have focused/replay and geometry evidence;
completed live cutover remains unverified. No matched cohort adoption is claimed.

Audit these contracts in particular:

- Shared deterministic planner reuse and legal operation exposure; avoid
  reproducing geometry or adding strategy exceptions in Laya modules.
- Complete mining support when furnaces grow, including recovery of old
  eight-drill/twelve-furnace layouts and a connected final provider.
- Owned steel-line extension, scarce requester/provider use, retirement of
  exact owned entities, observed bot removal and restart-safe cutover.
- Passive providers stay until **fewer than five occupied slots**; unknown
  occupancy retains them. No forced bulk transfer into storage.
- Persistent construction/request demand, precursor funding, request ages,
  incremental oil release and logistics/charging pressure.
- Interior, single-resource drill sites; depletion facts and recovery.
- Oil infrastructure packets retain empty-bill and hard-validator rejection.
- Regression-prefix progress remains distinct from earned-C1 and independently
  measured strict predicates. Neither saved proxy progress nor a wiring smoke
  establishes acceptance.
- Companion scheduler admission, adaptive concurrency, terminal evidence,
  supervisor completion wakes, and the PM prerequisite/delivery chain.

## Active work and exclusions

At **19:37 IST**, Miniship's
`laya-trackb-b-home-stonefirst-2x-20261114.service` remained active, with
7 running and 53 queued jobs in the captured status. The PM replay
`laya-pm-replay-20261114d.service` was also active. The scheduler, dashboard
and cooling-recovery timer remained active. These units were not stopped,
edited or restarted. Their evidence can advance; use the captured versions for
this audit and request later evidence explicitly. A cooling-recovery timer
alone does not establish hourly controller scheduling.

The two primary `codex/deterministic-checkpoint-fleet` checkouts contain
unrelated, uncommitted work, excluded from all pinned source refs:

| Checkout | Base | Tracked changed paths in captured patch |
| --- | --- | --- |
| Miniship primary | `53047ae110d16ae69d12e94c2ffb7add857efe2c` | 35 |
| Starship primary | `9df944310c5bb4e4b2a1a772327427b74f42c40d` | 50 |

Their tracked binary-capable diffs were preserved without changing either
checkout. Untracked files are **listed only**, not archived. Those patches
are unverified and must be audited against their own bases; they are not
part of the tested capacity change. The PM checkout also remains a concurrent
workspace; only its pinned commit is an audit source guarantee.

The requested Antigravity Gemini observer remains stopped following the
linked environment's full-access permission rejection. No replacement
provider or unattended observer was launched. No real-base action, installed
mod deployment or automatic successor run is part of this audit preparation.
