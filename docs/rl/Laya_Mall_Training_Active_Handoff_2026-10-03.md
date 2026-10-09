<!-- Path: docs/rl/Laya_Mall_Training_Active_Handoff_2026-10-03.md | Purpose: Coordinate ownership while the active agent implements and launches twenty mall RL sessions. -->

# Active mall training ownership — October 3

This is a coordination handoff, **not a pause or transfer of this task**.
The current agent continues implementing and validating the mall fleet.
Other agents may work on separate tasks while preserving this ownership.

## Authorized task and latest intent

Launch **20 simultaneous disposable mall RL sessions for eight hours**.
Learn what to manufacture, what to prioritize, when to expand and when to
upgrade equipment. Optimize faster manufacture of the required supply-upgrade
batch, earlier earned supply increases and faster subsequent useful production.

Latest fixture:

- **4 AM1s, 8 regular inserters, 6 requester chests, 12 passive-provider chests**.
- The starter/supply passive-provider chest is moved **15 tiles from the first
  mall module's centre**; output chests keep their functioning cell geometry.
- Initial iron/copper are 1 plate/s each. Every physically consumed newly
  manufactured batch (2 drills, 10 belts, 4 inserters, 1 splitter) adds
  1.5 plates/s to the queue-needed supply; no rate cap or concurrent time ramp.
- The learner may manufacture additional AM1s, upgrade AM1 -> AM2 and upgrade
  regular -> fast inserters. Upgrades must use manufactured items and bots.
- Vertical slots remain reserved until long-handed inserters are funded.
  Regular/fast one-tile inserters cannot replace those long arms.
- Engine inspection confirmed AM2 requires steel. Declared stone/coal deposits
  and mining/furnace actions provide a path without extra construction gifts.

Training, production and viewing remain isolated. The existing completed-mall
viewer is permanently viewing-only and cannot be used for training.
No automatic production promotion is authorized.

## Status at this handoff

- Latest committed prior milestone: `d64765b`, smooth continuous mall spectator
  viewing; earlier simulation commit `7ef4015`.
- **The new twenty-session fleet is not launched yet.** No mall-compatible Laya
  text checkpoint has been trained. Do not report either as already running.
- The revised kit/chest/resource fixture, growth reward, population learning
  and fleet supervisor are implemented in the worktree. **63 focused tests
  currently pass** across mall, growth, fleet, viewer, quad and saved-world behavior.
- `mall-growth-pilot-20261003-a` initialized with the exact smaller kit and
  preserved source save. Live evidence covers manufacturing extra AM1s, funded
  cell expansion, mining stone/coal and smelting steel. AM2 manufacture is in
  progress; AM2 and fast-inserter bot upgrades still need live verification.
- The population learner uses seeded exploratory policies and measured growth
  rewards. Training a Laya text checkpoint is separately gated on qualifying
  observed episodes and independent scenario groups; no checkpoint is claimed.
- Next: finish live hardware/reset verification, freeze verified code/mod copies,
  then launch and verify all twenty hosts under the bounded supervisor.
- Uncommitted core changes are active work, not ready for another agent to
  deploy, revert, format or independently commit.

## Files reserved for the active mall agent

Currently editing:

- `training/laya_mall.py`
- `training/laya_mall_supply.py`
- `training/laya_mall_world.py`
- `training/laya_mall_world.lua`
- `planners/laya_quad_cell.py`
- `planners/plan_validation.py` (scoped training-fuel opt-in; production remains electric-only)
- `training/laya_saved_world.py` (validator dispatch, furnace configuration and transaction evidence)
- `schemas/laya_mall_world.schema.json`
- `tests/test_laya_mall.py`

Also reserve for the rest of this task:

- `tools/run_laya_mall.py`
- `training/laya_mall_growth.py` (growth reward/measurement)
- `training/laya_mall_population.py` (exploratory learning/selection)
- `tools/run_laya_mall_fleet.py` (twenty-session supervisor)
- `tests/test_laya_mall_growth.py`, `tests/test_laya_mall_fleet.py`
- `tests/test_quad_mall.py`
- `docs/rl/Laya_Mall_Supply.md`
- this handoff file
- the new mall-training run document/artifacts created by this task

`CURRENT_STATUS.md` is shared: this agent appends milestone entries and stages
only its own entry. Other agents should preserve existing entries and coordinate
before editing the same trailing section.

The mall agent reads/reuses `tools/run_laya_saved_world.py`,
`training/laya_saved_world.py`, `training/laya_input_profile.py`,
`training/laya_dataset.py`, `training/laya_finetune.py`,
`planners/quad_mall_layout.py`, `planners/plan_validation.py`,
`orchestrator/game_bridge.py` and native server management scripts.
Coordinate before changing those shared interfaces. No deployed-mod change is
currently planned; helpers under `training/` are RCON-owned code.

## Runtime and artifact ownership

Leave these roots/processes/ports alone:

| Purpose | Root | Game / RCON |
|---|---|---|
| Existing completed-mall viewer | `data/laya-saved-world/mall-view-20261003-a/` | 34196 / 27016 |
| New growth pilot | `data/laya-saved-world/mall-growth-pilot-20261003-a/` | 34220 / 27120 |
| Planned twenty training hosts | new mall fleet-owned roots | 34200–34219 / 27100–27119 |

Both existing hosts were running at this handoff. The viewer's duration is
bounded; its server/GUI may remain available after the clock pauses. Do not
stop/restart them or reuse their ports without coordination. Fleet evidence,
frozen code and manifests will identify the exact twenty-session allocation
when launched; only that supervisor owns their cleanup.

Preserve `saves/mod_playground.zip`: it was already dirty before this work and
remains the immutable source for private copies. Verified SHA256:
`9763436071d2ad138e2844a81c7bbd6891218f612c9bf69dc5bede6517c6d5bb`.
Preserve all existing `data/laya-saved-world/mall-*` evidence, including stopped
failed pilots. Existing worker 05/14 and separately managed operations consoles
are outside this task's planned allocation.

## Useful independent work

Other agents can work on deterministic planner issues, documentation or UI
outside these files, independent analyses, and unrelated tests. Existing dirty
changes in `factorio_mod/`, `orchestrator/bootstrap_steam_power.py`, observer UI,
old training-run documents and miscellaneous untracked audits are **not owned
by this mall task**. Their owners should preserve each other's changes.

Use a separate branch/worktree for shared-file work. Do not run `git reset`,
`git clean`, broad formatting, broad `git add`, mod deployment or shared server
lifecycle commands against this workspace. Make scoped commits for your own
verified work; coordinate before altering shared interfaces or runtime copies.
