<!-- Path: docs/rl/Laya_Mall_Slots_Integration_2026-10-04.md | Purpose: Verify restored experiment hooks, record the mall-slot handoff and pin a guarded between-campaign mod deployment. -->

# Mall-slot integration and deployment coordination

Received the Laya-side handoff for **306346a**. The current working runner's
context settings, reservation view, `build_state`, initial phase handling,
outcome records and guided history features match the experiment implementation
in **814be8d** at AST level. Commit 306346a left those hooks out of committed HEAD
while preserving their restored working version. **6b4c81d** commits the verified
combined implementation. **47 focused runner/context/mall-slot/line/site tests
pass**. No reconstruction or replacement of the mall-slot code was needed.

The active 554-trial campaign remains on frozen **814be8d**, its original private
mod copies and unchanged settings. Six of its eight baseline attempts earned C1;
three distinct earned worlds were assigned to its transition groups. Integration
in the shared checkout does not alter those comparisons.

## Recipe-return deployment

The handoff reports 115/115 observed private-copy retargets. That demonstrates
successful switching, not physical arrival of all removed items in the feeding
chest. The new native Lua quantity test covers modern quality records, legacy
maps, partial/full chests, spill fallback and failure reporting. **11 focused
deployment/executor/quantity tests pass**, plus native Lua parsing. Physical
returned-item delivery remains a live verification gap.

`tools/deploy_after_laya_campaign.py` implements the requested between-campaign
deployment. Its request pins committed mod and manager/script hashes; unrelated
dirty mod changes are excluded. It waits for the matrix's terminal evidence and
inactive actor unit, then requires that the shared deterministic server and its
FIFO keeper have no live PID and no process consumes that server's config/mod
directory. Even a reused PID blocks deployment; it never stops such a process.

When eligible it invokes the pinned
`manage_linux_deterministic_server.sh deploy-if-required` with explicit shared
server and GUI mod directories. The manager checks stopped again. Afterwards,
both installed copies are verified against the pinned files. It starts no
server, modifies no save and does not reload the GUI. The watcher expires one
hour after the matrix's hard deadline if its conditions never become true.

Deployment evidence is under
`data/laya-experiments/recipe-return-deployment-20261004/`:
`request.json`, `status.json` and `deployment.log`. Installed mod copies remain
unchanged until that guarded task succeeds. The next server start loads the
deployed Lua; an already-open GUI must reload before joining the new mod set.

## Crude-oil blocker: observed live

An independent, paused disposable copy of the exact campaign baseline was
queried for `crude-oil` inside **[-128,128] × [-128,128]**. The engine returned
**zero wells** at tick 256296. Source SHA-256:
`9763436071d2ad138e2844a81c7bbd6891218f612c9bf69dc5bede6517c6d5bb`.
The survey used explicit Nauvis/player scope and owned ports 34440/27340; its
server was stopped afterwards. Full proof is
`data/laya-experiments/mechanics-matrix-20261004/crude-survey.json`.

An earned oil/plastic family therefore needs a scenario or observation/planning
bounds that include actual crude resources. The fixture-funded plastic tests
remain a separate mechanics domain and do not establish raw-world C4 progress.

## Follow-up evidence backlog

The handoff's reported mall-slot comparisons reached more C1 states and built
individual iron/copper lines, but **neither arm reached C2**. These results are
source-attributed handoff evidence, not newly rerun results of the active matrix.
The next separate frozen block should compare baseline versus mall slots on the
earned C1 worlds over **120+ game minutes**, with gate-size/buildable-request and
retarget-preference screens. It should expose copper strip coverage, each
line-site rejection stage, and stock/WIP/recipe-return balances. Those new arms
are a backlog, not injected into the running comparisons.

Lifecycle: no restart or deployment for the active matrix. Only the guarded
future shared mod deployment is scheduled; future runners load the combined
shared-checkout code on their next start.
