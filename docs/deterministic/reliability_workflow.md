<!-- Path: docs/deterministic/reliability_workflow.md | Purpose: Run repeatable plastic acceptance before expanding the research campaign. -->

# Plastic reliability workflow

Use the existing primary campaign controller in reliability mode. The secondary
helper stays read-only. No scheduler or second controller is required.

```bash
.venv/bin/python -m tools.opencode_campaign_orchestrator \
  --plastic-reliability --required-successes 3 --acceptance-seconds 120 \
  --stop-after-acceptance --checkpoint-failures \
  --source-save ~/.factorio/saves/mod_playground.zip \
  --state-root ~/.local/share/factorio-rl/deterministic \
  --rcon-port 27017 --max-runtime-hours 12
```

This command operates the isolated runtime: it starts fresh episodes using the
repository managers and saves failed-world checkpoints when requested. It does
not run against the GUI or training workers. Run it only within campaign
lifecycle authorization. Do not launch another primary controller alongside it.
An exclusive file lock rejects concurrent instances on the same state root.

## What it does

1. Pins an immutable hashed copy of the source save under `baseline-inputs/`.
   The original remains unchanged.
2. Starts fresh `produce plastic-bar` episodes. Historical research runs and
   checkpoint replays do not count toward the acceptance streak.
3. Once the builder returns a working output, observes the same recipe-matched
   producer cohort near that exact provider for 120 game seconds. Counters must
   advance on every sampled interval, with plastic present in the provider.
   Stock accumulation by itself cannot pass. A paused simulation or broken
   observation ends within a bounded timeout.
4. Rechecks the report's samples, target, episode and provenance. Requires three
   distinct successful fresh episodes with the same revision, code digest,
   source hash, mod hashes, profile and observation window. Failure or candidate
   change resets the streak. Successful runs repeat without requiring a fake fix.
5. On failure, archives the manifest/report/log/packet, optionally captures the
   failed world, then asks the fixer for one evidence-backed correction.
6. A separate read-only reviewer assesses the change. The primary controller
   runs the test suite and diff checks, then commits only the declared files.
   Pre-existing dirty files or staged work require review; they are not swept
   into an automatic commit. Failed verification or review parks the campaign
   with the candidate intact and a persisted pending-review record.
7. Records the certified revision and episode IDs as the last-known-good
   baseline. `--stop-after-acceptance` ends there. Without that flag, the next
   fresh episode targets the configured research technology. The existing
   research runner establishes science and queues research; its successful exit
   is not a claim that the technology finished researching.

Each record includes available milestone timings (foundation swaps, AM2, oil
packet and plastic output), acceptance outcome and candidate identity. Missing
milestone logs remain missing; no throughput or speed improvement is inferred.
Retained baseline revisions and pinned inputs provide explicit rollback targets;
this workflow does not automatically reset Git or overwrite user changes.

## Durable construction and loans

The opening oil district persists exact plans, ordered packets, fluid links,
coal route and service geometry before packet submission. A coverage/material
wait or process restart replays that same intent through normal validators and
material checks. It cannot silently choose another site after construction has
started. Corrupt identity/version/checksum fails closed. Deferred sulfur and
later remote crude expansion are separate stages, outside this opening intent.
Existing partial districts from before this feature cannot be reconstructed
retroactively; use a fresh episode for acceptance.

Completed loans already return through their normal recipe/requester restoration
path, including exact-target batches after demand retirement. The regression in
`tests/test_construction_handoffs.py` reproduces the circuit-producer starvation
without rerunning an hour of bootstrap. This remains part of candidate testing.

## Checkpoints and evidence

`--checkpoint-failures` requests a named Factorio server save after the runner
has stopped. It first pairs the RCON channel with the configured script-output
tree. The bundle stores the world ZIP, episode manifest, durable controller JSON,
oil transactions, events and blockers, with file hashes. It never substitutes
the initial baseline save for a failed world. If capture fails, no next reset is
issued automatically; the failed world remains available for inspection.

Extract a verified bundle into a new directory with:

```bash
.venv/bin/python -m tools.episode_checkpoint \
  --checkpoint <state-root>/checkpoints/<id> \
  --destination /tmp/factorio-replay-inputs
```

Extraction verifies hashes and refuses an existing destination. It does not
start Factorio or claim to restore arbitrary Python in-memory state. A live
replay needs a separately configured disposable runtime with these durable
sidecars and matching episode/mod provenance; do not feed the saved world alone
through ordinary fresh acceptance. Such replay is diagnostic, never one of the
three clean-start acceptance runs. Live capture/replay is not yet validated by
this implementation's offline tests.

## Resume, budget and outputs

- `logs/reliability-campaign-state.json`: active fixer session, pending review,
  completed cycles and persisted deadline. Restarting resumes the remaining
  window; it does not grant another 12 hours. Use a new `--state-file` explicitly
  for a new campaign window.
- `logs/reliability-state.json`: results, streak and certified baseline.
- `logs/reliability-runs/`: immutable per-episode evidence copies.
- `logs/production-acceptance.json`: current sampled evidence.
- `logs/oil-transactions/`: versioned construction intents.
- `checkpoints/`: failed-world bundles when enabled.
- `logs/latest-context.md` and `logs/run-history.sqlite`: concise handoff/search.

Existing same-failure guards remain active. Commands and waits respect the
persisted deadline; a budget exit does not imply the separately managed runner
or server has stopped. Inspect their status before the next lifecycle action.
The controller resumes unfinished review after interruption instead of starting
an unverified candidate. A commit interrupted before its completion record may
require explicit inspection; uncertain state fails closed.

For an offline command-generation check, use a separate temporary state root:

```bash
.venv/bin/python -m tools.opencode_campaign_orchestrator \
  --plastic-reliability --stop-after-acceptance --dry-run --max-cycles 1 \
  --state-root /tmp/factorio-reliability-preview \
  --observations /tmp/factorio-reliability-preview/observations.md
```

Activation: restart the primary campaign controller and use fresh runner
processes. No Lua change was made; no mod redeploy or Factorio restart is needed
solely to load this code. The chosen fresh-episode manager still performs its
normal isolated server lifecycle.
