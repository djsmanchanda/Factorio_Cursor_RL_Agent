<!-- Path: docs/archive/handoffs/34_run_observation_2026-09-05.md -->
<!-- Purpose: Record the 00:33 deterministic run, its live milestones, blockers, and resulting fixes. -->

# Live run observation — 00:33 fresh session (2026-09-05)

Target: `research mining-productivity-4`, profile `reduced-v1`.
Runner PID 2193280 (`tools/autonomous_run.py`, `--max-iterations 100`).
Method: poll every ~2 min — log delta (game-time `+Ts`), RCON `game.tick`
(effective UPS), runner CPU, save mtimes. Game runs at 60 UPS unless noted.

## R0 baseline — wall 00:56:13, game +1386s, tick 320215, UPS 60.0
- Copper refinery under construction: `[modular refinery for copper-plate
  #1/10] OPEN: ghost inserter ... needs 1 inserter, but its network has 0`,
  remedy `materials:inserter:1`; inserter exists in force stock (15) but 0
  transferable — waiting on provider/stock conversion, not production.
- Iron foundation done (starter swapped +968s). Runner CPU 1.2% (RCON-bound,
  not compute-bound). Autosaves flowing (00:47).

## R1 — wall 00:56:13 → 01:00:17 (244s wall, +245s game, 3024 → 3095 lines)
- Pace 1:1 (tick 320215 → 333624 = 55 eff. UPS; dip = autosave + RCON).
  Copper refinery resolved, starter retired +1546s, stone foundation +1574s.
- **Splitter trim verified live**: `POST-METAL RESERVE READY: splitter
  reached reserve (12)` at +1573s (22:33 run: 50-stack held +1538→+1950s).
  Circuits still take the full 200.
- Parallel loans healthy: splitter + transport-belt + inserter batches
  overlapped on separate cells (+1419→+1536s), no handoff serialization.
- **Wall-clock stall**: `SURVEY new_mine_site elapsed=44.96s` (stone) —
  one RCON survey blocks the whole control loop ~45s while the game runs
  blind. Copper's was 26.84s. Same magnitude as prior runs: systematic.

## R2 — wall 01:00:17 → 01:02:34 (137s wall, +137s game, 3095 → 3133 lines)
- Full speed (tick 333624 → 341816 = 59.8 UPS). Stone mine earmarked +1764s
  (short drills=5, ghosts placed anyway); power anchor bridged in 2s.
- Watch item: drill loan borrowed a **copper-cable cell** (+1752s). If a
  circuit/inserter loan starts waiting on cable, that's the feedstock
  cascade strangling downstream — flag for the borrow-guard fix.

## R3 — wall 01:02:34 → 01:04:44 (130s wall, +122s game, 3133 → 3148 lines)
- Quiet controller (15 lines): stone refinery earmarked +1770s (short
  inserter=12), roboport chained, power bridged +1776s, 161 ghosts built
  down to 4 and RESOLVED +1850s. Bots work while the loop waits — healthy.
- No measured stone output yet (+1890s); pioneer kept. Nothing stalled.

## R4 — wall 01:04:44 → 01:07:06 (142s wall, +147s game, 3148 → 3202 lines)
- Full speed (60 UPS). Core promotions: AM2 independent +1982s,
  fast-inserter +2002s, chemical ladder at pipe rung +2024s, pipe loan
  +2037s on a borrowed **electronic-circuit cell**.
- **~1300s ahead of the 22:33 run** at the same milestone (pipe loan was
  +3393s there): splitter trim (~400s + its belt ladder), parallel loans,
  and less restore churn compound across every phase.
- Same fragility as R2: the pipe loan sits on the single standing circuit
  cell. Any circuit waiter downstream (AM2/drills) now starves until the
  100-pipe batch restores. No waiter yet — watch.

## R5 — wall 01:07:06 → 01:09:32 (146s wall, +147s game, 3202 → 3267 lines)
- Full speed (60 UPS). **Steel conversion started +2162s** (22:33 run:
  +3573s — ~1400s earlier), iron expansion 6→12 drills earmarked.
- New pattern: `LAGGING BUILD: inserter holds 1/2 transferable; 15 locked
  in requester/buffer WIP` — the mall made the stock but loan requesters
  hoard it while the bill waits on crumbs. Resolves as loans complete,
  but systematically taxes every bill behind an active loan.

## Bottlenecks (evidence → code)

1. **Survey stalls — the only wall-clock bottleneck.** `new_mine_site`
   costs one 27–45s RCON-bound miss per plate (this run: copper 26.84s,
   stone 44.96s; ~2 min/run blind). Cache (`_NEW_DIRECT_MINE_CACHE`,
   `stage_extraction.py:38`) hits afterwards (0.00s re-surveys), so the
   cost is candidate probing: `choose_mining_origin` (l.182) evaluates
   candidates with one live `area_clear` /
   `drill_footprints_have_resource` probe each. Fix: batch — one Lua call
   scores ALL candidates server-side and returns the shortlist.
2. **Feedstock-cell borrowing — the systemic fragility.** Drill loan on a
   copper-cable cell (R2, +1752s), pipe loan on the circuit cell (R4,
   +2037s). No starvation this run, but any downstream waiter on the
   borrowed feedstock stalls until restore — the documented cycle-17
   cascade shape. Fix: downstream-need-aware borrow guard in
   `_borrow_free_mall_cell` (skip donors an active loan waits on with
   zero spendable stock); needs no signature changes (loans + stock are
   already in scope).
3. **WIP hoarding vs transferable bills.** Loan ingredient requesters lock
   finished goods (15 inserters) while open bills wait (R5). The
   readiness standard already measures transferable stock, but requester
   sizing (+20% headroom) over-claims against trickle production. Fix:
   cap concurrent loan claims per item to streaming rate; serve open
   bills before loan ingredient top-ups in cell delivery.
4. **Serial chemical ladder.** ~10 rungs (pipe → steel-chest → chem-plant
   → refinery → pumpjack → …) each borrow-to-completion on rotating
   cells. Independent rungs (pumpjack/offshore need only mall goods, not
   pipe) could run parallel on free cells. Fix after (2): admit
   dependency-free rungs alongside the ladder head.
- Explicitly NOT bottlenecks: UPS (60 throughout), runner CPU (~1.1%),
  power bridging (~2s), construction (161 ghosts in ~80s), RCON latency
  outside surveys.

## Plan (proposed order)
1. Borrow guard (2) — highest robustness leverage; bounded, testable.
2. Batched survey probes (1) — biggest wall-clock win; needs the Lua
   batching change in `choose_mining_origin` + cache-key care.
3. WIP-aware loan sizing (3) — small tuning, verify against R5 pattern.
4. Parallel ladder rungs (4) — after (2) lands.
5. Keep watching this run to steel/oil: confirm trim end-to-end, check
   coal-mine power + pipeline-vs-dynamic-pole live, validate tonight's
   oil fixes next session.

## R6 — run end: 00:33 session died +3971s on the SAME tile
- `STUCK: chemical_crude_pipeline ... pipe at (-297.5, -57.5) occupied by
  medium-electric-pole` — identical position as the 22:33 run's +5177s
  death. Deterministic, not bad luck. Run lasted ~66 min (vs 86 min:
  trim + parallel loans working).
- Live-game probe (server still up, world intact): the pole stands ON a
  vertical built-pipe run x=-297.5 (pipes y -65.5→-49.5, second pole at
  (-303.5,-57.5), roboport at (-293,-62)) — an east-west power chain that
  crossed the corridor after routing.
- Root cause: routing runs upfront, but `extend_power` fires mid-pass
  (after-packet backbone hookup) and per roboport wave while pipelines
  submit last. `extend_power` already honored `reserved_tiles`; the oil
  callers never passed them. Fixed in `8fae36e` (corridor reservation at
  both oil sites + roboport hookup inherits); staged-tree verified
  126 targeted + 648 fast green. Needs a fresh session to prove live.

## R7 — pipe dives (commit 12816ef, user blueprint decoded)
- Decoded the exchange string: two fluids interleaved with single-opening
  pipe-to-grounds (dir 8=south, 12=west in 2.x 16-way encoding). Confirms:
  max span 10, outward facings, closed sides let lines pass adjacent.
- Router constants already match (MAX_UNDERGROUND_SPAN=10); the gap was
  capability, not tuning: hard blockers were detour-or-die. Dives are now
  pass 2 (surface -> dive -> water). Killer tile resolves to one pair.
- Deliberately NOT done: routine interleaved (dense) routing -- the
  mixing ring stays; dives are fallback-only. Foreign crossings stay
  impassable. Both need live evidence before widening.
