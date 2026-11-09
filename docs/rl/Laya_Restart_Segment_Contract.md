<!-- Path: docs/rl/Laya_Restart_Segment_Contract.md | Purpose: Define bounded, restart-conditioned checkpoint regression state. -->

# Restart-conditioned checkpoint segments

This is an opt-in serialization primitive, **not an operational queue or an exact
controller continuation**. A matched Ci→Ci+1 experiment starts each arm from the
same copied, isolated checkpoint world and durable ownership bundle, then creates
a **new** guided policy and RNG with the recorded segment seed. It measures a
restart-conditioned segment, not a fresh full-run acceptance. Neither this API
nor the existing runner automatically orchestrates, matches or proves a segment.
Strict checkpoints and Laya's proxy phases must remain separately reported.

## Contract and use

1. At a quiescent checkpoint, observe strict acceptance at least once. Export
   `session.export_restart_state(mod_scope=...)` while the same isolated world
   save and sidecars are stable. Record the checkpoint, save identity/hash,
   code revision, mod-set identity, reference point, goal, seed, evidence and experiment window outside
   the bundle; pair the save and bundle from the same point in time.
2. Copy that world into a separate disposable server. Before constructing a
   session, call `import_restart_state(bundle, target_script_output,
   episode_id=origin_episode_id, mod_scope=...)`. The target's registered
   sidecars must be empty. Then construct `DeterministicSession(target_root,
   origin_episode_id=origin_episode_id)`, immediately call
   `session.restore_restart_acceptance(state)`, and pass that session into the
   runner. Instantiate a new `GuidedPolicy` with the recorded segment seed;
   explicitly choose whether the runner's optional lifecycle `carried` receipts
   are empty or identically supplied to all arms. The bundle does not carry them.
3. Do not call `acceptance()` on the new session before restoring: it would set
   a new baseline, and restoration deliberately refuses to overwrite it. Normal
   session initialization still loads and reconciles its ledgers. There is no
   automatic rollback of the copied world if initialization or a run fails.

The version-1 JSON object has exactly `version`, `scope`, `milestones`,
`baseline`, `history`, `reached`, and `sidecars`. Scope pins the origin episode,
`nauvis`, `player`, `reduced-v1`, and the caller's explicit mod-set identifier;
milestones pin the current default checkpoint IDs and predicate versions.
Snapshots encode every `FactorySnapshot` dataclass field, including nested
evidence and nulls, without pickle or `from_mapping` coercion. History is the
existing most recent 20 strict observations, and `reached` retains monotonic
strict passes; the original baseline remains independent of this rolling window.
Each sidecar role contains filenames mapped to base64 file bytes and SHA-256.
The bundle permits at most 256 files and 16 MiB of sidecar bytes, and a 32 MiB
JSON encoding. Import checks structure, version, scope, milestone versions,
types, filenames, hashes, limits, symlinks and destination emptiness before
writing; it attempts to remove files created by a failed write. It does not
overwrite a prior destination. The episode ID is a bounded ASCII filename token,
not a path. A restored state is bound to the import target and origin identity.

## Ownership registry

Paths are relative to the server's `logs` directory. Missing files are recorded
as empty role entries, not recreated:

- `deterministic-material-reservations/<episode>.json`;
- the same filename with `.steel-seed.json` and `.steel-district.json` suffixes;
- `deterministic-bootstrap-work/<episode>.json`;
- `deterministic-bootstrap-districts/<episode>/` and its hashed JSON records;
- `oil-transactions/<sha256-of-episode-surface-force>.json`, using the actual
  `OilTransactionStore` identity calculation.

The importer validates JSON and checksums, not every role's business schema or
its relationship to the world. Existing ledger loaders and operations perform
their own validations, some only on later use; steel files in particular are
read as ordinary JSON. A missing source file may mean legitimately absent state
or lost ownership: this serializer cannot distinguish those cases or establish
that its registry covers every possible planner state. Stop rather than run
when ownership completeness or save/bundle pairing cannot be established.
`mod_scope` is a caller-supplied equality label, not a measured mod fingerprint;
the bundle is neither signed nor proof of a trustworthy origin. Use a trusted
source and independently verify the save, mod set, checkpoint, code, reference
point, goal, ports and surface/force. Do not import into a real base or a
running/shared server.

## Unsupported continuation state

The serializer does not preserve policy/RNG state, proxy phases, decision/results
history, stall/measurement/consumer state, runner summaries, in-flight operations,
pending commands, or wall-clock timing. It does not save the Factorio world or
guarantee a quiescent/atomic snapshot. A fresh process is required: sessions use
module-global planner state and are not independent concurrent controllers.

Deterministic initialization clears caches and flags, including patch/new-mine
caches, managed intermediate sources, mall provider/capacity/stock-gate limits,
refresh signatures, loan progress/retry state, craft-proof consumption and
startup/transition flags. It restores refinery growth reservations only from
persisted bootstrap districts. Coverage progress, transferable waits and stage
delivery attempts are reset. Other process-local state, including
`_STAGE_DELIVERY_PROVIDERS`, `_BLOCKING_MALL_ITEMS`, drain-watch history,
`_BOOTSTRAP_LOAN_CONFIGURATION_REVISION`, and pending power bridges, is not
serialized or comprehensively reconstructed. Their absence or a fresh-process
reset can alter later behavior; the contract does not claim planner equivalence,
complete ownership reconstruction or safe resumption of an in-flight action.
Unknown, inconsistent, incomplete or unsupported checkpoints must fail closed
at the experiment boundary rather than be treated as valid continuation.

## Mixed-start interface (2026-10-29 implementation)

`tools/laya_mixed_design.py` allocates pairs before arms. The default 120 jobs
are C0=48, C1–C4=10 each, C5–C6=16 each. C1→C3, C2→C4,
C3→C5 and C4→C6 stop at fresh strict targets; C0/C5/C6 retain failure-or-cap
semantics. Proxy ladder completion is `capability_limit`. Full-origin share is
configurable within 30–50%; unavailable origins remain explicit and block a
complete suite. Admissions interleave late and early origins and alternate arms.

The opt-in v2 capture interface in `training/laya_restart_checkpoint.py` requires
a synchronous operation-boundary receipt, source ledger audit, paused clock,
fresh strict evidence and matching server-save tick. It retains the baseline,
bounded history, ownership and registered auxiliary planner sidecars. Restore
installs all registered state before session initialization; disposable reload
checks the independent server, engine, loaded tick, reconstructed state and a
fresh predicate. Fresh RNG and planner caches remain explicit experimental
choices. Lifecycle receipts and proxy phases are carried equally in each pair;
this does not establish exact planner continuation.

Use `python -m tools.laya_checkpoint_bank inspect BANK` to discover candidates
and track histories. `register BANK --bundle BUNDLE` copies an immutable
candidate. `tools/run_laya_clean.py run --actions deterministic --root ROOT
--restart-checkpoint BUNDLE --validate-reload-only --checkpoint-bank BANK
--no-helper` validates and records a live receipt (prepare the fresh disposable
root first). `select BANK --checkpoint Ci --generation GENERATION --track
anchor|representative` changes only a between-cohort pointer; `rollback` appends
a previous validated selection. Generations are archived rather than overwritten.
Only earned C0 lineage may be an anchor. Segment descendants remain provisional.
Live validation registration corroborates the independent process and hashes;
it is local operational evidence, not a cryptographic origin signature.

Changed mods require an explicit `laya-mod-migration-v1` JSON contract binding
source/target mod hashes, engine, description and `sidecars: identity-v1`.
`--mod-migration-contract` permits Factorio's own Lua-storage migration on the
disposable copy, then requires a new per-arm reload receipt. Other Python sidecar
schema migrations fail as incompatible. A declaration alone is not validation.
The bank's anchor selection currently requires an unchanged source-mod receipt;
changed-mod receipts belong to the candidate arm and do not promote the source.

`tools/laya_reference_interface.py` ports this interface into proven reference
9d9b279 without importing current working-tree policy changes. Its frozen receipt
records every common-interface file hash; both A/A arms receive identical inputs.
The port exposes `tools/run_laya_mixed.py`, and the manager receives the pinned
engine runtime through `LAYA_CHECKPOINT_RUNTIME_ROOT`. The original reference
policy, operations, mods and strict predicate source remain intact.

`python -m tools.laya_checkpoint_regression report --root COHORT` reports planned,
started and terminal denominators by origin, arm and generation, post-load strict
reach, true held-at-end predicates, latency, outcome codes and server-seconds.
Dashboard mixed-start tables use the same evidence; missing evidence remains
unknown. Compare full-origin and segment outcomes descriptively per stage and
failure signature; do not pool them into a policy success rate or infer adoption
from A/A. Capture/reload tests and offline reports are not runtime readiness.

Refresh guidance remains provisional: preserve a stable anchor, collect candidate
representative saves from full-origin runs across both arms, and validate/select
between cohorts. Keep previous generations and every cohort's pinned copies.
Refresh after a representative code/mod change or repeated evidence of stale
factories; a few calibrations cannot establish an optimal refresh interval.

Game speed is a frozen cohort field. `enqueue` records `game_speed` in
`regression.json` (default 2.0 = 120 UPS, as the batch cohorts through C48 ran)
and every run receives `--speed`. Roots frozen before 2026-11-05 lack the field
and keep the runner's 1x default on resume: C47 and C49–C57 ran at 60 UPS. Speed
changes decision latency in game time, so it is its own factor like the engine:
never pool or compare arms across speeds. The host gate judges 1x-equivalent UPS
(measured ÷ the world's logged speed), so its floors mean the same share of
target speed for 1x and 2x worlds.

The working-tree interface also exposes an optional `--research-target` when
the frozen reference runner supports the level-aware research objective. It is
off by default, including the planned C47 A/A calibration. When explicitly
enabled for a later experiment, the mixed report groups observed research
progress and earned completion by origin, arm and generation; unavailable or
missing research evidence remains unknown. This wiring has offline tests but
requires a real disposable research smoke before it can support a cohort claim.

Operational evidence as of2026-10-29 22:24 IST: disposable source02 earned strict C6, but only C3–C5 world/sidecar candidates were captured. Their hashes and contexts verify offline; none has a live independent reload receipt. C1 fresh-predicate and C2/C6 ownership-audit capture rejections remain unresolved. The implementation is incomplete and C47 remains blocked; the owned setup schedule is disabled after cleanup. A source milestone pass does not validate a restart generation. The initial40% full-origin allocation and stable-anchor/separately-refreshed-candidate guidance remain design choices awaiting operational calibration.

Operational update, 2026-10-30 05:00 IST: source03 captured C2 and C6, and all five candidates C2–C6 now have independent live reload receipts (separate disposable servers, loaded tick equal to capture tick, fresh predicate passed, ownership reconstructed). Strict C1 was never earned in 853 evaluations of source03 and once in source02: it needs four mall assemblers working at the same instant, while the gated mall keeps most disabled by control behavior (reference cohorts reach strict C1 in 0–4 of 60 runs). The user left the handling to the main agent, so the C1 lane is excluded as an explicit, recorded design change: `allocate_pairs(..., excluded=...)` reallocates its pairs across C2–C6 by their original weights, and `build_jobs`/the mixed scheduler require each excluded origin to be named with a reason, so an origin can never go missing silently. C47 is 200 jobs (100 pairs, 40 at C0): C0 80, C2–C4 20 each, C5 and C6 30 each. Measurement specs must not set `checkpoint_catalog` or `checkpoint_bank`: a restart run with a catalog tries to re-capture already earned stages and safe-stops on the first capture failure.
