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
complete review-report JSON schema to the model. The request disables Qwen's
thinking channel through Freetoken's `chat_template_kwargs`; this bounded
classification task needs a complete schema response more than a long hidden
reasoning trace.

When port 1919 is unavailable, the detached review worker makes one
cooldown-guarded `ft serve` restart attempt, waits for `/health`, and retries the
review. If the model remains unavailable, the packet stays in `inbox/` and the
ledger records `model_review_deferred`; it is not converted into a final fallback
report. A later processor run can therefore produce the real review. Invalid or
schema-incompatible model responses still produce a clearly marked deterministic
fallback, because the model was reachable but did not satisfy the review
contract. This supervision never blocks or restarts the Factorio runner.

The supplied Qwen3.6 helper profile is intentionally small: one running request,
16K sequence cap, 4K prefill chunks, 8K KV reserve, and 1,536 output tokens.
It uses `offload` plus automatic MoE cache sizing, leaving the Mamba cache under
FreeToken's checkpoint/GPU auto-sizing rather than reserving an arbitrary large
pool. Packets are capped at 36K characters before the system rubric and JSON
schema are added, preserving room in the 16K window. Inspect `ft ctl stats` and
`ft ctl cache` after a real review before resizing any live cache pool.

## Why this is not a Hermes agent session or schedule

The automatic path deliberately calls the Freetoken inference endpoint rather
than the Hermes API server. Hermes API requests run a complete agent with its
terminal, file, web, memory, and skill tools; an added system message narrows the
task but does not remove that authority. Inference-only review makes the
non-mutation boundary structural instead of relying solely on prompt obedience.

Hermes cron, recurring loops, and automation blueprints are not used for normal
post-run processing. Cron polls on a schedule instead of reacting exactly to
`RUN END`; loops belong to an active session; blueprints install schedules only
after operator confirmation. The runner's event-driven queue is cheaper, more
precise, restart-independent, and already serialized by the processor lock.

`Helper_Agent/AGENTS.md` is defense in depth for an operator who intentionally
starts Hermes with `Helper_Agent/` as its workdir. It does not grant Hermes a
role in the automatic path and must not be used as a substitute for disabling
tools. Do not create a cron job, loop, or blueprint for Helper Agent without an
explicit operator request.

References:

- https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server
- https://hermes-agent.nousresearch.com/docs/user-guide/features/cron
- https://hermes-agent.nousresearch.com/docs/reference/automation-blueprints-catalog
- https://hermes-agent.nousresearch.com/docs/user-guide/features/loops

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
