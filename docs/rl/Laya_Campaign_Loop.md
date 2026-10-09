<!-- Path: docs/rl/Laya_Campaign_Loop.md | Purpose: Operate the isolated Laya improvement loop through the existing campaign state machine. -->

# Laya campaign loop

The existing OpenCode campaign controller owns durable phases, persistent primary/fixer sessions,
scoped Git commits, verification, ownership checks and resumption. `campaign_runtime.LayaRuntime`
replaces runtime-specific leaves; it does not introduce a separate decision loop or Laya geometry.
The supervisor runs from a committed, frozen source copy. `--campaign-worktree` explicitly identifies
its separate writable campaign branch, which Git, model edits and tests use.

## Cycle and evidence

1. Pin the source save under `baseline-inputs/<sha>.zip`. Allocate a unique campaign experiment id
   and persist its code/world/mod paths before starting lifecycle work. Actual Laya `run_id` differs
   from the campaign experiment id; the unique persisted root establishes ownership on resume.
2. Export the writable branch's committed HEAD with `git archive` into `episodes/<id>/code`.
   Build mods from that revision with `--mod-builder`, then stamp them using the configured
   `data/live-run-kit/` script **before** preparing the save. Write `freeze.json` atomically with
   revision, archive hash, source hash, mod hashes, private ports, paths, requested speed and budgets.
   Source files are read-only; generated evidence has dedicated mutable directories.
3. Prepare a fresh world beneath that frozen code's `data/laya-saved-world/<id>` to satisfy the
   saved-world ownership validator. Launch the frozen CLI with deterministic operations, speed 2,
   accepted C6 goal, 1,440,000 game-tick budget and a declared 21,600-second cooperative wall budget.
   Wall expiration stops after the current operation and writes a terminal reason; it does not
   kill Factorio or interrupt a submitted operation. Runner crashes also get indexed terminal evidence.
4. During active play, checkpoints append bounded status/log snapshots. They do not call models.
   After the natural run ends, invoke exactly one terminal read-only Muse helper through
   `laya_helper_agent.py`: `opencode-go/muse-spark-1.3-contributor`, variant `xhigh`.
   The controller supplies an explicit config, report/transport directories and prior-run comparators.
   Root arguments are translated to one structured `comparators.json` file for the helper API.
   Reports and machine completion state live under `episodes/<id>/observer/`.
5. Once terminal evidence is recorded, stop only that episode's owned disposable Laya server.
   With a complete observer report and remaining campaign time, the persistent DeepSeek primary
   synthesizes/reviews and the persistent fixer implements **one focused fix** using
   `opencode-go/deepseek-v4.1-flash`, variant `high`. Neither model owns lifecycle or Git commits.
6. A code behavior change must declare `regression: tests/test_file.py::test_name` and include
   its test file in `files`. The controller exports committed HEAD into a temporary directory,
   overlays the new test, requires that exact test to fail before and pass in the candidate, then
   runs the requested fast gate. Collection errors, interrupts, process failures and timeouts fail
   verification. Only the exact pre-existing import-firewall node is tolerated by the gate:
   `tests/test_training_isolation.py::test_current_repository_obeys_import_firewall`.
   There is no CLI allowlist expansion. A new module's regression should fail as a test against
   the earlier behavior, rather than fail collection because its import is absent.
7. Commit only verified declared files and clean campaign-owned status records. On rejection,
   archive and restore declared tracked candidate paths; preserve pre-existing and unknown work.
   Fresh episodes always export committed HEAD, so rejected dirt never becomes deployed source.
   Durable committing/cycle records prevent repeating a successful commit after a restart.

A failed observer produces a counted cycle with its evidence and no speculative fix. An interrupted
terminal invocation waits for its announced completion without starting a second observer. If still
incomplete, record the failure and rerun the committed revision in the next fresh episode.
An incomplete saved-world prepare is preserved for diagnosis; it is never automatically prepared twice.

## Invocation

The dual-runtime builder accepts `--source <frozen code>/factorio_mod --output <episode mods>`.
By default the adapter uses the builder inside the frozen revision when present and separately copies
that revision's training lab. `--mod-builder` overrides the builder executable if a kit-owned wrapper is needed.
The stamping script accepts `--server-mods`, `--gui-mods` and `--tag`; its tag base must leave
room within Factorio's 1..65535 patch-version range. Configure the builder for the current dual-mod
build, rather than passing an installed mods directory from an earlier revision.

```bash
<repo>/.venv/bin/python -m tools.opencode_campaign_orchestrator \
  --runtime laya --campaign-worktree <writable new campaign branch> \
  --state-root <campaign state> --source-save <kit source save> \
  --mod-builder <current builder or kit wrapper> \
  --stamp-script <repo>/data/live-run-kit/stamp_run_mods.py \
  --gui-mods <authorized GUI mods directory> --stamp-tag-base <unused tag> \
  --game-port <private game port> --rcon-port <private RCON port> \
  --python <repo>/.venv/bin/python --seed 50616 \
  --speed 2 --until-accepted C6 --max-game-ticks 1440000 --run-wall-seconds 21600 \
  --max-runtime-hours 8 --max-cycles 0 \
  --comparators <repo>/data/laya-experiments/live-pair-20261010d/code/data/laya-saved-world/live-pair-20261010d-s50616 \
  --observations <campaign state>/journal.md --progress-file <campaign state>/progress.json \
  --opencode-log-dir <campaign state>/agents
```

Run from the frozen supervisor source, using `PYTHONPATH` and `WorkingDirectory` for that source.
`--python` must remain the absolute repository venv path because the archive has no venv.
Default production ports 34199/27017 are refused in live Laya mode.
`--code-root`, `--world-root` and `--mods-source` are internal episode paths once a campaign episode
is allocated; each new episode derives fresh paths below the campaign state directory.

## Deadline, unit and resumption

The controller persists the original 8-hour deadline in
`logs/opencode-campaign-state.json`; restarting with the same state file never grants another 8 hours.
It starts no new episode or fix after that deadline. A run started before the deadline finishes its
natural budget and terminal observer evidence, even if that extends beyond the campaign window.
The controller then stops its owned server and exits. Terminal observer waits are bounded; before
expiry they use the remaining campaign time, and after a natural run crossing expiry they use the
configured bounded terminal-evidence allowance. Timed-out model process groups are cleaned up.

Do **not** use `RuntimeMaxSec=8h`, which could interrupt the active episode. The persisted controller
deadline is the 8-hour boundary. A 15-hour emergency unit ceiling covers 8h + one 6h run + evidence.
Use `KillMode=process` so a supervisor failure does not kill the isolated active episode; the restarted
controller adopts the same persisted root and PID instead of preparing another world.

```ini
[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=<frozen supervisor code>
WorkingDirectory=<frozen supervisor code>
ExecStart=<absolute invocation above>
Restart=on-failure
RestartSec=30
KillMode=process
RuntimeMaxSec=15h
```

Before launch/resume, inspect `systemctl --user list-units 'laya-*'` and persisted deadlines.
The user-authorized two-hour oversight must inspect new runs, findings and commits, rather than
unit liveness alone. Append progress to the campaign directory and `data/live-run-kit/next-round.md`.

## Verification level

Offline tests exercise two full adapter cycles with fake subprocess boundaries: physical source
archives, unique owned roots, current revision/mod identity, stamping order, observer configuration,
selective commit, original-deadline resumption, existing-PID launch recovery, and strict verification.
Runner tests cover cooperative wall stops, pinned provenance and crash indexing. These are unit/fake
integration evidence; they do not establish live gameplay improvement, mod join compatibility or
adoption eligibility. Verify those separately in the requested isolated live campaign.
