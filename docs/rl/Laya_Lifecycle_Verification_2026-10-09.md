<!-- Path: docs/rl/Laya_Lifecycle_Verification_2026-10-09.md | Purpose: Record scoped lifecycle repair, preserved-case verification and its limits. -->

# Laya lifecycle verification — October 9, 2026

The isolated branch `codex/laya-lifecycle-obligations`, based on `370de88`,
repairs missing retirement choices without changing the deterministic builder,
milestone predicates, catalog weights or mods. Main-checkout work is preserved.

Production-rate filtering previously removed all plate operations, including
unfinished retirement, when rates became satisfied or proxy phases changed.
Successful-operation history was also insufficient: a foundation could return
a shortage after submission, then finish through persistent bot construction.

Ops now exports the episode/surface/force/profile-validated district ledger.
Submitted replacements remain reconciliation choices until released, independent
of rate demand. This does not declare them ready. Ownership, funded construction,
replacement output and bot-removal guards remain in the existing tooling.
Local receipts remain a fallback; scoped lifecycle facts take precedence.
Retirement normal returns distinguish `released` from `retirement_pending`;
an incomplete return is not labeled successful retirement. Pending options
serve the mission goal, with no forced selection or new weight rule.

Acceptance sampling now includes both sides of retirement attempts. This is
an evaluation change and adds live survey time; checkpoint definitions remain
unchanged. Ordered observed milestones and independent predicate passes must
still be reported separately.

## Evidence

- **54 focused tests passed:** ops loop/receipts, acceptance exports and district
  persistence/ownership/output guards. Existing startup ordering tests pass.
- **Terminal menu replay:** all needed metal retirement choices recovered in
  **101/101** affected failed trials. Receipts alone recover all needed choices
  in only **36/101**. This recompiles a hypothetical next menu using terminal
  receipts and scoped ledger records; it does not establish historical availability
  at every earlier decision or predict 101 successful reruns.
- **Live mechanics:** two copied terminal worlds, Factorio **2.1.21 build 87673**,
  frozen cohort mods, Nauvis/player, speed 2, private endpoints
  `35460/28460` and `35461/28461`. Both original saves and copied-source ledger
  hashes remain unchanged. Copies retain their original episode IDs, with
  explicit continuation provenance; no ledger scope was rewritten.

| Preserved case | Before | Observed recovery |
| --- | --- | --- |
| `ops-other-slots-1-s50507` | Both foundations ready; both metal starters present; C2 failed | Both starters recovered by bots; both districts released; unchanged `c2-v1` passed. |
| `baseline-s50616` | Both foundations ready; both metal starters present; C2 failed | Copper released. Iron initially retained because its provider lacked output; its option remained pending. A later attempt through the final runner released iron after output appeared, and `c2-v1` passed. |

The final runner recorded four immutable acceptance packets around the second
case's later attempt: start, before retirement, after retirement and end. C2
changed from failed to passed with the iron-starter absence check. Both verified
metal releases disappeared from the next menu; unfinished stone work remained.

These actions were scripted mechanics checks, not policy-selected successes or
full fresh-start runs. Session initialization reapplies normal runtime targets,
and the worlds keep producing during surveys. Recovery demonstrates actionable
coverage and preserved safety; it does not quantify a success-rate or speed gain.
The C1 mall capacity/input/gating problem remains separate.

Local evidence lives under `data/laya-experiments/lifecycle-20261009/`:
`menu-replay.json`, `live-replay.json`, `live-replay.log`,
`pending-receipt-live.json` and `pending-receipt-live.log`. The copied worlds,
manifests and acceptance packets live under its `code/data/laya-saved-world/`.
The original cohort review remains in
`data/laya-experiments/next-20261008/RESULTS_REVIEW.md`.

Both private worlds are stopped. No main server, installed mods, GUI runtime,
policy promotion or campaign default was changed. Python consumers need a
new/restarted runner using this branch; no mod deployment or Factorio restart
is required to adopt this Python-only change. Next evidence: a matched
fresh-save comparison before claiming performance improvement.
