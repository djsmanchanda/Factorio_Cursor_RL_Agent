<!-- Path: docs/deterministic/reliability_workflow.md | Purpose: Run repeatable plastic acceptance before expanding the research campaign. -->

# Plastic reliability workflow

Use the existing primary campaign controller in reliability mode. The secondary
helper stays read-only. The console supervisor keeps one primary controller alive; it does not create a competing controller.

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

## Operations Console: supervised 12-hour loop

The **Autonomous observation & fixes** panel provides Start, Resume and Stop.
Start creates a fixed 12-hour window with GLM-5.3 as the primary controller:
it observes the campaign, checks the shared evidence board, directs recovery,
reviews candidates, verifies tests, commits accepted fixes, records the
milestone, and starts the next fresh episode. Union Alpha performs all
read-only observations and the final focused repair. The window uses the
console's configured isolated runtime, save, ports and Python interpreter. It requires
Linux user systemd and OpenCode on the console's PATH with working model access.
The existing runner must be stopped before starting a new window.

The controller runs two campaign-persistent agent sessions and four per-run
observer sessions. `PRIMARY` (GLM) is bootstrapped once per campaign and the
same OpenCode session controls every checkpoint, run end, synthesis, review and
commit; controller restarts, run ends, worker failures and helper failures all
reuse it, and only a positively unrecoverable session is ever replaced.
`FINAL_FIXER` is likewise created once per campaign and receives each NEW
episode's evidence through the same session, so it retains prior fixes,
rejected hypotheses and review feedback (conversation context, never
authoritative evidence). Fresh episodes always receive a NEW four-observer
team (timeline, supply, comparison, falsifier) scoped to that episode's
directory; observers are read-only and never reset Factorio, start runs,
authorize fixes or decide campaign completion. The controller sends the
persistent sessions only concise event messages after bootstrap; ordinary
recovery resumes the same saved session with a minimal `continue`.

Four concurrent observers cover those roles with per-episode sessions and
findings. The shared board carries health, checkpoints, bounded excerpts and
links to full reports. Observer retry exhaustion is recoverable: after bounded
consecutive failures the same role/run gets a replacement session with a
handover of its durable findings instead of being disabled; a failed turn
never stops the run or parks the campaign. The permanent secondary helper
remains separate and its instructions are unchanged.

At run end the controller records the terminal tick and wall time, starts
observer finalization immediately, and concurrently gives the independent
OpenCode helper at most 120 seconds (`--helper-wait-seconds`) to finish. When
the helper window expires the controller records `helper_status` with a reason
and proceeds: missing helper findings never park the campaign, restart a run,
or block synthesis, fixing or the next episode. Findings that arrive after the
window are preserved as late evidence without rewinding the flow. The primary
then synthesizes terminal evidence (earliest divergence, first violated
invariant, first downstream consequence, terminal failure, and a structured
hypothesis block persisted to the hypothesis ledger), and the SAME persistent
fixer receives the synthesis, observer board, helper findings when present,
git evidence and the previous accepted fix, and implements ONE focused
evidence-backed correction (behavior fix, instrumentation-only change, or an
explicit no-change). The primary reviews the candidate in its own persistent
session and may reject with specific feedback; revisions return to the SAME
fixer session. The controller runs the mandatory cross-component gate declared
in `MANDATORY_CAMPAIGN_TESTS` plus changed regression files, and commits only
the fixer's declared files plus campaign-owned records.

Worktree ownership is explicit: runtime/generated artifacts (save zips,
helper findings, run logs, scratch), campaign-owned records (CURRENT_STATUS.md,
the observations journal) and the fixer's declared files are each staged
selectively; unrelated pre-existing user changes are preserved unstaged and
never swept into a campaign commit, and their existence never blocks a
targeted commit. A candidate that cannot be verified or reviewed within a
bounded revision budget (`--max-fix-revisions`) is abandoned with its clean
paths reverted and unknown paths quarantined, the failure is recorded in the
fix ledger, and the loop continues with the next fresh episode. A candidate is
never committed unverified, and an unverified partial edit never blocks the
next fresh lifecycle. The controller keeps an explicit state machine
(`STARTING_EPISODE`, `OBSERVING`, `RUN_ENDED`,
`WAITING_FOR_HELPER_AND_OBSERVERS`, `SYNTHESIZING`, `FIXING`, `PRIMARY_REVIEW`,
`VERIFYING`, `COMMITTING`) and records every completed cycle in a durable loop
log plus fix/hypothesis ledgers under `<state-root>/logs/campaign-loop/`;
terminal phases are `SUCCESS`, `DEADLINE_REACHED`, `MANUAL_STOP` and
`UNRECOVERABLE_FAILURE` only. Controller restarts resume from persisted state
without duplicating sessions, fixes, commits, resets or episodes.

A failed, timed-out, or empty model call is resumed through its saved session
with an explicit continue prompt until it responds or the fixed campaign
deadline expires. Each observer turn is bounded so a congested observer cannot
delay the primary's terminal handling; the next checkpoint resumes its same
session. A campaign-deadline exit preserves its episode and evidence.
Successful plastic acceptance runs repeat the same candidate; they do not
require an invented code change. The console displays acceptance streaks and
achieved milestone times; longer survival never counts as success.

The loop runs in a user systemd service independently of the browser and console
process. Process failures receive up to three bounded recovery attempts against
recorded controller ownership. Resume retains the original deadline and pending
work. Uncertain lifecycle state must be inspected; recovery never blindly repeats
a reset. A declined or incomplete fix retains the episode and original dirty-file
boundary; Resume returns to that pending record instead of running an unverified
partial edit. The displayed resolved-cycle count advances only after the cycle
reaches a verified change verdict or production acceptance.

Stop terminates the supervisor's controller and model processes. **The separately
managed Factorio runner and server remain running.** Their controls become
available after the loop releases ownership. Restarting the console itself does
not stop the supervised loop.

Window settings, controller state, progress, transport logs, journal and boards
live under `<state-root>/logs/campaign-supervisor/<window>/`. Existing compact
packets, history database and reliability scorecards remain the evidence sources.
The console reads bounded excerpts; agents can inspect full local files.

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
   always runs the mandatory cross-component gate declared in
   `MANDATORY_CAMPAIGN_TESTS`, then reruns changed regression files without a
   cost filter and checks the diff before committing only declared files.
   Production-only changes still run the mandatory gate. Verification records
   exact commands and return codes; an empty changed-test list cannot count as
   pytest success.
   Full slow/exhaustive sweeps remain milestone/manual verification.
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

Extraction also writes `REPLAY.json`, a diagnostic fixture contract containing
the world hash, episode/profile/source identity, deployed mod hashes and all
captured durable sidecars. Nested material, district and bootstrap-work stores
are included; top-level inventory snapshots are not substitutes for them.
Use `python -m tools.episode_checkpoint --replay <fixture>` to verify it.
Use `--baseline <fixture> --candidate <fixture> --destination <new-directory>`
to prepare paired inputs. Both sides must have identical world, provenance and
durable starting state; the episode sidecar may differ only in code/mod identity. Pair preparation does not launch Factorio or certify
an outcome; distinct candidate code must be selected by the diagnostic runner.
Fixtures and pairs explicitly set `acceptance_eligible: false`.

Keep fresh-start acceptance for every release candidate. A checkpoint test is
an earlier diagnostic gate, never proof that preceding bootstrap stages still
work. Develop later stages from separately preserved milestone worlds, then
exercise the same candidate from the pinned original source before promotion.

## Resume, budget and outputs

- `logs/reliability-campaign-state.json`: persistent primary/fixer session ids,
  observer episode, pending review, checkpoint monotonicity, helper status and
  the persisted deadline. Restarting resumes the remaining window; it does not
  grant another 12 hours. Use a new `--state-file` explicitly for a new
  campaign window.
- `logs/campaign-loop/`: durable per-cycle loop records, fix ledger,
  hypothesis ledger, per-cycle synthesis and git-evidence snapshots.
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
