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

1. **Rung as world count.** A rotating chemical-ladder rung is satisfied when the world holds its target (physical stock, including reserved, plus placed entities). The latch is not used.
2. **Named prerequisites become options.** An outcome that names the prerequisite it waits on (for example `chemical_capability_handoff` with `rung`) puts `produce <prerequisite>` on the menu whatever the proxy phase. Replay: day control-s76403, where one chest was handed off 125 times and no plastic option was ever offered.
3. **Recipe and production facts from goals and observed demand,** not a fixed list.
4. **Fresh-start validation.** Require newly produced **and delivered** plastic (strict C4), then C5 sustained service, before any policy experiment.
