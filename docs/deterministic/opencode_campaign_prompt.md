<!-- Path: docs/deterministic/opencode_campaign_prompt.md | Purpose: Restart-safe prompt and loop for long autonomous debugging campaigns. -->

# OpenCode deterministic campaign prompt

Paste the prompt below into a fresh session. Replace only the mission target if
needed. It deliberately grants bounded authority over the isolated deterministic
runtime; it does not authorize changes to the source save, GUI session, training
workers, or any other runtime.

## Prompt

```text
Continue the isolated deterministic Factorio campaign for
`mining-productivity-4` until the live acceptance criteria below pass.

This is a long-running, restart-safe task. Heavy tool use and token use are
acceptable, but work efficiently: gather evidence in batches, make one coherent
change per failure class, and avoid repeating a run that cannot produce new
evidence.

Read before acting:
- AGENTS.md
- docs/system_invariants.md
- docs/factorio_operations.md
- docs/deterministic/README.md
- docs/deterministic/planning.md
- docs/deterministic/opencode_campaign_prompt.md
- the generated last-ten-runs journal described below
- git status, recent git log, and the latest complete autonomous-run block

Authority and safety:
- You may inspect the repository, logs, isolated deterministic server, and
  read-only RCON state.
- You may edit repository Python/tests/docs and restart the Python runner when
  required by a verified Python change.
- You may start or stop the isolated deterministic runner.
- Before reset, mod deployment, Factorio restart, save replacement, raw mutating
  Lua, or starting a fresh autonomous retry, state the exact action and why it is
  required. The campaign authorization covers only the isolated deterministic
  copy under ~/.local/share/factorio-rl/deterministic and only through the
  repository managers. Never touch the source save or training runtimes.
- Never manually place, move, or insert factory items to rescue a run. Fix the
  observation, planner, validator, or executor that caused the failure.

Crash/restart protocol:
1. Treat a user message saying `continue` as a request to resume from durable
   evidence, not to restart the reasoning from memory.
2. Re-read the last-ten-runs journal, git status, the newest run block, and the
   most recent focused test result.
3. Identify the last completed lifecycle action. Do not repeat deploy/reset/run
   commands merely because the previous model response was interrupted.
4. Write a concise checkpoint after every completed run so another session can
   resume without the transcript.

Observation timing:
- Never sleep longer than 120 seconds while observing.
- Prefer 30-60 seconds while construction is active; use 120 seconds only for a
  measured slow phase.
- After each wait, check runner status plus only new log output. If nothing
  changed, continue the bounded loop without reprinting old output.
- Stop waiting immediately on RUN END, STUCK, ERROR, research completion, or a
  user-visible invalid layout.

Run journal:
- Keep at most the latest ten runs in:
  ~/.local/share/factorio-rl/deterministic/logs/last-10-runs.md
- Keep manual notes in:
  ~/.local/share/factorio-rl/deterministic/logs/run-journal-notes.json
- Refresh it after every run with:

  .venv/bin/python -m tools.deterministic_run_journal \
    --log ~/.local/share/factorio-rl/deterministic/logs/autonomous-run.log \
    --archive-dir ~/.local/share/factorio-rl/deterministic/logs/archive \
    --output ~/.local/share/factorio-rl/deterministic/logs/last-10-runs.md \
    --state ~/.local/share/factorio-rl/deterministic/logs/run-journal-notes.json \
    --limit 10

- Then run it a second time with `--change "..." --lesson "..."` to record what
  the run tested and whether the evidence was better, worse, flat, or mixed.
- Never reconstruct run chronology from memory when the journal or raw logs are
  available.

Failure loop:
1. Capture endpoint, server root, source revision, deployed-mod provenance,
   surface=nauvis, force=player, and exact run start.
2. Read the first hard failure, not the final repeated symptom.
3. Trace observation -> decision -> plan -> execution -> outcome.
4. Before editing, state:
   - verified evidence;
   - root cause;
   - reusable invariant being violated;
   - smallest coherent fix;
   - focused test that will fail before and pass after.
5. Prefer a better observation, rate model, action, validator, or planner
   primitive. Do not accumulate recipe-, coordinate-, or run-specific branches.
6. Run focused tests. Run the full suite only at a durable milestone or before a
   commit.
7. Perform only the lifecycle action required by the changed files.
8. Run once, observe, update the journal, and compare against the previous run.

When a clean reset, deployment, server restart, and runner start are all
required, use one bounded tool call instead of issuing six separate calls:

  scripts/manage_linux_deterministic_campaign.sh cycle \
    --source-save ~/.factorio/saves/mod_playground.zip \
    --python "$PWD/.venv/bin/python" \
    --technology mining-productivity-4

Use `--dry-run` first whenever the resolved paths or target are uncertain. Do
not use `cycle` when only a Python runner restart is required.

Trend gates:
- A run is better only when it reaches new live evidence, completes the mission,
  or removes the prior failure without losing an earlier milestone.
- Test count, elapsed time, or "the runner stayed alive" alone do not prove an
  improvement.
- If the same normalized failure occurs twice, do not launch a third retry until
  the observation or design changes materially.
- If two consecutive runs are worse, stop the campaign loop, audit the last two
  patches, and propose simplification or rollback before continuing.
- Keep one active root cause. Do not patch several speculative causes between
  runs.

Planning invariants:
- Size production from measured input/output rates, not machine counts alone.
- Repair existing starved production before adding capacity.
- A branched belt must include a splitter and an explicit throughput budget.
- Plan machines, transport, power, logistics, and expansion space together.
- Disconnected logistic networks are a planning failure; do not script-teleport
  inventory between them.
- Existing infrastructure is authoritative. Route around it or fail safely.

Context discipline:
- Keep commentary concise: current run, new evidence, next decision.
- Do not repeatedly narrate unchanged waits.
- After every three runs, reread the ten-run journal and summarize which failure
  classes disappeared, persisted, or regressed.
- Keep durable facts in the journal and CURRENT_STATUS.md only at real
  milestones; do not rely on the conversation as memory.
- If a fix needs more than roughly 200 net lines, pause and explain why a small
  reusable refactor is preferable to another recovery branch.

Live acceptance criteria:
- The selected research is queued or completed on nauvis/player.
- Research progress is observed increasing, or completion is reported.
- Required science production is live, powered, supplied, and connected.
- No duplicate starved refinery rows were opened.
- Furnace demand does not exceed measured ore delivery.
- No manual item relocation or hidden production inputs were used.
- Highest evidence reached and required lifecycle action are recorded.

Commit only verified, coherent milestones. Preserve unrelated worktree changes.
When complete, report outcome, live evidence, tests, commits, and whether any
runner/server/GUI lifecycle action remains.
```

## Why this loop is faster

The model can still use as many probes and tokens as needed, but it avoids the
three expensive patterns from the prior campaign: five-minute blind waits,
full-suite validation after every small edit, and repeated retries that reach
the same failure. The one-call campaign manager also replaces the repetitive
stop/deploy/reset/start command chain. The journal carries evidence across
model crashes and fresh sessions without relying on a long transcript.
