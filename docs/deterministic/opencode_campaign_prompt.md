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

Observation timing and recording:
- Never sleep longer than 120 seconds while observing.
- Prefer 30-60 seconds while construction is active; use 120 seconds only for a
  measured slow phase.
- After each wait, check runner status plus only new log output. If nothing
  changed, continue the bounded loop without reprinting old output.
- Stop waiting immediately on RUN END, STUCK, ERROR, research completion, or a
  user-visible invalid layout.
- Separate observation from interpretation. Record timestamped facts, deltas,
  and evidence references; label hypotheses explicitly. Never convert
  "unknown" into zero, and never infer adequate supply from aggregate stock
  (total, accessible, and allocated stock are different claims).
- Summaries report milestone transitions, newly blocked dependencies, changes
  in production/delivery rates, and contradictions. Repeated unchanged
  observations become one interval with a repetition count.
- Capture a blocked dependency whole: recipe and required quantities,
  requester contents, machine input/output inventories, assembler/inserter
  status, power, transferable stock, reservations, ghost backlog, and
  craft/delivery deltas.

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
   - observed failure;
   - causal hypothesis;
   - supporting evidence;
   - competing explanation;
   - smallest reusable fix;
   - predicted measurable result.
   If the evidence cannot distinguish the explanations, request the specific
   missing observation instead of guessing.
5. Prefer a better observation, rate model, action, validator, or planner
   primitive. Do not accumulate recipe-, coordinate-, or run-specific branches.
6. Inspect the preserved failed episode before resetting, with read-only
   probes where possible. A telemetry-only rerun is allowed only when the
   missing evidence requires execution — specify what each possible result
   would mean first.
7. Verify the failure mechanism locally before another full episode: prefer a
   focused behavioral reproduction, then the predicted milestone under
   matching starting conditions. Periodically confirm improvements on another
   scenario.
8. Run focused tests. Run the full suite only at a durable milestone or before a
   commit.
9. Perform only the lifecycle action required by the changed files.
10. Run once, observe, update the journal, and compare against all three
    references below.

When a clean reset, deployment, server restart, and runner start are all
required, use one bounded tool call instead of issuing six separate calls:

  scripts/manage_linux_deterministic_campaign.sh cycle \
    --source-save ~/.factorio/saves/mod_playground.zip \
    --python "$PWD/.venv/bin/python" \
    --technology mining-productivity-4

Use `--dry-run` first whenever the resolved paths or target are uncertain. Do
not use `cycle` when only a Python runner restart is required.

Trend gates and comparison:
- Compare every run against three references: the preceding run, the best
  verified milestone run, and previous runs with the same failure mechanism.
  A changed terminal item alone does not establish a different cause.
- Classify each outcome separately: factory improvement, useful diagnostic
  evidence, regression, or inconclusive. Longer survival and larger
  inventories do not establish factory improvement.
- Test count, elapsed time, or "the runner stayed alive" alone do not prove an
  improvement.
- If the same normalized failure occurs twice, do not launch a third retry until
  the observation or design changes materially.
- If two consecutive runs are worse, stop the campaign loop, audit the last two
  patches, and propose simplification or rollback before continuing.
- Keep one active root cause. Do not patch several speculative causes between
  runs.
- No code change is required every cycle. A cycle may end with a diagnosis, a
  rejected hypothesis, or an unresolved evidence request. Never manufacture
  changes just to unlock another run.
- One journal entry per episode, with exact provenance: episode ID, commit,
  dirty-patch hash, save/configuration/profile, tests, and activated code.

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
