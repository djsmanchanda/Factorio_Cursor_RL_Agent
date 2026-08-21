# Path: docs/30_session_handoff_2026-08-21.md
# Purpose: Resume handoff for the 2026-08-21 session: console layout, roboport
# waste/spike fixes, and the research-queue run autopsy with its three fixes.

## State right now (read this first)

Latest commits: `6de3bc5` "fix(deterministic): coverage serves plans; pending
systems defer", then the autopsy commit landing on top (see `git log -3`). The
research-queue mission for `mining-productivity-4` FAILED at 20:33:15 after
3m54s (livelock guard); its failure class is now fixed in code but **has not
been re-run live**. Immediate next action: restart the Python runner from the
console (9137) and re-run the research queue; expect the iron system to build
through its own mine scaffold.

Runtime: deterministic server healthy on loopback 34199/27017 (Factorio
2.1.14, isolated root `~/.local/share/factorio-rl/deterministic`), dashboard
on 9137, no runner active. World state: copper system complete and productive
(provider chest held 1,204 plates, 6/6 furnaces working); iron mine built but
its refinery was never placed (the failed run's blocker); mall cells built.
A fresh restore before the next run is fine — nothing depends on this state.

## The failed run, in one paragraph

The user started the queue three times (20:26/20:29/20:29) with a save restore
between starts. Run B rebuilt the mall cells idempotently, submitted the iron
mine at 20:29:51, then the initial iron-refinery preflight ran 4s later and
found tiles (11,5),(11,6) occupied by a player-force substation at exactly
(10.0,4.0) — **the mine plan's own retained power scaffold**, whose LIVE
bounding box covers tiles the declared footprint constants never claim.
`_plate_expansion_foundation` raised StuckError → prep marked iron-plate
prepped-and-deferred → every later pass the MALL recursion re-called
`build_mining_stage(iron-plate)`, whose `assert_affordable` queued a phantom
`transport-belt=53` demand each pass. Belts need iron plates; the blocked
system is the iron producer; stock froze at 13/53; after 12 identical passes
the livelock guard correctly ended the run — while copper finished building
and reached 6/6 working.

## What was implemented (all Python-only)

1. **Occupant-identity collision classification** (`live_base.occupied_tile_owners`
   + `_planned_entity_positions`): collisions are excused when the occupying
   entity's name + exact centre matches an action of this system's own plans
   (mine bill ∪ delta). Tile arithmetic against footprint constants can never
   classify this — live bounding boxes exceed declared footprints.
2. **Deferral over phantom bills**: a genuine foreign-infrastructure blockage
   on the initial path now raises `ProductionPrerequisiteDeferred`; prep
   retries it later without marking prepped, and NO MaterialShortage demand
   is queued for a site that will not be approved.
3. **Ghost-trend livelock reset** (`_livelock_step` + `pending_ghost_count`):
   one cheap RCON ghost count per pass; a falling count resets the no-progress
   bound because bots visibly building is patience, not a spin.

Also earlier this session (commit `6de3bc5`, all verified by the 20:29 run's
log): position-based construction coverage replaced bounding-box corners;
coverage stages only AFTER affordability via the `_submit(stage_coverage=...)`
hook; preflight-only dry runs no longer mutate the world; roboport chains land
in waves of 3 while network generation < 100 MW (live-probed
`get_max_energy_production()`, kW; roboport buffer 100 MJ, ports spawn ~50%);
`PendingSystemDeferred` + furnace-ghost-proximity duplicate detection (the old
detector walked horizontal rows; modular templates build vertical columns).
Dashboard layout fixed (priorities pinned under Frequent actions).

## What NOT to do

- Don't redeploy Lua for any of this — no mod file changed today.
- Don't treat a repeated decision signature alone as a stall; check the ghost
  trend first (that IS the new guard behavior).
- Don't weaken `_plate_expansion_foundation`'s refusal: foreign infrastructure
  must still fail closed; only planned-centre-matched OWN entities are excused.

## Open / next

1. Re-run `mining-productivity-4` from the console and watch the iron system
   build through its own scaffold (the exact case that died).
2. If an initial site is genuinely blocked by FOREIGN infrastructure the run
   now defers indefinitely — the follow-up is anchor-walk re-siting: have
   `plan_local_extraction` return ranked alternate smelter origins and let
   `build_mining_stage` try the next candidate on a real-collision deferral.
3. Mission hygiene: refuse a new research-queue start while another runner PID
   lives; log save-restores in the run header (the triple-start + mid-mission
   restore cost real forensics time).
4. Known cosmetic: `PREP DEMAND`/`MALL DEMAND` duplication of the same item
   across passes is noisy; consider demand de-duplication in `add_demands`.

## Durable lessons reinforced

- Preflight surveys and executor truth disagree at the margins; only running
  the game finds it. This failure was invisible offline: every test passed
  while the mine/refinery pair had never been executed back-to-back live.
- A deferred stage must not keep billing the mall for work that cannot be
  approved — circular shortages should surface as named blockers, not as
  unrelated item shortfalls.
- Evidence ladder: fixes 2 and 3 rest on a live-failed run plus focused tests;
  fix 1 rests on live probes of the surviving world (substation identity,
  copper health) plus tests. Nothing has been validated against a NEW live
  run yet — that is the highest-evidence step remaining.
