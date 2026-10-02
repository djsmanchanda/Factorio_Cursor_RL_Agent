<!-- Path: docs/rl/Laya_Mall_Supply.md | Purpose: Record the confirmed separate mall economy and the verified supply-accounting seam. -->

# Laya mall earned supply

The user resumed work on October 3 and confirmed the earned-upgrade alternative:

- Start iron and copper at **1 plate/s each**, with **no rate cap**.
- Each upgrade **consumes** 2 electric mining drills, 10 transport belts,
  4 inserters and 1 splitter.
- Add **1.5 plates/s** to the supply currently prioritized by the queue.
- Do not simultaneously apply the earlier time-based linear ramp.
- The distinct starter chest contains 10 requester chests, 20 passive-provider
  chests, six AM1 assemblers and 12 inserters. Declare power/bot scaffolding as
  protected fixtures; it supplies no extra construction materials.

`training/laya_mall_supply.py` implements the accounting contract. Snapshot
engine production counters after seeding the episode, then count only subsequent
crafting. Newly crafted batch credit and actual provider/storage availability
are both required; player inventory, transfers and requester/machine WIP cannot
substitute. Planning an upgrade grants nothing. Committing it requires a fresh,
complete physical-removal receipt bound to that exact plan and ledger state.
Partial crafting credit remains available after consumption; stale plans and
replayed receipts are rejected.

The caller supplies the current plate priority queue. This seam neither orders
factory builds nor derives a production queue. Resolving product demands into
plate shortfalls belongs in the separate mall observation/queue integration.

Supply accrues in integer 120ths of a plate from exact engine ticks. Fractional
and blocked delivery credit is retained, and delivered fixture inputs have their
own counters. They must never be presented as factory crafting or self-sustain
production. Rates are stored in half-plates/s, so the additive 1.5 increment
needs no floating-point clock arithmetic.

Three focused tests verify seeded/transfer exclusion, provider availability,
complete consumption, queue selection, partial remainders, duplicate rejection,
segmentation-independent supply, and blocked delivery conservation.

This is a unit-tested accounting seam. **The separate live mall fixture, item
removal/insertion integration, demand schedule, model observations/actions and
joint-service rewards have not been implemented or live-verified.** It creates
no server, supplies no live items and grants no model authority. It must receive
its own versioned input profile before trained policy execution; saved-world or
lab checkpoints cannot be relabelled to support it. The supplied-input mall
remains separate from exact-save gameplay and full self-sustain/promotion evidence.

Lifecycle: no action for this standalone module. Its future Python consumer must
start with the new code; no deployed mod or production runtime changed.
