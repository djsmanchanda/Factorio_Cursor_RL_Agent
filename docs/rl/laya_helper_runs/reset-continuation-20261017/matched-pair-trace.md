<!-- Path: docs/rl/laya_helper_runs/reset-continuation-20261017/matched-pair-trace.md | Purpose: Offline causal trace of the reset cohort's two strict-C4/C5 seeds against their matched failures, plus a terminal-cause census. Evidence, not authority. -->
# Reset cohort matched-pair trace (2026-10-17, read at 12:20 IST)

**Cohort:** `reset-heavy-20261017-paired`. Lifecycle arm `a93ee97`, network arm `76d24cb`.

**Source:** raw `logs/laya-runner.log` for each world under `data/laya-experiments/reset-heavy-20261017/cohort/arms/<arm>/code/data/laya-saved-world/reset-heavy-20261017-paired/`. At the time of reading, 27 of 32 runs had finished and 5 were still running. Nothing was replayed live.

**Matched seeds diverge early.** Both arms run on the same save with the same policy seed. Even so, their decision streams differ by decision 6:
- In seed 76604, lifecycle chose `starter copper-plate` where network chose `foundation copper-plate`.
- The divergence comes from live timing in the menu and stock facts, not from the delivery change.

So a pair compares two trajectories. It does not isolate the network factor.

## Seed 76601: lifecycle passes C4/C5, network stalls at the oil-refinery rung

**Network failure (decisions 185–199, +1438s to +1568s).** The chemical ladder alternated between two rungs:

1. The ladder waits at the oil-refinery rung. A loan borrows the copper-cable cell to make one refinery, requesting two crafts, which includes 20 pipe.
2. The refinery's requester draws that pipe, and transferable pipe falls from 101 to 81–89.
3. The pipe rung's target of 100 now looks unmet, so `_missing_chemical_ladder_predecessor` reports pipe. It fires both in the loan-advance check and in the plastic ladder walk.
4. The handoff restores the refinery loan with the message "chemical handoff from oil-refinery to missing pipe".
5. Release recovers the pipe, stock returns to 101, and the cycle starts again.

The refinery was never blocked: the yield diagnostic shows `blocked=[]`. It was restored six times, never crafted, and the stall guard stopped the run.

The same cycle appears in other runs:

| Run | Handoff restores | Role in the run |
|---|---:|---|
| network-s76609 | 10 | terminal |
| network-s76600 | 3 | terminal pattern |
| lifecycle-s76609 | 6 | contributing |

**Lifecycle success.** It met the same pipe draw, but its stock sat well below target. Each reopening therefore produced a real pipe top-up of 34, 18 or 29 crafts instead of a two-step cycle. After the oil intent persisted, the remaining bill raised the pipe target to 167.

**Fix:** `f1469e1` on `laya/reset-20261017`. The rotating-rung count now includes what active ladder-rung loans have requested for their own crafts. Pipe consumed outside the ladder, and real shortages, still reopen the rung. This is unit-tested and the test fails without the fix. It has not been run live.

## Seed 76604: network passes C4/C5, lifecycle deadlocks in C2

**Lifecycle failure (+771s to +1007s).** The C2 iron expansion is caught in a cycle:

- The `modular_iron-plate_refinery` needs one more passive-provider chest ("need 4, short 1").
- The chest recipe needs an advanced circuit, which needs plastic, so the chest gets `chemical_capability_handoff ... plastic-bar before passive-provider-chest`.
- Plastic waits at the steel rung.
- Steel gets `steel_iron_retirement_wait`: the cycle-break needs a submitted iron replacement, which needs the chest.

**How the network run escaped.** It submitted its iron replacement while it still held 5 chests. That allowed `STEEL STARTER CYCLE BREAK` at +940s and iron retirement at +996s.

## Census: logistic chests are the leading terminal cause

Logistic chests (passive-provider and requester) cannot be crafted until advanced circuits flow. The committed save holds 14 passive-provider chests.

Consumers before plastic, each claiming its chest through the material ledger:

| Consumer | Chests |
|---|---|
| Every mall cell | 1 |
| Steel seed | 1 |
| Each plate refinery side tap | 1 |
| Oil packet `chemical_refinery_and_plastic_machines` | its own chests, plus whatever other projects have reserved |

Combined, they exceed 14. Whether a run reaches C4 depends on the order of consumption.

| Terminal cause (27 finished runs + 5 running) | Runs |
|---|---|
| Chest ↔ plastic/advanced-circuit handoff or chest shortage | lifecycle 76601 (at C6), 76604, 76607, 76609; network 76604 (at C6), 76605 (oil packet "need 4, short 2"), 76607, 76612; 76611 (running) |
| Ladder rung cycle (fixed by `f1469e1`) | network 76600, 76601, 76609 |
| Steel seed site or steel conversion material | lifecycle 76600, 76603, 76606, 76608; network 76606 |
| Mall assembler/inserter capacity or no borrowable cell | lifecycle 76602, 76613; network 76602, 76603 |
| Unbuildable collector district or refinery intersecting infrastructure | network 76610, 76614 |

Both C4/C5 successes later stopped on the same resource:
- lifecycle-s76601: the quad inserter cell needed passive providers.
- network-s76604: the `quad_mall_advanced-circuit_top` cell needed a chest, but 2 were available and that chest itself needs an advanced circuit.

## Proposed next mechanism (not implemented)

The plastic bootstrap needs its own non-renewable logistic chests, and they are known before C4. It should own them from the start of the run, through the same ledger path as `STEEL STARTER RESERVE`, which holds 2/2 medium poles before foundations spend them.

1. Size the reserve from the planner's oil-packet bill, not a hand-set number.
2. Expose the fact on the menu: chests remaining and chests owned by each project. Mall-slot and expansion options then show the cost of a non-renewable chest before they are selected.
3. Release the chests when advanced circuits flow.
4. Test it as a single-factor arm against a control that includes `f1469e1`.

This is a contract change (owned remaining bill, previewed before selection), not a weight change.
