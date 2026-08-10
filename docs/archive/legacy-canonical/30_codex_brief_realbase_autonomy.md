# Path: docs/30_codex_brief_realbase_autonomy.md
# Purpose: Handoff for Codex to finish the real-base autonomous factory-expansion system started this session — the plan, the current live-verified state, exactly how the service is run, and the remaining work.

Use gpt5.6-terra, high effort. Read `AGENTS.md` first (files <=500 LOC, "# Path:"/"# Purpose:"
header on every file, deterministic/symbolic planning only, commit messages explain why, do NOT
touch git remotes). This is a NEW direction, separate from the synthetic-sandbox electronics-block
pipeline that fills most of this repo — read the "What this is (and is NOT)" section carefully so
you don't accidentally wire the new work back into the old sandbox assumptions.

---

## 1. What this is (and is NOT)

The user pivoted the whole project to a new goal: **an autonomous system that, given a high-level
target ("produce X", "research Y"), figures out on its own how to build/expand a REAL factory on a
REAL Factorio save — surveying actual terrain, deciding what to build, placing it, connecting it,
and troubleshooting failures itself — with NO human babysitting each step.** The user's exact
words: *"It should do this decision making on it's own, not through you ... I give it a target ->
do x research, -> increase x production ... and it should figure out how to do that itself, not
through you babysitting it's every step."* And: *"if it's running low on electricity, make that
itself, if it's running low on assembly machines make that itself ... it should set up everything
on it's own from here on out."*

This is NOT the synthetic-sandbox pipeline. Do NOT reuse or extend:
- `planners/electronics_block.py`, `tools/build_processing_units.py`, `planners/resource_survey.py`
  (all hard-locked to the synthetic `planner-sandbox` surface + fixed WorldSpec).
- `experimental/legacy_autonomy/expansion_daemon.py` / `run_cycle.py` / `loop_daemon.py` (three older,
  non-integrated autonomy-loop prototypes, all sandbox-bound; the user's intent supersedes them —
  they implement only 2 of 9 catalog actions and assume a pre-registered line registry).

The new system lives in three NEW files created this session (all uncommitted — commit them as
part of your first change; they compile and are live-verified, see section 3):
- `orchestrator/live_base.py` (265 LOC) — observe a real surface/force over RCON.
- `orchestrator/autonomous_builder.py` (426 LOC) — the decide → build → troubleshoot loop.
- `planners/belt_bridge.py` (119 LOC) — reusable chest-to-chest belt+inserter connection geometry.

Two ALSO-uncommitted, load-bearing Lua changes make the mod able to target a real surface/force
(previously everything was hardcoded to `planner-sandbox`/`planner`). DO NOT revert these:
- `factorio_mod/sandbox_shared.lua`: `get_or_create_sandbox_surface(surface_name)` and
  `get_or_create_planner_force(force_name)` now take an optional arg to operate on an EXISTING
  surface/force (e.g. `"nauvis"`/`"player"`) with no creation/tech-sync/friendship — the real
  force already has its own research.
- `factorio_mod/layout_executor.lua`: `execute_build_plan` reads `build_plan.surface` and
  `build_plan.force` and passes them through, so a BuildPlan can carry `"surface":"nauvis"`,
  `"force":"player"`. These need a mod redeploy + server restart to take effect (see section 4).

---

## 1b. Prior state — everything the deterministic PLANNER already achieved (the "1M save" / `planner-sandbox` pipeline)

Before this real-base pivot, the whole project was a deterministic symbolic planner + live executor
that built ONE fixed factory on a SYNTHETIC surface. That work is committed (git history `78e21ea`
… `5b9a1a2`, milestones M1–M7) and, crucially, **fully working and live-verified** — it is the
proving ground for every geometry/execution primitive the new real-base system reuses. You are not
extending it, but you should MINE IT FOR PATTERNS, and you must not re-break what it got right.

What the sandbox pipeline does and achieved:
- **The build:** `planners/electronics_block.py::build_electronics_block(include_processing=True,
  world=...)` composes a complete raw-ore → iron/copper plate → cable → electronic-circuit →
  advanced-circuit → **processing-unit** factory (~4,300 production entities + ~250 infrastructure
  entities), plus the sulfur/sulfuric-acid oil chain, against a hand-authored `ElectronicsWorldSpec`
  (`tests/fixtures/electronics_world_spec.json`) on a generated 500×500 `planner-sandbox` surface
  (save `1M_test.zip`), isolated `planner` force.
- **Verified end-to-end LIVE** (`tools/build_processing_units.py --construction-mode radial
  --existing-topology reset`, checked by `tools/verify_factory_invariants.py`): exactly **1
  electric network, 1 roboport network, 0 stuck ghosts**, oil refineries `working` with crude
  flowing, processing units actually produced. This clean result was the payoff of M7.

Load-bearing things it got RIGHT that the real-base system depends on (do not regress):
- **Pole auto-wiring root cause (the single most important fix).** Factorio's
  `LuaSurface.create_entity` auto-wires nearby electric poles UNRELIABLY once ~60+ poles already
  exist on a surface (verified live: fine with 2–3 poles, silently drops connections at scale).
  Fixed in `factorio_mod/layout_executor.lua::ensure_pole_wiring` — after every placement it
  explicitly wires each pole pair within `min(get_max_wire_distance)` using the modern
  `get_wire_connector(pole_copper).connect_to(...)` API (NOT `connect_neighbour`, which doesn't
  exist on poles in 2.0.77). The new real-base builder relies on this exact pass every time it
  submits a plan.
- **Live-verified prototype constants** (match `planners/infrastructure.py::POLE_SPECS`):
  substation wire=18 supply=9, big-electric-pole wire=32 supply=2, medium wire=9 supply=3.5,
  small wire=7.5 supply=2.5; roboport construction radius 55, logistic/link ~46 (conservative 50
  hard limit). The new `autonomous_builder.py` uses these same numbers for `extend_power` /
  `extend_roboport_coverage`.
- **Geometry primitives you should reuse, not reinvent:** `planners/infrastructure_geometry.py`
  (`step_points`, `choose_clear_l_route`, `minimum_spanning_tree_edges`, `FootprintPlacer`),
  `planners/local_layout_planner.py::generate_line_layout` (+ `generate_mining_feed`), and the
  fluid geometry in `planners/fluid_layouts.py` / `planners/fluid_routing.py`
  (A* obstacle-aware pipe routing, `generate_fluid_machine_row`). `planners/belt_bridge.py` (new)
  already wraps `choose_clear_l_route` for the real base — a `pipe_bridge` for fluids should mirror
  it.
- **Native-tier defaults + explicit upgrade path** (`a4145f0`): initial builds use
  `fast-transport-belt` + `fast-inserter` (Nauvis-craftable); express/stack/turbo are
  planet-imported and only worth it at scale — there's a `tools/generate_electronics_upgrade_plan.py`
  + `factorio_mod/upgrades.lua` upgrade flow. Keep this principle on the real base.

Known OPEN issues carried over from the sandbox work (documented in `docs/28`, `docs/29`; on a real
Nauvis save some of these DON'T apply because water/oil is real terrain, not seeded):
- Offshore-pump orientation/`water_lakes.py` direction semantics are backwards, and correcting them
  collides with the pump's own power scaffold (task list #1). On the real base you SKIP lake-seeding
  entirely but still need the correct pump connector geometry from `docs/29`.
- A belt-turn-immediately-after-an-underground-exit can be left disconnected (item-routing tunnel
  geometry) — relevant if you reuse the sandbox item router; the new `belt_bridge` avoids it by
  routing simple L-paths.
- `docs/26`/`docs/27` are the M6/M7 session-state handoffs with the full blow-by-blow if you need
  deeper history; `CURRENT_STATUS.md` is the append-only milestone log.

The through-line: the sandbox pipeline proved the deterministic geometry + the live-execution mod
work. The real-base system swaps out the "fixed WorldSpec on a synthetic isolated surface"
assumption for "survey and adapt to a real surface/force," reusing the proven primitives.

---

## 2. Architecture of the new system (how it already works)

`orchestrator/autonomous_builder.py::run(goal_item, ...)` loops:
`survey → decide the single deepest missing stage → build it → troubleshoot → repeat`, until the
goal item has a real, working line, or it raises `StuckError` (never guesses silently — a hard
project rule).

- **Survey** (`orchestrator/live_base.py`): `find_line` (existing machines by recipe + working
  count), `nearest_resource` (nearest real ore/resource patch + its bbox), `find_clear_area`
  (nearest buildable box, ring search — note: it EXCLUDES resource tiles from "occupied" so a
  mining stage can stand ON ore), `entity_at`/`is_safe_to_clear`/`remove_entity_at`
  (obstruction handling), `entity_status_name` (decode live `.status`), `pole_network_id` +
  `nearest_pole_on_other_network` (power-gap detection), `nearest_roboport`, `chest_contents`,
  `bot_and_power_summary`.
- **Decide** (`ensure_produced`): recursion over `planners/recipe_data.py::LINE_RECIPES`. If the
  item is already producing (>=1 machine with that recipe in `working` status) → return its
  output chest position. Else if it's a `_mineable` recipe (sole ingredient is a raw resource,
  not another LINE_RECIPE) → build a mining+smelting stage. Else → recursively ensure every
  ingredient is produced first, collect their output-chest positions, then build a conversion
  stage fed from those. Builds exactly ONE stage per call and returns None, so the caller
  re-surveys each loop (idempotent, restart-safe).
- **Build**: `build_mining_stage` (uses `LocalLayoutPlanner.generate_line_layout(mining_feed=True)`),
  `build_conversion_stage` (chest-fed line + a `belt_bridge` from each ingredient's real upstream
  chest). Both call `strip_local_power(plan, remove_substations=False)` to drop the sandbox's
  free `electric-energy-interface` cheat but keep the local substation, then set
  `plan["surface"]="nauvis"`, `plan["force"]="player"`. Conversion stages replace the sandbox's
  `infinity-chest` feeders with real `steel-chest` (via `_swap_infinity_chests`) — no cheating,
  everything comes from mined ore.
- **Troubleshoot** (the user explicitly asked for this — all three are implemented AND
  live-verified this session):
  - `_submit` retries after clearing a blocking tile ONLY if it's safe map clutter (tree/rock,
    neutral force); anything a force built raises `StuckError` ("go around, not through") rather
    than bulldozing real infrastructure.
  - `extend_power`: if a stage's machines report `no_power`, detect the isolated pole network and
    build a real medium-pole chain to the nearest pole on another (the main) network.
  - `extend_roboport_coverage`: if ghosts never build because the site is beyond every roboport's
    55-tile construction radius, chain new roboports out (within link distance) until covered.
  - `_diagnose_machines`: polls each machine's real `.status` with a grace period (bots are slow),
    reports the actual stuck reason; for conversion stages, if a feed chest is empty it names the
    specific bridge that isn't delivering.

Everything is materialized as a standard BuildPlan (`schemas/build_plan.schema.json`) and submitted
through the EXISTING `orchestrator/game_bridge.py::GameBridge.build_layout(authorization, plan)`
(backed by `factorio_mod/layout_executor.lua`), which already preserves direction/recipe/etc. and
does explicit pole auto-wiring. Authorization comes from
`planners/sandbox_infrastructure.py::build_layout_authorization`.

---

## 3. Current live-verified state (what's actually running on the user's save)

Save: a copy of the user's `mod_playground.zip` (real Nauvis, `player` force). Starting point the
user placed by hand: 1 roboport at (3,-1), 50 logistic + 50 construction bots, an
electric-energy-interface at (0,0), substations forming a small powered grid, and two
passive-provider chests holding a large starter stockpile of machines/belts/inserters/materials
(that stockpile is for BUILDING entities, NOT for feeding production — the user was explicit that
science packs must be made "from scratch by mining ore").

**ACCURACY NOTE (added after a later server restart):** the factory described below WAS built and
verified live, but it was never `/server-save`d, so the on-disk `mod_playground.zip` copy reloaded
clean. The base is currently back to its pristine starting state (1 roboport, 1
electric-energy-interface, 4 substations, the 2 stockpile chests, 1 storage chest — nothing else).
The verification below is a true record of what the code achieved, NOT a description of what is
physically standing right now. Re-running `run("automation-science-pack", ...)` should rebuild it.
Lesson: call `/server-save` after any live build you want to keep.

Verified working live this session (real ore → real product, nothing scripted):
- Iron chain: 3 mining drills on the real iron patch → 3 electric furnaces → iron-plate (135+
  produced) → 2 assemblers making iron-gear-wheel (88+ produced) → belt bridge back to →
  2 assemblers making automation-science-pack (18+ produced). All on the existing power grid.
- Copper chain: `ensure_produced('copper-plate', ...)` was run through the autonomous builder and
  exercised ALL THREE self-repair paths for real: the copper patch is ~60 tiles out, so the build
  hit (a) `no_power` → `extend_power` built a pole bridge and fixed it, and (b) ghosts beyond
  roboport range → `extend_roboport_coverage` chained a roboport out and they built. As of handoff,
  1 of 2 copper drill/furnace pairs is mining+smelting; the 2nd has the known bug below.

Nothing here is committed yet. `git status` shows the 2 modified Lua files + 3 untracked new
Python files. `git log` head is `5b9a1a2`.

---

## 4. EXACTLY how the service is run (you will likely NOT have live RCON — user runs it; but this
is the full procedure so your code matches it)

Same division of labor as prior Codex briefs: **you write/extend the code offline; the USER runs
it live against their save and reports back.** But your code must match this runtime exactly.

**Server (headless Factorio against a COPY of the save — never the user's original):**
- Data dir this session (session-specific temp path; the user will have their own when they run
  it — treat the path as a variable `$fd`):
  `...\scratchpad\mod_playground_run\` containing `config.ini`, `mod_playground.zip` (copy),
  `server-settings.json` (with `auto_pause=false`), and `mods\factorio_cursor_rl_agent\`.
- `factorio.exe` at `E:\Games\Factorio\bin\x64\factorio.exe` has "Run as administrator" forced in
  its Windows compat settings → it can ONLY be launched from an ELEVATED PowerShell. The agent
  cannot self-elevate; the user runs this launch command:
  ```powershell
  $fd = "<data-dir>"
  $exe = "E:\Games\Factorio\bin\x64\factorio.exe"
  $argList = @("--config","$fd\config.ini","--mod-directory","$fd\mods","--start-server","$fd\mod_playground.zip","--server-settings","$fd\server-settings.json","--port","34199","--rcon-port","27017","--rcon-password","planner_test")
  Start-Process -FilePath $exe -ArgumentList $argList -RedirectStandardOutput "$fd\launch_out.log" -RedirectStandardError "$fd\launch_err.log"
  ```
  Ports: game 34199, RCON 27017, password `planner_test` (chosen to not collide with the older
  sandbox server on 34198/27015 if it's also up).

**Mod deploy (needed after ANY `factorio_mod/*.lua` change — mods only load at server start):**
copy `info.json` + every `factorio_mod\*.lua` into `$fd\mods\factorio_cursor_rl_agent\` AND into
`%APPDATA%\Factorio\mods\factorio_cursor_rl_agent\` (so the user's own client matches — a mismatch
gives "mod script files are not identical between you and the server" on join). After deploy, the
server AND the user's client both need a full restart. Byte-identical mod folders are required.

**RCON (ad-hoc queries):** from repo root, Git Bash:
```bash
MSYS_NO_PATHCONV=1 python tools/rcon_client.py --host 127.0.0.1 --port 27017 --password planner_test "/sc <lua> rcon.print(...)"
```
`MSYS_NO_PATHCONV=1` is required so Git Bash doesn't mangle the leading `/` of `/sc`.

**Running the autonomous builder (the actual service):**
```python
from tools.rcon_client import RconClient
from orchestrator.game_bridge import GameBridge
from orchestrator.autonomous_builder import ensure_produced, run
# one target end-to-end:
run("automation-science-pack", surface="nauvis", force="player",
    rcon_port=27017, rcon_password="planner_test",
    script_output=r"<data-dir>\script-output", reference_point=(3.0, -1.0))
```
`reference_point` is where the builder starts searching for space/resources (the roboport at
(3,-1) is a good origin). `script_output` must be the server's own `script-output` dir (that's
where the mod writes its JSON reports that GameBridge polls).

A clean-slate reset (the older sandbox helper) is NOT used here — this is a real base, you never
wipe it. To remove a specific mis-built test stage, target exact entity NAMES in a bounded area
(a blanket "destroy everything in area" is correctly blocked by the safety classifier and by good
sense — never do it on the real base).

---

## 5. The plan / remaining work (in priority order)

**A. Fix the known drill-placement bug (start here — it's the immediate blocker).**
`build_mining_stage` calls `find_clear_area` near the ore patch, then places drills via
`generate_mining_feed` (drills 3x3, at rows -3..-1 north of the line's input belt). The bug: the
chosen origin can put one or more drills' 3x3 footprints partly OFF the ore patch's irregular
edge, so those drills report `no_minable_resources` while others work. `find_clear_area` only
checks that the AREA is buildable (and now correctly ignores resource tiles), but does NOT verify
every individual drill footprint actually overlaps minable ore. Fix: after computing candidate
drill positions, verify (live, via a new `live_base` helper that queries `find_entities_filtered
{type='resource', area=<drill 3x3>}`) that each drill's footprint contains the target ore; if not,
shift the whole line origin along the patch (or shrink machine_count) until every drill sits on
ore. Prefer aligning the line to the patch's actual bbox (returned by `nearest_resource`) over
guessing. This is deterministic and testable offline with a fake bbox + fake resource-tile set.

**B. Add the missing science-pack recipes to `planners/recipe_data.py::LINE_RECIPES`.**
Only `automation-science-pack` is defined. The user's target set is automation, logistic, chemical,
and production science packs. Add `logistic-science-pack`, `chemical-science-pack`,
`production-science-pack` and every intermediate they need that isn't already present (check
Factorio 2.0 Space Age recipes live via RCON `prototypes.recipe['<name>'].ingredients` against the
running game — do NOT trust memory for 2.0 recipes; they differ from 1.1). Chemical science pulls
in an oil→sulfur→sulfuric-acid fluid chain and engine units; that means the builder needs a fluid
path too — see C. Respect the existing LINE_RECIPES shape (machine, ingredients, amounts,
product_amount, craft_time, and fluid_ingredients where relevant, as processing-unit already
shows).

**C. Extend the builder to handle FLUIDS (currently solids-only).**
`build_conversion_stage` bridges solid ingredients over belts only. Chemical/production science
need pipes (water from offshore pump, oil from pumpjack, sulfuric acid between stages). You have a
lot of prior fluid geometry to draw on (`planners/fluid_layouts.py`, `planners/fluid_routing.py`,
`generate_fluid_machine_row`) but it's all sandbox-shaped — adapt the PATTERN (a
`pipe_bridge` analogous to `belt_bridge`, plus fluid-source stages for real pumpjack/offshore-pump
placement) rather than wiring in the sandbox composer. Note the offshore-pump direction bug fully
diagnosed in `docs/29`/`planners/water_lakes.py` — on a REAL Nauvis save the water/oil already
exists as terrain, so you do NOT seed lakes here (that was a synthetic-sandbox concern); you just
need to place the pump/pumpjack correctly against existing water/oil and connect the pipe. Use the
live-verified connector geometry in `docs/29` (pumpjack west-facing connector at center+(-1,+1),
offshore-pump connector semantics) and re-probe live to confirm before trusting.

**D. Generalize "build more of X when running low" beyond power/roboports.**
Power and roboport self-expansion are done. The user also wants: low on assembly machines → build
more; low on belts/inserters/pipes/rails/bots → produce those itself; i.e. a dedicated
"expansion supplies" sub-factory. This is the largest remaining piece. A reasonable first slice:
when `run(goal)` detects a stage is throughput-limited (all its machines `full_output` upstream
but downstream still starved, or a feed chest chronically empty despite a working upstream), add a
parallel line or more machines — reuse the `add_parallel_line`/`extend_line_x` IDEAS from
`core/action_catalog.py` but implement them against the real base, not the sandbox. Do NOT try to
build the whole 9-action catalog at once; implement the specific expansions the science-pack goals
actually demand, verify each live, then broaden. Flag anything that needs a product decision (e.g.
"how many parallel lines is 'enough'") rather than inventing a magic threshold.

**E. Wire a top-level goal/target interface.**
Right now the entry point is `ensure_produced`/`run(goal_item)`. The user wants to hand it targets
like "do X research" and "increase X production by N". Add a small CLI/driver
(`tools/autonomous_run.py` or similar) that accepts a goal spec and drives `run` to completion,
emitting progress. Research targets mean: ensure the required science packs are producing, then
set the research queue (there's a `/set_research` mod command + `game_bridge.set_research`) and
report progress — but confirm the tech tree state on the REAL player force first (it already has
whatever the user researched).

---

## 6. Hard constraints (do not violate — these are the user's standards, learned the hard way)

- **Never babysit / never guess silently.** Every failure path either self-repairs deterministically
  or raises `StuckError` with the real diagnosed reason. No fabricated success.
- **From scratch by mining ore** — production ingredients come from mined resources, never from the
  user's starter stockpile chests (those are for building entities only) and never from
  infinity-chest cheats.
- **Real base = never destructive at scale.** Route around real infrastructure; only auto-clear
  neutral map clutter (trees/rocks). A blanket area-wipe is forbidden.
- **Electric-only** (no burner drills/boilers/fuel furnaces) unless the user says otherwise.
- **Verify live, not by assertion.** Every real defect this project has ever had was found by
  running the actual game (preflight/tests passing != game truth). Your offline work must be
  structured so the user can run it live and it actually works; where you can't verify live
  yourself, say exactly what's unverified.
- Files <=500 LOC; keep the "# Path:"/"# Purpose:" headers.

---

## 7. Deliverables / report back

Commit the 3 new Python files + 2 Lua changes as your first commit (they're validated), then
proceed through the plan. Report: which files changed, LOC of each, what you verified offline vs.
what needs the user's live run, and any point where you had to stop for a product decision. Do NOT
push. Do NOT connect to a live Factorio instance yourself — hand live-run steps to the user with
the exact commands from section 4.
