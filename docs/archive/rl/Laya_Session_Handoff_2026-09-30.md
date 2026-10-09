<!-- Path: docs/archive/rl/Laya_Session_Handoff_2026-09-30.md | Purpose: Handoff of the 2026-09-29/30 Laya workflow-training session: what exists, what was learned, what runs, what is next. -->

# Laya session handoff (2026-09-29 → 2026-09-30)

## Objective

Replace the brittle deterministic runner with **Laya**, a local decision maker
that learns the deterministic workflow's decision menu from **measured
outcomes** in disposable Factorio worlds. It should not imitate the old cascade
or rely on hand-written exceptions. The menu is hierarchical: what to build or
serve next, whether to expand, which variant, and when to wait. It grows level
by level toward the full workflow: furnaces, mall, power, logistics, oil,
circuits and science. Promotion to the real base still requires held-out wins
against the deterministic baseline (see `docs/system_invariants.md`).

## Where it stands

- **Learning from outcomes works.** On 32 held-out seeds per level, the learned
  policies beat the scripted rollout. Cost is completion ticks, plus 10 ticks per
  material unit, plus 60 ticks per decision.

  | Policy | L1 completed / median | L2 completed / median |
  |---|---|---|
  | Scripted rollout (baseline) | 32/32 · 6,854 | 28/32 · 8,093 |
  | Features policy (round 20–28) | 32/32 · ~6,630, faster on 32/32 | 27–28/32 · ~7,570, faster on 31–32/32 |
  | **Laya `laya-overnight-r26-min19`** (most robust) | 32/32 · 6,167, faster on 32/32 | 28/32 · 7,496, faster on 27/32 |
  | Laya `laya-overnight-r28` (best medians, less robust) | 28/32 · 6,148 | 27/32 · 6,492 |

- **Best artefacts:**
  - Laya checkpoint: `data/laya-checkpoints/laya-overnight-r26-min19`.
  - Features policy: the latest `data/laya-pi/features-ov-r28.json`.
- **Live run:** started 2026-09-30 12:29 for 8 h, ends ~20:30. It is at round 29
  on workers 01–04 and 06–13. Results go to `data/laya-pi/overnight/summary.log`.
  The driver stops its workers at the end.
- **Demo worker 14:** running, carries the latest lab mod, and is used for
  observed demos.

## What is running right now (snapshot 2026-09-30 17:03)

| Process | What it is doing | Stops by itself? |
|---|---|---|
| Driver `scripts/laya_overnight.sh 8.0` (PID 502102, started 12:29, code 1de07ac) | Round 29, level 2, seeds 6505–6540, continued with `features-ov-r28.json`. Next: features retrain → held-out eval → round 30 (L1) → Laya fine-tune on rounds 23–30 → … | Yes, ~20:30: final evaluation, then stops workers 01–04 and 06–13 |
| 3 labeller processes (`laya_counterfactual_labels.py`, stepped, speed 32) | p1 finished (159 labels). p0 and p2 are labelling slow level-2 seeds; labels print per finished seed | Part of the round |
| 12 training workers (01–04, 06–13) | Paused-clock episodes for labelling and evaluation (older lab mod: no level 3 or Coach interface) | Stopped by the driver at the end |
| Demo worker 14 | Latest lab mod (level 3 and Coach worlds list). User is connected as LeonidasUIP. A 5-episode Laya demo finished: 3 completed, 2 under-built and ran out of time | **No:** stop with `scripts/manage_linux_training_worker.sh stop --worker-count 20 --only 14` |
| Laya Operations Console (PID 501158, `:9138`) | Read-only status and controls; started with `LAYA_FINETUNE_TIMEOUT=240m` | **No:** a normal Python server; kill it or let a reboot end it |
| GPU | Idle between fine-tunes (limit ~79 W since the reboot) | – |

No hourly monitoring or scheduled checks are active. Nothing else is chained
after this run.

## What was built (commits, oldest first)

| Commit | What |
|---|---|
| c8b5b8f | Multi-worker batches/labelling (`--instance` repeatable, `--slots` per instance); policy-iteration labels (learned continuation) and a 60-tick per-decision cost |
| 7f51f6b | **Stepped mode:** one episode per instance with a paused clock and exact `ticks_to_run` waits. Replay noise dropped from 137–800 ticks to 0–50 |
| 7fbdcad | Workflow **level 2**: random belt target 0.08–0.70/s and an optional `iron2` expansion branch |
| 2ed47e6 | Features policy sees the demand (target, delivered/target, per-item rates) |
| e72468c | Lab rate window fixed so branch closure no longer depends on sampling phase. Features set 3 adds time and wait streak. `scripts/laya_overnight.sh` and `scripts/laya_policy_iteration_round.sh` |
| 35623ed | Driver can resume a run and train Laya on selected rounds (`LAYA_MIN_CASE_ROUND`) |
| f0d7f61 | **Key fix:** labels and live runs now share the same guideline rules (they had 1 vs 7). The offline evaluator respects option order |
| 00a5586 | Unique final-evaluation run ids |
| 1c0252c | **Laya Operations Console** on `:9138` (status, results, logs, start / stop-after-round / stop-now) plus `LAYA_RUN_NAME` / `LAYA_PREFIX` and a stop-request file |
| 11557de | Workflow **level 3**: copper → cable → circuits competing with belts for iron; multi-product objective; belt target 0.06–0.19/s |
| 1de07ac | `LAYA_FINETUNE_TIMEOUT` knob |
| 880efd8 | **Laya Coach window** lists training worlds with View / Leave (lab remote interface); `--observe` demo runs |

## What was learned (keep these)

1. **8× real-time labels are latency-coupled.** Stepped mode is required:
   at 16× results are distorted, and the policies learned from 8× labels loop
   on 1 s waits.
2. **One-step labels teach wait loops.** Policy iteration fixes this: label
   Q-values under the learned policy, so endless waits show up as timeouts. So
   does giving the features policy the elapsed time and the wait streak.
3. **Measurement artefacts become policy.** The ≤600-tick window trimming made
   branch closure depend on sampling phase, and Laya learned to exploit it.
4. **Train/live input mismatch kills an LLM policy.** Laya scored 52–57% top-1
   offline but looped live, until labels carried the same guideline context as
   live runs. Any future change to state rendering must apply to both.
5. **Laya fine-tunes on all rounds regress.** A sliding window of recent rounds
   (`LAYA_MIN_CASE_ROUND`, ~6–8 rounds) is stable and fits the GPU budget.
6. **Laya under-builds on high-demand seeds.** It picks basic tiers, then times
   out. That is the current main weakness.
7. **The GPU power limit comes from the laptop's thermal management**
   (hp-thermal-coordinator, nvidia-powerd). It was 20 W before the reboot and
   ~79 W after. Under the cap a fine-tune needs a ≥240 m limit.

## How to operate

- **Console:**
  `LAYA_FINETUNE_TIMEOUT=240m .venv/bin/python tools/laya_console_server.py --port 9138`
  and open http://127.0.0.1:9138/. "Start run" auto-resumes from the newest
  `features-ov-rN.json`. Use min Laya case round ≈ newest round − 7.
- **Settings:** stable settings are 12 instances (01–04, 06–13), 3 labeller
  processes, speed 32, stepped. The user asked for slow and stable, not maximum
  load.
- **Pause / resume:** send SIGSTOP / SIGCONT to the driver, all its descendants
  (the trainer runs under `timeout` in its own process group) and the Factorio
  PIDs. Never `pkill -f` with a pattern that also matches your own shell.
- **Clean stop:** use the console's "Stop now", then kill any `train_laya_4060`
  child, un-pause the instances and stop the workers.
- **Watching in Factorio:**
  1. Join `127.0.0.1:35014` (demo worker), then open Laya Coach → Training worlds → View.
  2. Run the demo with `tools/run_laya_workflow_batch.py --instance factorio-training-linux-14 --slots 1 --level 2 --policy laya --epsilon 0 --checkpoint <ckpt> --game-speed 2 --observe --observer-name LeonidasUIP ...`.
  3. The decision feed is `tools/watch_laya_run.py`.
- **Deploying the lab mod:** it goes only to stopped workers. The fleet (01–13)
  still runs the lab mod from before level 3 and the Coach remote interface.
  Redeploy after the live run ends. The GUI client then matches all workers
  again.

## Next steps (in priority order)

1. **Adopt level 3 in training.** After the live run ends:
   1. Redeploy the lab mod to 01–04 and 06–13.
   2. Add level 3 to the driver's level rotation (currently odd rounds → L2,
      even rounds → L1).
   3. Add a level-3 rollout baseline.
2. **Fix the level-3 `iron2` layout.** It feeds the gear assembler, so it does
   not raise belt capacity (measured ~0.20/s with or without it). Build a
   level-3-only cell that feeds the shared plate chest, so expansion becomes a
   real choice.
3. **Laya robustness on high-demand seeds.** Weight labels where tier or
   expansion choices have large cost gaps, and/or add the features policy's
   choices as distillation targets. Keep the sliding window. Evaluate offline
   (`tools/evaluate_laya.py`) before live runs.
4. **Deepen the menu toward the real workflow:** scale choices (iron3, gears2,
   furnace blocks ×6), power and roboport coverage branches competing for build
   materials, then a mall (batch vs. dedicated line). Each should be a new level
   with a live-verified layout, the same pipeline and hash-pinned earlier levels.
5. **Throughput.** Level-2 rounds take ~60 min, limited by Python per labeller.
   More processes per instance group, or batched Laya inference, would roughly
   double rounds per night at the same settings.
6. **Toward replacement.** Build a scenario matched to the deterministic
   runner's real opening, so Laya and the deterministic runner can be compared
   head to head (promotion gate).

## Housekeeping

- `CURRENT_STATUS.md` has entries for 2026-09-29. They are uncommitted, because
  the file also holds other agents' pending lines.
- Level-3 subagent notes and probes live in `/tmp` and `data/`; `data/` is
  gitignored.
- Superseded labels are kept but unused:
  - `s1` and `pi1` (8×, pre-fix);
  - rounds r1–r11 labels, whose states lack the 7 guideline rules. Laya trains
    on rounds ≥ 12 only.
