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
   verified `mining-productivity-4` research goal. Natural episodes have no total tick, wall or
   decision-count cap. A hard tooling failure ends the run; complete stalls require an unchanged
   useful-state observation window (construction, required inputs, loan crafts and research).
   Explicit experiment caps remain available when separately requested. Runner crashes also get indexed terminal evidence.
4. During active play, checkpoints append bounded status/log snapshots. They do not call models.
   After the natural run ends, invoke exactly one terminal read-only Muse helper through
   `laya_helper_agent.py`: `opencode-go/muse-spark-1.3-contributor`, variant `xhigh`.
   The controller supplies an explicit config, report/transport directories and prior-run comparators.
   Root arguments are translated to one structured `comparators.json` file for the helper API.
   Reports and machine completion state live under `episodes/<id>/observer/`. Muse has reader-only
   tools; it returns delimited report text which the wrapper validates and publishes.
5. Once terminal evidence is recorded, stop only that episode's owned disposable Laya server.
   With a complete observer report and remaining campaign time, the persistent DeepSeek primary
   synthesizes/reviews and the persistent fixer implements **one focused fix** using
   `opencode-go/deepseek-v4.1-flash`, variant `high`. Neither model owns lifecycle or Git commits.
6. A code behavior change must declare `regression: tests/test_file.py::test_name` and include
   its test file in `files`. The controller exports committed HEAD into a temporary directory,
   overlays the new test, requires that exact test to fail before and pass in the candidate, then
   runs the requested fast gate. Collection errors, interrupts, process failures and timeouts fail
   verification. Only the exact pre-existing import-firewall node is tolerated by the gate:
   `tests/test_training_isolation.py::test_current_repository_obeys_import_firewall`, and only
   while an independent scan of the candidate tree finds no import violation outside the four
   confirmed baseline strings (`KNOWN_IMPORT_FIREWALL_VIOLATIONS`); the node id alone is not enough.
   There is no CLI allowlist expansion. A new module's regression should fail as a test against
   the earlier behavior, rather than fail collection because its import is absent.
7. Commit only verified declared files and clean campaign-owned status records. On rejection,
   archive and restore declared tracked candidate paths; preserve pre-existing and unknown work.
   Fresh episodes always export committed HEAD, so rejected dirt never becomes deployed source.
   Durable committing/cycle records prevent repeating a successful commit after a restart.

A failed observer receives one bounded re-collection of evidence for the same episode, then
`HARNESS_FAILURE` stops new episodes at the unchanged revision. Check-ins must not resume that
deliberate stop. Gameplay activity and completed observer/fixer/test/gate/commit stages are distinct;
placeholders are never completed reports. An interrupted terminal invocation is preserved.
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
  --speed 2 --technology mining-productivity-4 \
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
It starts no new episode or fix after that deadline. A run started before the deadline finishes on its
verified goal, hard failure or measured complete stall, followed by terminal observer evidence, even if that extends beyond the campaign window.
The controller then stops its owned server and exits. Terminal observer waits are bounded; before
expiry they use the remaining campaign time, and after a natural run crossing expiry they use the
configured bounded terminal-evidence allowance. Timed-out model process groups are cleaned up.

Do **not** use a finite `RuntimeMaxSec` on this natural-run campaign: it would impose an
unrequested episode cutoff. The persisted controller deadline is the eight-hour boundary for
starting new episodes/fixes, not a kill deadline for the last active episode.
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
RuntimeMaxSec=infinity
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

A verified goal completion in the owned terminal index, plus completed observer evidence, ends
the campaign successfully without a speculative fixer or another fresh world. C0–C6 and rate
proxies remain observations; research queued alone is not completion.

Before an unattended launch, verify installed OpenCode permissions with disposable fixtures and
exercise the complete controller cycle. The October10 round2 evidence is in the local
data/live-fixes/codex_helper_runs/20261010-round2/: Muse published a preserved-run report through
the wrapper; DeepSeek edited scoped source and was denied outside/.git/lifecycle operations.

## DeepSeek / Claude Haiku six-hour profile (October 10, 2026)

The user-requested `--agent-topology deepseek-haiku` profile supersedes the
older model-role topology above for this campaign. Its owning entrypoint is
`python -m tools.campaign_pingpong`, using the existing orchestrator,
`campaign_fix_flow` and `campaign_runtime` state machine. It requires the one
branch `t3/laya-deepseek-haiku-6h`, an explicit writable campaign worktree,
`opencode-go/deepseek-v4.1-flash/high` for observer/planner and
`claude-haiku-5-5/xhigh` through the first-party Claude CLI for implementation.
There is no Muse or additional model team.

Before launch, send exactly `Hi` through OpenCode, persist its **returned**
session ID immediately, then initialize instructions using `run --session ID`.
The profile refuses to create a replacement planner session. Save the original
launch time, launch + six-hour deadline, exact routes and planner ID in
`<campaign>/campaign.json`; the controller validates them on every restart.
Store all evidence under `data/laya-experiments/deepseek-haiku-<timestamp>/`.

Each implementation cycle begins a new Claude `-p --model claude-haiku-5-5
--effort xhigh` invocation without a session or worktree flag. Its returned
real ID is persisted as soon as the transport emits it; corrections use only
`--resume ID`. Saved native transcripts verify actual effort/model/provider.
Only the controller stages/commits. Haiku receives the saved planner ID,
findings, previous change/outcome, plan/prediction and verification contracts.
Run-start messages request acknowledgement only, without active gameplay
evidence. The user requests completed-run reviews; neither DeepSeek nor the
owning thread inspects active gameplay. Automated controller status snapshots
remain lifecycle evidence, with no live model review.

Every completion packet is relayed verbatim through OpenCode to that saved ID,
with acknowledged delivery recorded before review. The subsequent run
notification adds its verified commit and frozen run identity to the same ID.

The exact fast gate remains `pytest tests -m fast -k
"not test_runner_records_controller"`. Independently run baseline verification
before launch; never enlarge the fixed known-failure set. Each behavior needs
its own focused test that fails against committed HEAD and passes in the
candidate. Multiple behaviors declare comma-separated regression node IDs.

Use disposable private ports, save roots and a campaign-local stamping cache;
never deploy to the production server or GUI profile. Episodes retain the
natural useful-state contract and verified completed research target. No new
run starts at/after the original deadline, including after preparation. No
new edits/commits/Haiku start at/after it. An active natural episode and final
DeepSeek observations may finish later. Reserve the final 30 minutes for one
frozen validation; final findings receive no subsequent fix. Unfinished
candidates are copied to `unverified-candidates/` and remain explicitly
unverified. A failed bounded candidate does not trigger repeated unchanged runs.

Run under user systemd with `Restart=on-failure`, `KillMode=process` and
`RuntimeMaxSec=infinity`; restarts retain the same episode/session/deadline.
The adapter records every allocated episode and harness failure and publishes
`final-report.md`, `run-records.json`, ledgers, transcripts, diffs and gates.
GPT checks at approximately launch +2h/+4h/+6h, appends short supervision
notes, and intervenes only for broken/stopped/misrouted/protocol-violating
infrastructure. No automatic merge, push, adoption or real-base promotion.

Terminal prose is preserved before machine-envelope validation. If DeepSeek
renders the six synthesis fields as Markdown, the controller asks the same
session once for only the exact plain envelope; it never repeats the audit
merely to reformat it. All six nonempty fields remain required. OpenCode
session exports stream to a regular file and must parse as JSON before
replacing a previous valid export (pipe exports can truncate at64KiB).
A recorded operational recovery retains all failure records; `recovery.json`
sets the prior-failure count so the repaired controller gets at most three
new recovery attempts, without changing deadline/session/episode identity.

## Authorized ten-hour successor (October 14, 2026)

The latest user instruction requests the same DeepSeek/Haiku loop for ten
hours after fixing the preparation failure. Use a fresh campaign state root
and returned DeepSeek bootstrap session, `--max-runtime-hours 10`, and an
immutable launch-plus-ten-hour deadline in both campaign.json and controller
state. The historical branch name still ends in `-6h`; it does not determine
this successor's duration. Never extend the completed campaign's deadline.
The final report names the configured duration.

The remaining profile contracts stay in force: fresh disposable worlds,
end-only model reviews, one focused verified fix per cycle, scoped commits,
private mod stamping, final thirty-minute validation, and no new work after
the original deadline. A natural episode already active can finish for
terminal evidence. Python-only preparation containment requires new frozen
runners; it grants no production or GUI lifecycle authority.


## Authorized eight-hour follow-up (October 14 evening, 2026)

The latest user request starts another eight hours after the small evidence and
retry fixes. Use a fresh state root/session, `--max-runtime-hours 8`, and
immutable launch-plus-eight-hour campaign/controller deadlines. Retain the
end-only review, private-world, verification, final-validation and natural
active-episode contracts above. Prior deadlines are not extended. The branch
name remains historical; the new record defines this campaign's duration.

Terminal index lookup keeps the latest32whole JSONL rows rather than a byte
tail; a large work queue must not make a completed result appear missing.
Queue promotion changes priority, while its existing retry interval remains;
reconciliation still wakes work as soon as relevant evidence changes.
