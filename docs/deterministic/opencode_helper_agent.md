<!-- Path: docs/deterministic/opencode_helper_agent.md | Purpose: Define the permanent per-run OpenCode Helper contract. -->

# OpenCode Helper Agent

The permanent OpenCode Helper replaces the legacy one-shot post-run Helper
packet processor for deterministic runs. It is a read-only observer, not a
planner or coding agent.

## Lifecycle

1. `tools/autonomous_run.py` emits `RUN START` and launches one independent
   OpenCode Helper process for that run.
2. The helper creates one OpenCode session and one findings document at
   `docs/deterministic/opencode_helper_runs/<episode-id>/findings.md`.
3. Every minute it sends that same session only new runner-log output,
   read-only manifest facts, and the loopback logistic-inventory snapshot.
4. When it sees `RUN END`, it steers the same session with the terminal log and
   requests a final wrap-up: outcome, timeline, inventory/mall state, code
   correlations, comparison to the preceding helper report, recent commit
   history, and a structured coding handoff.
5. After the final document is written, the helper appends a short absolute
   findings path to `autonomous-run.log`:

   ```text
   OPENCODE HELPER: findings complete /absolute/path/findings.md (run directory /absolute/path)
   ```

The report and its OpenCode transcripts are separate: findings live under the
repository documentation path for convenient review; mutable state and raw
transcripts live under `~/.local/share/factorio-rl/opencode_helper/runs/`.

## Boundaries

- The helper may read runner logs, the episode manifest, and
  `http://127.0.0.1:9137/api/logistic-inventory` only.
- It must not edit code, commit, deploy, restart, reset, issue RCON, or mutate
  the factory.
- It makes no run-continuation or code-fix decision. Its final handoff is
  evidence for a later coding task.
- One run gets one session and one findings document. A helper restart resumes
  the saved session for that same run.
- A failed or timed-out OpenCode request is resumed with a `continue` prompt up
  to three times. After the third retry the helper stops and appends that
  explicit failure to the runner log; it never starts a replacement session.
- Helper prompts prohibit sleep commands longer than 90 seconds.

## Operations

The runner starts this automatically. For diagnosis or recovery only:

```bash
.venv/bin/python -m tools.opencode_helper_agent \
  --run-id <episode-id> \
  --log-path ~/.local/share/factorio-rl/deterministic/logs/autonomous-run.log \
  --episode-manifest ~/.local/share/factorio-rl/deterministic/episode/current.json
```

Python/controller changes require a new runner process to load this lifecycle;
no mod deployment or Factorio restart is required.
