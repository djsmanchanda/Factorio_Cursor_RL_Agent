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
