# Path: docs/archive/handoffs/32_full_session_log_2026-08-22.md
# Purpose: Complete session record for the 2026-08-22 mining-productivity-4
# observation campaign: every run, every failure, every fix, and the state of
# the deterministic runtime when the session closed.

# Full session log — 2026-08-22

## Goal

Boot the isolated deterministic Factorio server + operations console, run the
autonomous builder on the `mining-productivity-4` research mission from the
safe save, observe every decision it makes, fix what breaks, and repeat until
the mission completes or the failure classes are exhausted. The user steered
continuously with design corrections; each correction was encoded as code plus
a regression test before the next cycle.

## Environment

- Isolated deterministic server: `~/.local/share/factorio-rl/deterministic`,
  game `127.0.0.1:34199`, RCON `127.0.0.1:27017`, Factorio 2.1.14.
- Console: `tools/dashboard_server.py` on loopback `9137` with both native
  managers wired.
- Runner: `scripts/manage_linux_deterministic_runner.sh start --python
  .venv/bin/python --technology mining-productivity-4`.
- Every cycle: runner stop → server stop → `deploy` → save `reset`
  (source `~/.factorio/saves/mod_playground.zip`, backup kept per reset) →
  server start → runner start.
- No Lua changed all session: deployed mods matched the repository throughout;
  only Python + tests were modified. Nothing committed (charter: no unverified
  commits while the mission is incomplete).

## Method

Each cycle followed the same loop, which is the reason the failure count grew
while repeat failures shrank to zero:

1. Run until `STUCK`/`RUN END` or a user-visible wrong behaviour.
2. Read the exact failing window of `logs/autonomous-run.log`.
3. Probe the live world read-only over RCON (entity statuses, logistic
   sections, bounding boxes, network ids, item counts, belt contents) instead
   of reasoning from the plan's intent.
4. Reproduce the decision offline where possible (dry-run preflights, spy
   wrappers around `_submit`) to pin the responsible line.
5. Fix the cause, add a focused test that fails on the old behaviour, run the
   suite, redeploy/reset/restart, observe again.

Live probing decoded several things no static reading could: Factorio 2.1's
`defines.entity_status` enum values (36 = `waiting_for_space_in_destination`,
34 = `waiting_for_source_items`, 29 = `full_output`), logistic sections vs the
legacy request-slot API, roboport electric-network ids vs logistic network
fragmentation, and that script-placed entities never auto-wire.

## The runs

| # | Start | Length | End cause | What it proved |
|---|---|---|---|---|
| 1 | 22:58 | ~4 min | livelock: transport-belt stuck 24% | circular shortage diagnosed |
| 2 | 00:51 | 98 s | six no-op remediation rounds | iron system BUILT end-to-end; chain spacing fixed |
| 3 | 01:04 | ~3 min | cell starved | real charge wait; requester correctly configured via sections API |
| 4 | 01:18 | ~3 min | starved again; second cell opened elsewhere | intake placed; reuse missing; health StuckError escaped |
| 5–7 | 02:15–02:52 | minutes each | intake geometry iterations | ground-truth candidates needed; drop-chest interface designed |
| 8 | 02:40 | minutes | power race crash at (48,-70) | automation-science GOAL MET first time |
| 9 | 03:01 | minutes | gate deferral escaped StuckError handler | GATE WORK concept proven |
| 10–12 | 03:17–03:54 | short | inserter 75% livelock variants | gate parity between prep and expansion paths |
| 13 | 04:10 | short | (11,5) collision returned on fresh save | scaffolding excusal implemented |
| 14 | 04:32–05:00 | minutes | GOAL MET; inserter livelock persisted | prep-path gate parity added |
| 15 | 05:08–06:07 | short | intake iterations (drop chest, candidates) | blocked-drill drop-chest interface works |
| 16 | 06:07–06:58 | longer | landfill priority inversion | defer-behind-prerequisite generalized |
| 17 | 07:10–08:00 | minutes | belt adoption failures (three masks) | own-belt adoption generalized to whole belt family |
| 18 | 08:46–09:42 | 56 min | single steel ghost, "no blockage", 360 s | FURTHEST RUN: steel conversion under construction |
| 19 | 10:08–11:35 | ~87 min | same ghost, 540 s (multi-ghost refusal) | electric-furnace gate chain needs persistent intermediates |
| 20 | 14:14–14:16 | 2 min | inserter 75% returns | stock-movement patience added |
| 21 | 16:52–17:00 | 8 min | landfill belt: base stock 28, network 0 | material delivery remedy designed |
| 22 | 17:21–18:04 | ~45 min | passive-provider-chest demand loop | priority inversion generalized (`other_pending`) |
| 23 | 18:56–19:31 | 35 min | inserter 75%, frozen stock | CELL DELIVERY (relocate ingredients into the stalled cell's network) |
| 24 | 20:41–20:49 | ongoing when stopped | healthy: landfill building, inserters 12/12 fast | full stack holding |

Run numbering here is by session order; archived logs under
`~/.local/share/factorio-rl/deterministic/logs/archive/` hold the raw evidence
for every one of them.

## The fix catalog

### A. Breaking the plate bootstrap circle

1. **Cold-start fallback** (`_cold_start_belt_shortage`,
   `_bootstrap_logistic_plate_line`, `build_logistic_smelter`): when the FIRST
   plate system fails affordability on belt-family items alone, open the
   temporary requester-fed smelter instead of re-queueing belts into a demand
   loop that feeds itself. Deliberately not cached so BOOTSTRAP UPGRADE can
   later replace it. Tests:
   `tests/test_plate_bootstrap_circle.py`.
2. **Inserter exemption, then retraction**: inserters were briefly part of the
   exemption family; the user rejected requester-fed refineries as a
   destination, and run 13 proved the mall produces inserters within minutes,
   so inserters were removed again — their shortages flow through normal mall
   demand now.
3. **Eager swap** (`_retire_standing_bootstrap_cells`): the moment the real
   belt-driven system passes affordability, standing cells are deconstructed
   FIRST and the proper refinery installs in their place — the user standard,
   after run 15 left both setups fighting.

### B. The ore intake (five iterations)

4. Intake must be a logistic CHEST interface, not a belt tile
   (`ore_pickup` parameter; coverage checks name chests).
5. Ground-truth candidate tiles (`intake_candidate_tiles`): a tile qualifies
   when a drill drops onto it OR its lanes hold items now — geometry guesses
   failed three different ways (westbound rows, upstream tails, wedged drop
   columns).
6. All four directions tried per anchor; modular rows wedge drop columns
   between two drill bodies, leaving only end caps open.
7. Dead-pair liveness gate: a legacy tap whose inserter sits in
   `waiting_for_source_items` with an empty chest is skipped at the ANCHOR
   level, not just the direction, so fresh taps are never clustered onto a
   corpse.
8. Preferred interface: a provider chest placed on a BLOCKED drill's empty
   drop tile (`blocked_drill_drop_tile`) — zero demolition, becomes the mine's
   ore interface.

### C. Logistics reality

9. Real bounded charge wait (`_wait_for_logistic_service`, 90 s) replaced six
   instant no-op rounds.
10. `extend_power` replans once when a hop loses its tile to a concurrent
    build between survey and bot arrival.
11. Own-belt adoption in `_submit`: joins onto our force's standing belts of
    ANY tier or kind (direction_mismatch *and* occupied_by_different_entity)
    adopt the corridor instead of crashing — generalized across the whole
    belt family including undergrounds.
12. MATERIAL DELIVERY: when a ghost needs X, base stock has X, but its local
    network has none, a provider chest is placed beside the stage and stock is
    relocated into it (`network_item_count` + `transfer_stock`,
    conservation-honest single-Lua-transaction moves).
13. CELL DELIVERY (`_deliver_cell_ingredients`): the same relocation for a
    stalled MALL cell — run 18's inserters held 16 gears and zero plates while
    plates sat across a network gap.

### D. Economy (user standards encoded)

14. Roboport chains travel link-distance per hop (user-flagged waste).
15. Belt cell prepped BEFORE any mine spends stock
    (`_prep_the_belt_cell`).
16. Essential routes spend stocked FAST tiers first
    (`_essential_belt_type`); bidirectional rebalance
    (`_prefer_stocked_belt_tiers`) swaps toward whichever tier covers the
    plan, downgrades included.
17. Underground/splitter-class consumers defer behind the blueprint belt
    reserve (`_belt_starved_consumer`, floor 50).

### E. Priorities and gates

18. Defer behind queued prerequisite whenever any OTHER pending demand exists
    (`other_pending`), not just when the target dict grows — landfill had
    outranked the chests and belts it demanded.
19. Bootstrap-cap gates queue an electric-furnace producer as concrete work
    from BOTH the prep path and the expansion path
    (`_queue_electric_furnace_unlock`); the expansion handler also catches
    `ProductionPrerequisiteDeferred` (it used to crash the runner).
20. `PERSISTENT_INTERMEDIATES` gained `advanced-circuit` and `steel-chest` so
    the furnace-unlock chain schedules their producers instead of draining
    starter stock unbacked.
21. Livelock patience counts STOCK MOVEMENT as progress
    (`last_items_total` trend resets the unchanged-pass counter) — slow
    supply behind a gate is waiting, not spinning.

### F. Geometry and diagnosis honesty

22. Starved refinery → grow ITS OWN mine (recursion into `expand=True`),
    never stack capacity onto an unfed line; furnace sizing carries a
    physical ceiling (~2× drill count) against catalog-spec inflation, while
    still rewarding force productivity.
23. Refinery surveys excuse OUR OWN poles/roboports/belts
    (`_SERVICE_INFRASTRUCTURE_TYPES` + `_own_service_infrastructure`);
    foreign infrastructure still fails closed.
24. Lone undiagnosed ghosts get ONE remove-and-resubmit cycle
    (`_rebuild_stale_ghost`), multi-ghost capable.
25. Stale `UNBACKED_DRAWS` expire when a mining stage succeeds.
26. Solar top-up after every power bridge on lean networks (<1 MW), panels
    from stock, pole included so the executor wires them in;
    `solar-panel=8` added to starter reserves.

## Live-debugging playbook that produced these findings

- Decode enums from the game, never from memory:
  `pairs(defines.entity_status)` printed the whole table.
- Logistic SECTIONS are the truth for requester chests;
  `get_request_slot` silently reports nothing for section-based requests.
- `LuaLogisticNetwork.robot_count` / `.owner` / `cell.available_*` do not
  exist in 2.1 — robot ground truth came from scanning
  `find_entities_filtered{type={'logistic-robot','construction-robot'}}` and
  roboport `roboport_robot` inventories.
- Script-placed entities NEVER auto-wire; every placement that expects power
  or network membership needs explicit verification (which produced fixes 9
  and 10).
- `get_transport_line(n).get_contents()` per belt tile settles "is it jammed,
  empty, or flowing" in one query — this ended three rounds of speculation
  about intake placement.
- Dry-run reproduction through the real planner functions
  (`plan_local_extraction` + `_build_initial_plate_smelter(preflight_only=
  True)`) reproduced runner decisions exactly without mutating the world.
- Python-side spy wrappers around `_submit` turned "why did it place THERE"
  into one readable call.

## Test suite

Grew from 1613 to **1669 passed / 30 skipped** across the session. New files:
`tests/test_plate_bootstrap_circle.py` (14 tests),
`tests/test_bootstrap_priorities.py` (6),
`tests/test_power_and_belt_economy.py` (13), plus regressions folded into
`tests/test_logistic_coverage.py` (chain spacing),
`tests/test_ghost_diagnostics.py` (charge wait, race replan), and
`tests/test_extraction_separation.py` (supply ceiling). Several suites were
rebuilt mid-session after a bad splice duplicated
`test_plate_bootstrap_circle.py`; the reconstruction is clean and every test
name is unique again.

## State when the session closed

- Server RUNNING on `34199`/`27017` with the run-24 world (automation science
  producing, inserters 12/12, landfill refinery building past every previous
  blocker). Runner deliberately STOPPED to close out the report.
- Console on `9137`. Save backups accumulated per reset under
  `saves/backups/`.
- Working tree: five modified Python modules/tests groups + four new test
  files + two docs. NOTHING COMMITTED — per charter, commits wait for a fully
  completed mission.
- `CURRENT_STATUS.md` carries the session entry pointing here.

## Remaining work, in order

1. Resume run 25 from the current world (no reset needed — run 24 was healthy
   when stopped) and let the landfill → oil → chemical-science chain play out.
2. Expected next walls, pre-diagnosed:
   - Oil cell siting near water with the same fragmented-network material
     questions (delivery remedy should cover it).
   - Logistic-science requires the iron system to reach its proper belt-fed
     form; the swap machinery exists but has not yet fired in a live run.
   - The stone multi-site policy is only partially solved: growth-own-mine is
     in, but genuinely-needed second sites still need an ore-headroom check
     before siting.
3. When a run clears chemical science, append CURRENT_STATUS and commit the
   session as scoped commits (`fix(deterministic): ...` series).
4. Longer-term: unify logistic networks along construction corridors (chain at
   ≤22-tile spacing) so CELL/MATERIAL DELIVERY become rare rather than
   structural.
