<!-- Path: docs/archive/rl/Laya_Current_Handoff_2026-09-21.md | Purpose: Preserve the historical September 21 stopped-state handoff; not current operational instructions. -->

# Laya current handoff — 2026-09-21

## Safe stopped state

- No `train_laya_4060.py`, resumable trainer, shadow runner, or training observer is active.
- Native disposable Factorio worker 01 is stopped.
- The incomplete guided-shadow process was terminated; no real Nauvis state was involved.
- The Coach mod was synchronized to worker 01 and `/home/djsm/.factorio/mods` before shutdown.
- The GUI Factorio client was not restarted by Codex; it must reload the mod before the **Laya Coach** button appears.

## Preserved model and evidence

- Durable checkpoint: `data/laya-checkpoints/laya-flex-v1`
- Training: 4 epochs, 44,288 microsteps, mean loss `0.9471603885`, BF16, compile + FlexAttention.
- Held-out accuracy: `0.6585603113`; test accuracy: `0.6129032258`; zero inference failures.
- Authority: disposable shadow only; real Nauvis promotion remains blocked.
- Completed overnight shadow: 280 episodes, 279 decisions, 224 selected/agreed, 55 escalations, one episode error, zero failed transitions.
- Partial pre-fix run: `data/laya-shadow/guided-shadow-20260921.jsonl` contains 24 decision records but no completion summary; preserve it as interrupted evidence.
- `data/laya-shadow/guided-shadow-20260921b.jsonl` was not successfully launched; `logs/laya-guided-shadow.log` remained empty.

## Implemented in the working tree

- Full typed workflow boundary: goal, operation, site, scale, route, research, upgrade, recovery, wait/act, and candidate decisions.
- `factorio_mod/laya_coach.lua`: in-game high-level guidance/observation window; it does not execute actions.
- `training/laya_guidance.py`: injects newest runtime/surface-scoped guidance per focus as soft goals/rules.
- `tools/import_laya_coach_data.py`: converts explicit human choices into future fine-tuning cases; guidance-only notes are never fabricated into labels.
- Disposable staged episodes now preserve available candidates, selected candidates, pre/post observations, execution, and outcome traces.
- Count-based shadow lifecycle bug fixed: without `--duration-hours`, `--count N --attempts-per-scenario M` now stops after `N*M` episodes. The previous implementation looped forever.

## Resume sequence for the next agent

The next agent owns these operations; do not hand shell commands back to the user.

1. Read `AGENTS.md`, `docs/system_invariants.md`, `docs/rl/training.md`, and `docs/factorio_operations.md`.
2. Start only native disposable worker 01 with one slot and verify explicit RCON `127.0.0.1:28001` using its worker-owned secret and a read-only tick probe.
3. Start the loopback training observer on port 8766 if the user wants the Observatory.
4. Launch the corrected ten-episode guided shadow as a persistent user-systemd service, not through a background process tied to a transient Codex command. Use:
   - workers: `training-workers-linux.json`
   - secret: `/home/djsm/.local/share/factorio-rl/training/01/worker/rcon-password`
   - checkpoint: `data/laya-checkpoints/laya-flex-v1`
   - base model: `data/laya-base`
   - output: `data/laya-shadow/guided-shadow-20260921b.jsonl`
   - log: `logs/laya-guided-shadow.log`
   - guidance: `/home/djsm/.local/share/factorio-rl/training/01/worker/script-output/factorio_mod/laya_guidance`
   - count 10, attempts per scenario 1.
5. Verify the service PID, first fresh decision, baseline-only execution authority, summary completion, and guidance-record count.
6. After the GUI client reloads the synchronized mod and writes guidance, verify the newest note is injected at the next decision. Do not require the user to label individual actions.
7. Collect workflow/staged traces, merge only explicit labels into a new dataset, and fine-tune a new checkpoint. `laya-flex-v1` remains mining-candidate-only.

## Important operational notes

- The first persistent run launched from the user's terminal was active as PID `462867`, but exposed the count-loop bug and was stopped after preserving its partial JSONL.
- A later `nohup` launched from an escalated Codex command returned PID `979602` but exited immediately with no log or output. Prefer user-systemd for the next persistent run.
- Repository `.git/index` is read-only in this environment; the Laya/workflow/Coach changes are uncommitted.
- Preserve all unrelated dirty worktree changes and runtime artifacts.
