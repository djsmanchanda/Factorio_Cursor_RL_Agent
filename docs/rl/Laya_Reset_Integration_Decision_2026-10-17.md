<!-- Path: docs/rl/Laya_Reset_Integration_Decision_2026-10-17.md | Purpose: The reset integration decision required by the October 17 audit: base, recovered fixes, exclusions, contracts, and the open user decision. -->
# Laya reset integration decision (2026-10-17)

This responds to [the forward handoff](Laya_Audit_Forward_Handoff_2026-10-17.md) and [the weekly audit](Laya_Weekly_Reset_Audit_2026-10-17.md).

## Branch and references

- **Integration branch:** `laya/reset-20261017`, from compact `4764dfe`.
- **Immutable references:** `4764dfe` (compact), `e12e04c` (queue) and `7bbb1d5` (oil frontier).
- **Not merged as tips:** the queue tip and the experiment tips. Fixes are recovered one at a time, with the source commit recorded (`cherry picked from …`) and the source's own regression tests.

## Recovered so far

| Commit | Source | What | Regressions |
|---|---|---|---|
| `92e7345` | `378154e` | A pending coal mine is a typed construction wait. | `tests/test_extraction_separation.py` |
| `80a73de` | `8f338ea` | An unbuildable district has its own terminal code, `district_expansion_unbuildable`. | from the source; its test is appended |
| `bf62ce1` | `63a6359`, adapted | `pending_system_construction` counts as work in progress. The source's `retirement_pending` was dropped because that code does not exist on the compact runner. | from the source |
| `f4f36ba` | `0709237` | `stage_power` checks stranded consumers after the anchor call, whatever its return value. | The source's parametrized test plus the October 17 crude-oil case (control-s76307). Chosen over `7bbb1d5` because it covers strictly more. |
| `d22e60a` | `73b0af6` | Physical fluid port mirroring for pumpjacks, refineries and chemical plants; Factorio 2.1 fluid diagnostics; coal service on oil retries. **Lua and schema change.** | from the source; the executor settings list keeps both `chest_gate` and `mirroring` |
| `ea71269` | new, using `a6a731c` helpers | The stall guard counts an active finite batch whose craft counter rose. Replay: pipe-300-s76401. | A behavioural test: an advancing batch survives and an unchanged one stalls. The advancing case stalls on the previous commit. |

Fast suite on the branch: only the inherited import-firewall failure.

## Excluded, and why

- **Ladder latch (`d4e4310`).** An "ever stocked" rung cannot prove the selected packet's remaining bill is paid; it is replaced by a world-count rung (next work). It is also not shown to help: early batch 0/24 oil transactions.
- **Item whitelists (`a832bef`, `78d8557` EXTRA_RECIPES additions).** To be replaced by facts derived from observed demand and goals. The goal-producer *option* idea survives, generalised to named prerequisites.
- **reservation-release.** Its lead did not replicate (night batch 12 vs 12).
- **coal-wait (`21bf861`).** Superseded by the recovered `378154e`.
- **handoff-park.** Superseded by exposing the prerequisite itself (next work).
- **shortage-weight-8.** A policy weight; deferred until the mechanics are proven.
- **Queue progress chain (`5130b80`, `77e43de`, `537b37f`) and the runner/session parts of `a6a731c`.** They conflict with the compact runner. They are a reference for the fulfillment contract below, not ported wholesale.
- **Research and lab contract (`a3ae7d5` / `c385d21`, `9bf5949`, `5de56f6`).** Needed after C4. Not part of this slice.

## Contracts in force

- Every live evaluation is fresh from committed `saves/mod_playground.zip` (sha256 `347eba65…c442b0`), with mods staged from the frozen code (`--mods-source`).
- Gear and cable keep the own-chest cap of min(10 stacks, 1000 items).
- When finite demand ends, its requests are removed and its slot is released. Requester WIP stays distinct from available stock.
- No live manual repairs, no production promotion, no blanket adoption.
- Milestones are reported with versioned predicates: c1-v1 here, never mixed with c1-v2.

## Open decision for the user

**Queue authority.** The October 12 queue branch records a user-approved contract: continuous production and construction execution, with Laya managing goals and order. The compact runner is one operation per decision. This slice keeps the compact contract. Whether the reset adopts queue authority is a user decision. The audit recommends comparing the two only after their shared mechanics agree.

## Next work on this branch (P0/P1, fresh-start plastic slice)

1. **Done (`c8b6d5a`): rung as world count.** A rotating chemical-ladder rung is satisfied when the world holds its target (physical stock, including reserved, plus placed entities). The latch is not used.
2. **Implemented: named prerequisites become options.** The latest typed handoff per parent puts `produce <prerequisite>` on the menu whatever the proxy phase and outside the four-shortage limit. A newer parent outcome retires the handoff. Quantified prerequisites use a fixed absolute level; unquantified rungs request capability establishment, leaving admission and batch sizing to the toolkit. No policy weights changed. Replay: day control-s76403, where one chest was handed off 125 times and no plastic option was ever offered.
3. **Implemented: recipe and production facts from goals and observed demand.** Queries include mission targets, outstanding shortages/prerequisites, project missing items and machine recipes, then expand enabled item ingredients. Fluid recipes have enabled-capability facts and item counters while remaining outside the solid geometry map. Disabled goals are not offered. The original geometry bootstrap list remains compatible; later goals need no list additions.
4. **Fresh-start validation.** Require newly produced **and delivered** plastic (strict C4), then C5 sustained service, before any policy experiment.

Continuation verification (October 17, 2026, 09:37 IST): 66 focused runner/adapter checks pass, including Lua observation behavior. Fast gate: 963 pass, 1 skip, inherited import-firewall failure (four existing stage imports). These are offline results; no campaign, deployment or live runtime changed. New runners must use this checkout; the observation Lua is sent by the Python adapter, not deployed as a mod.

## Selected bill continuation (October 17, 2026, 09:45 IST)

The next fulfillment slice now connects the persisted opening-oil manifest to decisions and execution:

- The shared observer counts exact installed, pending and unplaced entity/tile placements, deduplicating identical placements. Only installed material reduces the remaining bill. This adapts the observation idea from `0a50565`; no queue scheduler or geometry is imported.
- The oil transaction retains episode/surface/force identity and its immutable intent hash. Transferable stock, foreign pending ghosts, allocatable stock after foreign reservations, finite missing quantities and absolute production levels are separate fields. Its own packet claims are excluded from foreign claims. Requester/buffer WIP does not pay a construction bill.
- Selected missing bill items become production choices without another shortage failure, outside the ordinary four-shortage limit. A current bill replaces only its own historical shortage stages. The parent option carries its current remaining bill and owner, including a known empty material bill while service remains incomplete. Dependency facts propagate the consumer's goal to its prerequisites and bill items; numeric weights remain unchanged.
- Shared affordability and oil packet preflight charge for remaining placements on retries. A material project whose entire plan is installed releases its construction reservation. This does not certify production, delivered plastic or sustained service.
- Per-intent installation high-water marks count construction delivery as progress. Busy states, unreadable surveys and demolition/rebuild cycles do not keep the run alive. Before/after transaction facts and source network identities are retained in decision evidence.

Verification: 388 focused checks pass, including actual Lua query behavior, immutable-plan ownership, unrelated reservations, unknown observations and bounded rebuild cycles. The remaining-bill regression fails against the prior affordability implementation by requesting **10 pipes instead of 2**. Fast gate: 964 pass, 1 skip, the same inherited import-firewall failure. Offline replay of control-s76403 decision 378 reproduces all four recorded prior options and adds the missing plastic option with `goal_need=true`; raw prototype/machine facts were not retained, so this is a history/board replay, not a live legality or execution result. Evidence: [replay](laya_helper_runs/reset-continuation-20261017/control-s76403-decision378-replay.json).

Still required: finite-cell request removal, owned ingredient-WIP recovery and slot release; source-network-aware delivery/allocation (the current surface-wide transferable budget is not proof of logistic reachability); bounded spatial alternatives; and fresh committed-save C4 production **and delivery**, then C5 sustained service. The day campaign remains untouched. Queue authority is unchanged. No policy adoption, deployment or live success is claimed.

## Finite-cell release continuation (October 17, 2026)

Standing finite mall production now receives the same durable tag as a loan to its own recipe. A completed self-loan vacates the assembler; a borrowed cell recovers WIP and then restores its original recipe, requests and gates. Gear/cable anchors are excluded. Goal/capability options explicitly use `finite=false`; geometry outside the owned labelled mall is not claimed.

The new `release_cell` configuration operation clears only its own request section, preserves companion requests, stops the assembler, and conserves the engine's returned ingredients and held-inserter stacks. Real recipe refunds remain in a durable mod-owned recovery record until accepted by the cell provider; a full destination retains both remainder and ownership. Recovery validates machine/requester/provider identity. The empty ownership tag survives successful recovery, interrupted restoration and restart, and clears only after verification/acknowledgement. No new entity, item grant or ground spill is part of cleanup.

Evidence: 298 focused tests pass. The fast gate has 973 pass / 1 skip / the same inherited import-firewall failure. [Luna-medium review](laya_helper_runs/reset-continuation-20261017/luna-medium-release-review.md) found and drove the restart/spill corrections; its second pass found no further confirmed bug and required disposable engine verification of the refund APIs. Lua/schema changes need staging and reload on new disposable instances. No existing live campaign or installed mod changed. The day campaign finished 64/64 jobs and its unit is inactive.

## Delivery continuation (October 17, 2026)

Cell/stage delivery now verifies that its provider and consumer share the same actual force-scoped logistic network after coverage service; nearby distance and surface-wide stock no longer prove reachability. An uncovered or disconnected pair returns no delivery progress. A transfer returning zero is not an acted material remedy. The shared stock-transfer helper funds only accepted destination capacity and rolls back insertion if the source cannot remove those items, avoiding deletion on a full chest. Its destination is force-scoped.

Evidence: 378 related checks pass, including executable Lua conservation cases for full/partial providers and source refusal, plus connected/disconnected/unknown network behavior. Fast gate retains only the inherited import-firewall failure (973 pass / 1 skip / 1 failure). This is network-aware delivery, not a complete network-partitioned construction allocator; the selected-oil funding budget remains surface-wide until that later contract is implemented.

A fresh engine smoke of frozen lifecycle commit `a93ee97` has started on dedicated game/RCON ports 35440/28440 under `laya-reset-engine-smoke-20261017.service`, bounded to 25 minutes. It stages both mods from frozen code and the verified committed save, with no resume. The completed day campaign and installed GUI/server mods are untouched. Broad matched testing follows engine wiring verification; no live success or adoption is claimed at startup.

## Heavy testing started (October 17, 2026, 10:11:50 IST)

The fresh smoke exercised actual release, recipe restoration and acknowledgement on two borrowed cells. Its terminal receipt is `stop=window_timeout decisions=119 failures=0 phases=C0,C1 accepted=C0` after 772 seconds; C2 was the active target, not a completed milestone. Strict C4 remains unverified. A separate provisioned **test surface and force on a separate disposable instance** passed normal-quality recipe refunds, a full provider, requester/held-stack conservation, companion request preservation, persistence across a save/load with refunds pending, and a second save/load after restoration before acknowledgement. This API fixture is excluded from policy results; its test-only reloads are not resumed evaluation runs. Earlier fixture attempts are retained: three setup errors and one incorrect inserter/count expectation. No production world was repaired or provisioned.

The bounded cohort compares `a93ee97` (lifecycle control) with `76d24cb` (network delivery/conservation), sharing all other reset mechanics and numeric policy weights. There are **16 matched policy seeds, 32 planned fresh trials, eight concurrent worlds**, 360,000 game ticks and 75 minutes per trial, with deadline **October 17, 2026, 16:30 IST**. The user unit is `laya-reset-paired-20261017.service`; its 23,000-second outer cap expires at 16:35:10 IST (150-second stop allowance). Ports are 35480–35487 / 28480–28487. Each arm has a clean frozen code copy, separately staged mods and `freeze.json`; every trial uses the committed save hash above without resume or jumpstart. This isolates delivery; it does not estimate the complete reset's effect against an older branch.

Primary endpoint: strict c4-v1 newly produced **and delivered** plastic. Secondary: c5-v1 sustained service, earlier-service regression, all typed/untyped failures, release errors and every planned job's terminal status. c1-v1 retains its known retirement limitation. Same-save RNG matching is not unseen-terrain evaluation. No policy adoption follows startup or smoke results.

Evidence root: `data/laya-experiments/reset-heavy-20261017/`. `batch.json`, `job-status.json`, `cohort-metrics.json` and `cohort-metrics.md` retain all planned jobs; `laya-reset-metrics-20261017.service` refreshes offline metrics every minute until the cohort ends. `report.md` is generated at cohort end. Luna medium reviewed implementation and independently observed the full suite, engine checks and initial cohort; [review evidence](laya_helper_runs/reset-continuation-20261017/luna-medium-heavy-test-review.md).

The full offline run of `76d24cb` finished in 532.8 seconds with **3,840 passing cases, 18 failures and 39 skips** (3,897 JUnit cases; 3,895 collected nodes). Nine failures were the new dependency missing from an atomic-test Lua search path. Six more reproduce on predecessor `c8b6d5a` after catalog-installing tests: the shared recipe catalog leaked between tests. The loader now loads the real module; catalog fixtures restore their mapping, and per-test snapshots cover catalog installs. **146 affected checks pass** after these test-only corrections; fast gate **973 pass / 1 skip / 1 existing import-firewall failure**. Two other incomplete-client tests and the existing four-import firewall violation reproduce on the predecessor in isolation. The original full gate was not clean. A fresh complete-suite confirmation follows the test-harness commit; running cohort code/mods are unchanged.

Test isolation note: the original full-suite tests also appended synthetic reliability-campaign entries to their own checkout's `CURRENT_STATUS.md`. That checkout is preserved as evidence; clean separate cohort worktrees were created before launch. Test execution is not allowed to establish campaign code identity through a dirty mutable checkout.
