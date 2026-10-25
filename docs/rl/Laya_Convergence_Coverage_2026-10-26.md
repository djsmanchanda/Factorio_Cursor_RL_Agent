<!-- Path: docs/rl/Laya_Convergence_Coverage_2026-10-26.md | Purpose: Account for recent branch content, recovered omissions and deliberately separate work. -->
# Convergence coverage audit, 2026-10-26

The first convergence (`8fccde1`) reconciled main and measurement but did not
cover every archived development line. The user's concern exposed real missing
functionality. A squash also removes source commit IDs from main's ancestry;
that alone is not evidence of missing code. This audit checks content and
records the difference.

## Scope and provenance

The audit indexed 397 non-checkpoint commits dated October 10–26 across local
heads, remotes, archive tags and custom `refs/archive/` refs. The October 10–20
review found 230 commits: 17 already in main, 84 measurement-only commits
carried by the squash, and 129 outside both histories (124 distinct patch IDs).
Equivalent implementations and superseded experiments explain much of that
last group. Static presence checks alone do not prove runtime equivalence.

The original 53 main-only / 122 measurement-only counts remain correct for
`23bc988` and `764ed6b` relative to `32f7d2d`. Neither source tree lost a path in
the first convergence. The deterministic fleet's three unique commits were
already equivalent or superseded. The mistake was treating that narrow branch
comparison as sufficient coverage of the wider recent work.

## Original cohort commit histories

Main's one convergence commit is a squash. Individual source commits remain
on the original histories below; they are not ancestors of the squash merely
because their content was integrated. These named archive tags preserve them
on the remote as well as locally.

| History | Preserved ref | Browse original commits |
|---|---|---|
| Cohort results through C37 | `archive/20261026/convergence/source-8e5280e` | [Controller/cohort history](https://github.com/djsmanchanda/Factorio_Cursor_RL_Agent/commits/8e5280ea6a10ed677f2270a138ca776cba26f4b5) |
| Measurement/runtime development | `archive/20261026/convergence/measurement-764ed6b` | [Measurement history](https://github.com/djsmanchanda/Factorio_Cursor_RL_Agent/commits/764ed6b28d210bf04f922e0078184554b845f534) |
| Earlier result-record line | `archive/20261026/convergence/records-74975be` | [Records history](https://github.com/djsmanchanda/Factorio_Cursor_RL_Agent/commits/74975beae97437d1248ad0e7d8a39e0751f6a2b4) |
| Deterministic main before convergence | `archive/20261026/convergence/main-23bc988` | [Deterministic history](https://github.com/djsmanchanda/Factorio_Cursor_RL_Agent/commits/23bc988cdd7749f476adfadeb09ed4d9ab73e3d3) |
| C5-v2 development | `archive/20261026/convergence/c5-v2-ab7e8d3` | [Startup-contract history](https://github.com/djsmanchanda/Factorio_Cursor_RL_Agent/commits/ab7e8d30914c9bd859f5cbcdd7dd7c39f6c76e5e) |
| First published squash | `archive/20261026/convergence/pre-coverage-8fccde1` | [Initial convergence](https://github.com/djsmanchanda/Factorio_Cursor_RL_Agent/commit/8fccde1fc6437e18f1bfc22f5e3471dc5bcd9f72) |

For a local view of the cohort commits:

```bash
git log --oneline --decorate archive/20261026/convergence/source-8e5280e
git log --oneline --decorate archive/20261026/convergence/measurement-764ed6b
```

The original working checkout remains on
`codex/deterministic-checkpoint-fleet` because it contains uncommitted work.
Its branch history is a different view from main or the cohort archive.
The clean main checkout is `data/laya-orchestration/convergence-20261026/publish-code/`.

## Content dispositions

| Work | Source evidence | Disposition |
|---|---|---|
| Deterministic C1-v2, goals/research, whole-bill reservations, demand siting, loan progress, runtime namespaces, campaign supervision | Main `23bc988` and Oct 10 duplicate lines | Preserved in the first convergence. |
| Laya compact runner, adopted C29 behavior, census, option facts, accounting/recovery, retention | Measurement `764ed6b`, names `d9402da` | Preserved; census changes policy facts as well as acceptance observations. |
| Plastic-startup C5-v2 and retained game-clock evidence | `1c03d47`, `515d1a5`, `ab7e8d3` (equivalent `5a8e431`, `cec3008`) | Recovered. Ported to separate runtime interfaces and coherent recovery; main's C1-v2 remains active and C1-v1/C5-v1 remain comparable. |
| Strict C2 reliability, milestone timing, release-refusal evidence | `bedd7cb`, `14e52bc` (equivalent `d8ef2bc`), `18e5b6d` | Recovered. Keeps v3 batch reports and adds nested reliability evidence. Unknown timing and runner-only maintenance failures remain visible. C5 versions are explicit; unknown/mixed versions are excluded from paired C5 comparisons. Timing RMST remains completed-only, with excluded terminal jobs disclosed. |
| Bot teardown storage room and covering-network checks | `219e3bb`, `5b41f87` | Recovered shared tool safety; insufficient/unknown room remains a typed wait before orders. |
| Retained starter providers and resumable exact retirement | `6c3a223` | Ported tool/state behavior; preserve current explicit retirement authority. Automatic retirement scheduling and nonempty-provider chest-pressure heuristics are not imported. |
| Loan-owned cell stock gates | `c91becf` | Recovered. Standing caps leave a borrowed machine to its loan. |
| Power routes through legal gaps | `2cd3796` | Recovered in extracted `construction_coverage` module; shorter legal hops escape the coarse lattice and remain within wire reach. |
| Geometry-refusal retry facts | `120e982`, `08b690a` | Recovered alongside newer material/allocation facts. Idempotent requests do not claim layout change. |
| Zero-exploration evaluation control | `163ae4e` | Recovered. Default stays 0.15; epsilon zero still samples weighted choices with its seed. |
| Fresh observation ticks through report retention | `3cf5f37` | Recovered. Only identical same-tick reports deduplicate; later observations keep their evidence tick. |
| Scheduler growth headroom | `58363db` | Recovered source and CLI validation; installed scheduler remains unchanged. |
| C37 final result | `8e5280e`, recorded 12:20 IST | New evidence after the first convergence; now indexed. 120/120 complete, robot exclusion not adopted. |
| Persistent queue authority / execution slices | `a90a80f` and October 12 line | Explicitly deferred by the reset decision; archived source retained. Not silently substituted for Laya's current decision layer. |
| Old lab construction, seed vertical mall, two-model campaign wrappers, matrix launcher, Factorio 2.1.22 | Archived October 10–20 lines | Replaced by current research operations, quad mall, campaign supervisor, matched batch runner and 2.1.23 runtime. |
| C11 readiness/reconciliation and October 16–17 policy arms; C30 site; C34 outblock; C35 LHI; C36 clearance; C37 robots | Result docs and reset exclusions | Retained as experiments, not default-adopted. Mechanism fixes and unit tests do not establish target improvement. |
| Previous electric-furnace C6 consumer option | `d491d6c` | Explicitly excluded when the Laya C6 definition changed. Legacy deterministic C6 and Laya C6-v2 remain separate endpoints. |
| Persistent live-server campaign tooling | Archived persistent runtime line | Remains separate after recorded ownership/port problems; persistent segments are not fresh-start trials. |

The local audit artifacts are under
`data/laya-orchestration/convergence-20261026/coverage-audit/`. They include
commit metadata, mechanically absent paths and the preservation manifest.
The independent helper reports are local evidence, not adoption authority.

## Uncommitted work is a separate category

The root checkout still contains 31 tracked dirty paths and an uncommitted
Observatory/Laya Director, mall-population training, optimization bench, coach
import and fleet scripts. T3 checkpoint trees preserve their development, but
no ordinary recent commit contains those features. The convergence does not
claim to have integrated or verified them. They remain intact in the root
checkout; no reset, blanket add or overwrite was used. Their hashes are saved
in the local preservation manifest.

## Evidence and lifecycle

Repository regressions verify the restored contracts. They do not establish a
matched cohort gain or deployed/live success. Historical C29–C38 all retain
C5-v1; the repair never rescales their results as C5-v2. New Laya episodes arm
the startup watch before operations; resume and ordinary observation only read
it, so missing legacy watches remain unknown. Live deterministic replays
explicitly return `deterministic_startup_capture_unavailable`; a creator save
watch cannot pass C5-v2 for a new attempt. Their before-operation sampling
protocol remains separate work, not a completed capability.

C38 remains on immutable base `9764fb6` and its recorded candidate diffs, with
all 120 planned jobs retained. Its archive tag is
`archive/20261026/convergence/c38-base-9764fb6`. Main publication does not modify
that frozen source, installed mods, server processes, scheduler or real base.
Future runners need a new committed freeze. New Lua requires separately
scheduled mod deployment/reload outside the already frozen campaign.

Review corrections also preserve reclaim decision state through checkpoints,
reject unexpected promoted-slot occupants before clearing requester settings,
and support custom milestone sets that do not define C5. Storage probes exclude
filtered, deconstructing and target chests and require unambiguous construction
network coverage. The API reference for the filter is
[Factorio LuaEntity storage_filter](https://lua-api.factorio.com/latest/classes/LuaEntity.html#storage_filter).
These are fixture-tested guards, not a claim of live teardown validation.
