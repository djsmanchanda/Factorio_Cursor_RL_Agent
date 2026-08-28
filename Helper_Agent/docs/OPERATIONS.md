# Helper Agent operations

Helper Agent is repository source plus a mutable local data root.  Default data
root is `~/.local/share/factorio-rl/helper_agent/`.  Create a copy of
`config/agent.toml` only if the local model endpoint changes; the service reads
the repository default otherwise.

The deterministic runner writes a bounded case packet after `RUN END`, then
starts a review processor automatically. A systemd-managed runner creates an
independent transient user service so collection of the runner service cannot
kill the review; a direct runner uses a detached child process. Concurrent
processors share a filesystem lock, so each queued packet is reviewed once
without blocking the runner. Processor output is appended to
`state/processor.log`.

Run the processor manually only to recover an older queue or diagnose startup:

```bash
python3 -m helper_agent.cli process
```

The command reviews every packet currently in the inbox. The repository default
uses the local Freetoken OpenAI-compatible endpoint on port 1919 and supplies the
complete review-report JSON schema to the model. If that endpoint is unavailable
or returns invalid output, Helper Agent writes a deterministic signature fallback
report and marks the model review as missing. It never blocks or restarts the
runner.

The operations console exposes the latest report and optional feedback controls.
Original reports are not rewritten by feedback.  Feedback is appended to
`casebook/feedback/` and can promote, demote, or reject a named casebook skill.

To generate a focused-edit brief for one run:

```bash
python3 -m helper_agent.cli brief --run-id RUN_ID \
  --target "Reduce starter-era supply livelock" \
  --category validator
```

The brief is written under `reports/` and is advisory context for a future main
coding-agent session.
