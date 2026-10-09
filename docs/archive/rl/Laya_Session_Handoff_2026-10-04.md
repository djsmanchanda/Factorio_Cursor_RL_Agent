<!-- Path: docs/archive/rl/Laya_Session_Handoff_2026-10-04.md | Purpose: Handoff for the Laya clean runner after long smelting lines and the variant-experiment framework. -->
# Laya handoff, 2026-10-04

Branch `codex/deterministic-checkpoint-fleet`. All work is committed; nothing is running
except the Laya Operations Console (http://127.0.0.1:9140/, read-only between runs).
The mall RL agent's files, ports and servers were not touched.

## Commits this stretch

| Commit | What |
|---|---|
| `2c19cd4` | Saved-world executor accepts splitter `input_priority`/`output_priority` (shared adapter; additive one-line allow-list change). |
| `e0b7fcb` | Smelting lines offered only when fully in bot reach; roboports may stand in smelting strips; base = starter roboport; strips avoid a 24x24 starter area. Live mechanics check of one line. |
| `fd5cafd` | Coverage serves an ore only when none of it is in reach; `protect-goal-stock` rule + `starves_goal` feature. |
| `2edf90d` | Variant experiments (`training/laya_variants.py`, `tools/laya_experiment.py`, `docs/rl/laya_variants/`); runner `Settings`; variant/experiment in run index and ledger; roboports may stand on ore zones; coverage reach measured against planned ore zones. |
| `2046230` | `goal_reserve` (kit the unbuilt goal starters need) + `dips_goal_reserve` feature; variants `reserve-starter-kit`, `epsilon-05`. |
| `f20edea` | Options carry `draws` (requester requests x multiplier); scarce/starves/dips use bill + draws. |
| `1ed1db7` | `mission.inputs-first` rule (doc quote, no weight in baseline) + `inputs_missing` feature; variant `inputs-first`. |

## Long smelting lines (ready for the RL handler prompt)

`training/laya_lines.py`: one column of the approved Start/Middle/End units (basic variant),
6 furnaces (Start+End) or 18 (Start-Middle-Middle-End), fed by a paired-drill mine unit and
a corridor haul, with sized provider taps; growth is parallel lines at a 16-tile pitch.
Live mechanics check (`data/laya-saved-world/line-mechanics-20261004a`, fixture items, not
Laya evidence): 6-furnace iron line built by bots, 171 entities, 0 failed, 3.9 ore/s in,
3.75 plates/s out, all six furnaces working. 18-furnace lines pass offline checks only.
Not supported: lengthening a built line in place (the executor only places ghosts).

## Variant experiments

A variant is a JSON tweak to the catalog (`catalog` / `catalog_patch`), context settings,
policy (`epsilon`) or runtime (`speed`). `tools/laya_experiment.py run` runs it beside the
baseline on the same seeds in disposable worlds (ports 34240-34259 / 27140-27159, no helper)
over a window (`--until-phase`, `--max-game-ticks`) and writes
`data/laya-saved-world/exp-<id>/report.json`. Adoption rule: `docs/rl/laya_variants/README.md`.
Runs take ~3-5 min wall each at speed 4; 12 in parallel is fine on this machine.

| Experiment | Window | Result |
|---|---|---|
| `c0-goal-weight-20261004` | C0, 15 game min, 3 seeds | 0/6 for both: coverage could not cross the iron ore zone toward copper. Fixed in `2edf90d`. |
| `c0-goal-weight-20261004b` | same | baseline 2/3, goal-weight-10 3/3. |
| `c1-20261004` | C1, 60 game min, 4 seeds | baseline 2/4, goal-weight-10 2/4, reserve-starter-kit 2/4, epsilon-05 1/4. Cause: requester draws hidden. Fixed in `f20edea`. |
| `c1-20261004b` | same | baseline 3/4, reserve-starter-kit 3/4, goal-weight-10 1/4 (rejected). |
| `c1-inputs-first-20261004` | same, 6 seeds | baseline 4/6, inputs-first 2/6 (one run cut short by the stop). Not adopted. |

No variant has been adopted; the baseline catalog weights are unchanged except where an
observation was fixed (coverage reach, requester draws).

## Open problems, in order

1. **Bootstrap deadlock (main C1 failure).** An exploration pick (p ~0.07-0.09) builds the
   long-handed-inserter cell before gears are made; its requester (sections x15) locks the
   kit's inserters; gear, inserter and long-handed cells then all wait on each other.
   `inputs-first` (x0.1) did not help and did worse; check its runs before retrying. Likely
   needs a different lever: smaller first-cell request multipliers (shared mall code;
   coordinate), or a stock-chain order observation, rather than another weight.
2. **Coverage waste after a stall begins:** runs spend medium poles on untargeted hops.
3. **Lines in a real run** need belts, splitters and inserters the kit lacks; the mall must
   make them first (C2+), untested end to end.
4. Experiment stalls use the default 12-decision stall rule; long-window runs (C2+) have not
   been tried.

## How to resume

```bash
.venv/bin/python tools/laya_experiment.py report --experiment c1-inputs-first-20261004
.venv/bin/python tools/laya_experiment.py run --experiment <id> --variants docs/rl/laya_variants/<v>.json \
    --seeds 6 --until-phase C1 --max-game-ticks 216000 --speed 4 --parallel 12
```

Full live run (console): Fresh Episode on http://127.0.0.1:9140/, or
`tools/run_laya_clean.py prepare|run --root data/laya-saved-world/clean-<ts>`.
Watch completion with the run's own background task; do not use `pgrep -f <pattern>`
watchers (they match themselves).

Lifecycle: nothing to restart. Mods unchanged; experiment worlds are disposable
(`data/laya-saved-world/exp-*`, `line-mechanics-*`) and may be deleted when no longer needed.
