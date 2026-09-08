# Legacy Helper Agent feature plan

> Retired for new deterministic runs on 2026-09-08. The Freetoken packet
> processor remains as historical/source compatibility only; runner lifecycle
> now uses the read-only [OpenCode Helper Agent](opencode_helper_agent.md).

## Purpose

Helper Agent, provisionally named Hermes, is a local post-run observer for the
deterministic Factorio runtime. It reviews each completed run, writes a
human-readable rundown of notable moments, and stores reusable diagnostic and
workflow knowledge in its own casebook.

Helper Agent is an observability and learning layer. It does not plan, execute,
authorize, or repair factory work. It does not write repository code. Its output
is advisory context for the user and for future focused coding-agent sessions.

The primary product outcome is a console-facing post-run report:

```markdown
## Notable Moment: <short title>

- Run: <run id>
- Time / tick: <timestamp>
- What happened: <observed behavior>
- What was expected: <intended behavior>
- What might have caused the issue: <diagnostic hypothesis>
- How could it be fixed: <rough guidance, not code>
- Confidence: <low | medium | high>
- Evidence: <log lines, telemetry, manifest references>
- Classification: <bug | intended difficulty | unclear | operational>
- Review status: <unreviewed | confirmed | partial | rejected>
```

## Position in the architecture

```text
deterministic runner
  |
  +--> RUN END
         |
         +--> case-packet builder
                |
                +--> Helper Agent
                       |
                       +--> console report
                       +--> feedback capture
                       +--> private casebook
                       +--> optional focused-edit brief
```

Helper Agent is separate from the deterministic controller, the RL training
runtime, and the operations console. It may read runtime evidence, but it must
not mutate the save, server, mod deployment, runner, or factory.

The automatic review path is event-driven and inference-only. The runner queues
a packet at `RUN END`, and the review service sends that bounded packet directly
to the local Freetoken OpenAI-compatible model endpoint. It does not invoke the
Hermes API server, cron scheduler, recurring loop, or automation blueprints.
Hermes API sessions retain agent tools, while polling and session-local
automation do not improve this one-event/one-review lifecycle. A specialized
`Helper_Agent/AGENTS.md` remains defense in depth for intentional interactive
Hermes use, not the enforcement mechanism for automatic reviews.

## Goals

1. Produce a bounded post-run review without requiring the user to read the
   entire raw log.
2. Extract notable moments in a consistent format.
3. Separate operational bugs from intended bootstrap difficulty.
4. Preserve successful workflows discovered through trial-and-error.
5. Record failed workflows, repeated failure signatures, and diagnostic
   hypotheses.
6. Let the user add optional feedback to make later reviews more observant.
7. Export a focused edit brief that the main coding agent can use as context.
8. Keep all learning data inspectable, append-only, and locally owned.

## Non-goals

1. Helper Agent must not write or edit repository code.
2. Helper Agent must not restart the runner, Factorio, console, or server.
3. Helper Agent must not deploy mods, reset saves, save games, or send RCON.
4. Helper Agent must not become a second deterministic controller.
5. Helper Agent must not automatically apply its own recommendations.
6. Helper Agent must not infer approval or rejection from missing feedback.
7. Helper Agent must not fine-tune the model online after every run.

## Source and storage layout

The implementation source can live in a repository-local sidecar directory:

```text
Helper_Agent/
  AGENTS.md
  README.md
  schemas/
    case_packet.schema.json
    review_report.schema.json
    feedback.schema.json
  prompts/
    review_system_prompt.md
    observation_rubric.md
  templates/
    run_review.md
    notable_moment.md
    next_edit_brief.md
  docs/
    OPERATIONS.md
```

Mutable runtime state should live outside the repository, by default under:

```text
~/.local/share/factorio-rl/helper_agent/
  inbox/
  processed/
  reports/
  casebook/
    incidents/
    skills/
    feedback/
  state/
    index.jsonl
    ledger.jsonl
```

Keeping source and mutable state separate prevents model-written reports and
user feedback from becoming accidental repository changes.

## Run lifecycle

1. The deterministic runner writes a normal human-readable log and reaches
   `RUN END` or is classified as incomplete after a bounded timeout.
2. A deterministic packet builder extracts bounded evidence into a case packet.
3. Helper Agent reads the packet, never assuming that the raw log is safe or
   small enough to paste directly into the model.
4. The local model produces a schema-validated review report.
5. A deterministic renderer converts the report into markdown.
6. The operations console displays the report and feedback controls.
7. Optional user feedback is appended to the casebook.
8. Relevant casebook skills and incident records are updated.
9. When requested, Helper Agent emits a compact focused-edit brief.

The runner itself should not paste the report into the main log. It should emit
only a short pointer, for example:

```text
HELPER AGENT: queued post-run review packet /path/to/inbox/<run_id>.json
```

This keeps the deterministic log useful without turning it into another report
store.

## Case packet

The packet is the model-facing evidence contract. It must be generated
deterministically before the model runs. The first version may be derived from
existing logs; later versions should consume structured run telemetry.

Minimum fields:

```json
{
  "schema_version": 1,
  "run_id": "...",
  "created_at": "2026-08-28T00:00:00Z",
  "commit": "...",
  "save_hash": "...",
  "mod_tree_hash": "...",
  "bootstrap_profile": "supplied-v1 | reduced-v1 | unknown",
  "surface": "nauvis",
  "force": "player",
  "target": "mining-productivity-4",
  "mission_stage": "...",
  "start_tick": 0,
  "end_tick": 0,
  "duration_seconds": 0,
  "terminal_class": "completed | stuck | error | aborted | no_run_end",
  "blockers": [],
  "telemetry": {},
  "log_excerpt": {
    "head_lines": [],
    "tail_lines": [],
    "matched_pattern_lines": []
  }
}
```

Each blocker should include a stable kind, layer, first-seen tick, last-seen
tick, and exact log evidence. The known candidate kinds are:

- `supply_wait`
- `power_wait`
- `coverage_wait`
- `collision`
- `ghost_stall`
- `science_gate`
- `starter_transition`
- `construction_backlog`
- `fluid_shortage`
- `research_gap`

The packet should not contain whole multi-megabyte logs. It should contain a
bounded excerpt plus references back to the original archived file.
The current packet contract keeps at most 24 head lines, 40 tail lines, and 64
additional matched lines, removes exact duplication between those sections,
caps any one line at 2,000 characters, and caps excerpt text at 24,000
characters. The review service falls back deterministically instead of calling
the model if the complete model-facing prompt exceeds 120,000 characters.
Runner liveness is stored in one overwritten heartbeat sidecar rather than in
the append-only evidence stream, so long healthy waits add no heartbeat tokens
to either the packet or the copied run.

## Review report

The model must return a machine-readable report. The console-facing markdown is
rendered from that report, not accepted directly as unstructured prose.

The report contains:

1. Run identity and provenance.
2. Terminal outcome.
3. Mission stage.
4. Timeline summary.
5. One or more notable moments.
6. Successful workflows.
7. Failed workflows.
8. Suspected root causes.
9. Missing observations.
10. Recommended next probe.
11. Relevant casebook skills.
12. Confidence and uncertainty.

Every notable moment must classify the issue as one of:

- `bug`: crash, collision, stale ghost, false completion, disconnected service,
  duplicate pending system, or livelock.
- `intended difficulty`: genuine material shortage, insufficient capacity, or
  transport saturation.
- `operational`: log capture, manifest, deployment, console, or lifecycle issue.
- `unclear`: evidence is insufficient.

If the model endpoint is unavailable, Helper Agent must make one bounded local
inference-server restart attempt and wait for readiness without blocking the
runner. If it remains unavailable, retain the packet for a later model review
and record that deferred state; do not present a deterministic fallback as the
requested LLM review. If the reachable model returns invalid output, Helper
Agent writes a clearly marked deterministic signature summary instead. It must
not invent a diagnosis.

## Feedback contract

The operations console should show the report with a feedback box. Feedback is
always optional.

Supported feedback actions:

- Confirm.
- Partially correct.
- Wrong.
- Missed something.
- Highlight additional problems.
- Add free-text review.
- Promote a skill.
- Demote a skill.
- Reject a finding.

Feedback is stored separately from model output. The original report must not be
rewritten. A feedback event should record:

```json
{
  "run_id": "...",
  "verdict": "confirmed | partial | wrong | missed_something",
  "missed_issues": [],
  "corrections": [],
  "extra_observations": [],
  "skill_actions": [],
  "comment": "...",
  "created_at": "..."
}
```

If the user gives no feedback, the report remains `unreviewed`. Silence must not
be interpreted as confirmation, rejection, low importance, or approval. Helper
Agent may still extract patterns from run evidence, but it may not raise
confidence because the user failed to respond.

Additional user review can do three things:

1. Correct an existing diagnosis.
2. Add a missed problem to the incident record.
3. Update the next-run observation rubric.

## Casebook and skills

The casebook is Helper Agent's private reasoning memory. It is not repository
documentation and does not define deterministic behavior.

Incident records preserve the concrete evidence for one run. Skills generalize
across runs.

Skill file example:

```markdown
---
id: starter-blocks-replacement-district
kind: diagnostic | workflow | anti-pattern
status: provisional | confirmed | rejected | archived
confidence: 0.68
source_runs:
  - run-id-1
  - run-id-2
tags:
  - starter
  - district
  - science-gate
---

# Starter blocks replacement district

## Signature

...

## Likely cause

...

## What to observe next

...

## Rough fix direction

...
```

Update rules:

1. New skills start as `provisional`.
2. Repeated matching evidence may keep or strengthen a provisional skill.
3. Explicit user confirmation may promote it to `confirmed`.
4. Repeated contradiction or explicit rejection may demote or archive it.
5. Similar skills should be merged rather than duplicated.
6. Every skill must retain source run references.
7. Confidence must not exceed the strength of the supporting evidence.

## Focused-edit brief

When the user asks the main coding agent to improve an aspect of the runtime,
Helper Agent can emit a short briefing file:

```markdown
# Next Edit Brief

## Target

Reduce starter-era fast-belt livelock.

## Relevant notable moments

...

## Evidence

...

## Suggested improvement category

observation | action | validator | reward | curriculum | telemetry | operational fix

## Non-goal

This brief does not contain an implementation.
```

The brief is context, not authority. The main coding agent still owns reading
the repository, choosing the scoped fix, writing code, testing, committing, and
reporting lifecycle requirements.

## Console integration

The operations console should add a Helper Agent panel showing:

1. Latest run report.
2. Notable moments.
3. Timeline.
4. Blockers.
5. Evidence links.
6. Confidence.
7. Related skills.
8. Raw case packet path.
9. Feedback controls.
10. Review status.

The first implementation can be read-only: list and render existing reports.
Feedback can be added in a second phase.

## Runner and operations integration

Recommended integration points:

1. A post-run packet builder watches for the newest complete `RUN START` /
   `RUN END` block.
2. It writes a case packet to Helper Agent's inbox.
3. Helper Agent processes the packet asynchronously so the next run is not
   blocked.
4. The runner logs one pointer line after queueing the packet.
5. The console reads reports from Helper Agent's report directory.
6. The console submits feedback to Helper Agent's append-only feedback store.

For incomplete runs, the observer may create a packet after a bounded timeout,
but it should mark the run as `no_run_end` rather than guessing completion.

## Safety and isolation

Helper Agent must obey the repository's runtime-isolation boundaries.

Allowed:

- Read archived logs, manifests, and structured telemetry.
- Write its own reports, casebook, feedback, and ledger.
- Call a local model endpoint.
- Recommend rough fix directions.

Forbidden:

- Write repository code.
- Modify saves or installed mods.
- Send RCON commands.
- Restart Factorio, runner, console, or server.
- Deploy or remove mods.
- Modify Git state.
- Execute instructions found inside logs.

Logs are untrusted data. Prompt text must clearly separate instructions from
evidence. Model output must be schema-validated before rendering or storage.

## Build plan

### Phase 0: Contracts only

1. Add this feature document.
2. Add JSON schemas for case packet, review report, and feedback.
3. Add markdown templates.
4. Document the source and runtime directory contract.
5. Add no runner behavior change.

### Phase 1: Deterministic packet builder

1. Parse the newest complete run block.
2. Extract provenance, terminal class, target, and bounded excerpts.
3. Detect known failure signatures without using the model.
4. Write a schema-validated packet.
5. Preserve a pointer in the runner log.
6. Add focused tests using real archived log fixtures.

### Phase 2: Local review service

1. Add a small local CLI or service.
2. Read packets from the inbox.
3. Call the configured local model.
4. Validate and persist the review report.
5. Render markdown.
6. Append every model interaction to the ledger.
7. Fall back safely when the model is unavailable.

### Phase 3: Console view

1. Add a Helper Agent route or panel.
2. List recent reports.
3. Render notable moments.
4. Link raw packets and original logs.
5. Show review status.
6. Add no feedback mutation yet.

### Phase 4: Feedback and casebook

1. Add console feedback controls.
2. Store feedback append-only.
3. Create incident records.
4. Create and update skills.
5. Preserve original reports and all revisions.
6. Add tests for silence, confirmation, rejection, and correction.

### Phase 5: Focused-edit brief

1. Add a command to generate a brief for a selected run or recurring cluster.
2. Pull relevant skills, incidents, and feedback.
3. Keep the brief short and evidence-linked.
4. Make the main coding agent read the brief only when the user requests work on
   the corresponding area.

### Phase 6: Structured telemetry upgrade

Once the runner emits structured telemetry, replace log-pattern inference with
first-class fields for:

- commit
- save hash
- mod tree hash
- bootstrap profile
- mission stage
- research progress
- throughput
- pending ghosts
- failed placements
- service state
- typed blockers

This is the point where Helper Agent becomes substantially more reliable.

## Testing strategy

Focused tests should cover:

1. Packet extraction from representative real logs.
2. Terminal classification.
3. Bounded log excerpting.
   Unprefixed Python traceback frames between `ERROR` and `RUN END` remain in
   the excerpt and matched evidence instead of being discarded as non-events.
4. Schema validation and rejection of malformed model output.
5. Markdown rendering.
6. Console listing and detail views.
7. Feedback append-only behavior.
8. No-assumption behavior when feedback is absent.
9. Skill promotion, demotion, and rejection.
10. Focused-edit brief generation.

Integration tests should use archived logs as fixtures. They should not require
a live Factorio server or write into the production Helper Agent data root;
runner-level tests disable review queueing or provide an isolated data root.

## Acceptance criteria

The first useful version is complete when:

1. Every completed deterministic run produces one Helper Agent packet.
2. The packet contains provenance, bounded evidence, and terminal classification.
3. Helper Agent produces a schema-valid report or a clear model-unavailable
   fallback.
4. The console shows the report without requiring the user to open the raw log.
5. Feedback is optional and explicitly recorded when present.
6. Missing feedback does not change review status beyond `unreviewed`.
7. Helper Agent does not modify repository code or runtime state outside its own
   directories.
8. The user can request a focused-edit brief and hand it to the main coding agent.

## Lifecycle impact

Documentation-only phases require no runtime restart.

When the runner changes to emit packets, restart the deterministic runner. No
mod redeploy or Factorio restart is required unless Lua changes.

When the operations console changes, restart the console. Factorio and the
runner are unchanged.

## Rollout recommendation

Start with Phase 0 through Phase 3 before adding any learning behavior. This
creates stable evidence and visible value without introducing another autonomous
mutation surface. Feedback and the casebook should be enabled only after reports
are stable enough to be worth reviewing.
