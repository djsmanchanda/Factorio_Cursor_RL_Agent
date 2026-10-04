<!-- Path: docs/rl/Laya_Mechanics_Matrix_2026-10-04.md | Purpose: Follow-on six-family experiment queue, explicit interventions, observer cadence and isolation. -->

# Laya mechanics and context matrix

The user authorized many small variations across all six proposed test families
and continued execution beyond the preceding eight-hour block. This finite queue
has **554 trials**, **90 variant configurations plus baseline**, three matched
policy seeds per arm, and a **24-hour hard lifetime cap**. It stops when the queue
finishes or reaches its cap. It does not automatically promote a variant.

| Family | Trials | Small variations |
|---|---:|---|
| Earned C1 checkpoint creation | 8 | Previously successful baseline seeds; fresh measured replay required; up to three distinct earned states assigned |
| C1 to C2 transition | 108 | Up to three independently earned C1 worlds; mall/corridor/all soft zoning, requester horizons 1/3/6, stall patience 24/48, double time window, combined context and a selected interaction |
| Context | 51 | Inventory-only policy-board ablation, bottleneck-only board, dependency graph, eight recent outcomes, combined view, guideline budgets 8/10/12, exploration 5%/30%, feedback weights 0.35/0.7/1.5 |
| Planning | 33 | Goal and upstream preference strength, construction-stock demand rates, dependency/stock-lock lookahead |
| Transport | 90 | Belt and bot controls; supply distance 6/15/30/45 tiles, robots 22/55/110/220/550, requester horizons 1/2/3/6/10, separation, machine ratios, input shares and selected distance/buffer/robot interactions |
| Plastic integration | 72 | Refinery and plant counts, coal shares, route separation, coal/crude interruptions and downstream sink backpressure of 10/30/60 game seconds, selected interactions |
| Recovery and sequencing | 192 | Declared missing belt/machine/pole, circuit belts/bots and plastic, context and exploration variants; repair versus wait versus additional unconnected capacity |

The baseline has its original soft preferences and hard validators. Preference
weight screens preserve other entries of the tested rule. Combined interventions
are explicitly named and are not interpreted as single-factor evidence.

## What this measures

This is guided-policy experimentation and fixture mechanics evidence. It is not
neural fine-tuning or evidence that a trained director understands a different
prompt. Context views affect documented guided-policy features and preserve
complete facts for the executor's safety, geometry and funding checks. Observation
enrichment without an active preference may legitimately produce no decision
change; the feedback-weight arms test whether using that feature changes outcomes.

Policy seeds vary decisions on the same source terrain. They do not establish
unseen-terrain generalization or held-out promotion. Raw runs, checkpoint
continuations and funded component fixtures remain separate report domains.

The first eight jobs try to earn C0/C1. Up to three distinct completing worlds are
assigned to the continuation groups; a failed preferred seed can use another
earned world, with the actual source recorded and fixed across its paired arms.
No completing world is reused as an independent checkpoint. Each checkpoint contains a paused
Factorio save, engine state, construction attribution, site plan and hash manifest.
Continuations use the exact save and phase provenance without item grants. If
checkpoint creation fails, its dependent trials are recorded as blocked setup,
excluded from policy comparisons and never replaced with a hand-built factory.

Component construction uses funded ghosts. The fixture declares raw-material
sources, construction/spare budgets, robot count, protected infrastructure and
every perturbation. Deliberate damage destroys only the declared non-fixture
production entity, with no refund. The policy sees repair, extra machine capacity
and wait options, plus bot-based ground cleanup when a damaged site has dropped
items. Cleanup orders construction-bot deconstruction in that site's small
footprint, moves real items into fixture storage and never grants items. Repair completes the recovery stage without requiring the
extra machine. It records every choice and engine outcome. Construction bots and
fixture power remain constant while logistic robot counts vary.

Supply interruptions and sink backpressure are armed after warmup, begin 60 game
seconds into a two-minute engine-counter measurement window, and last 10/30/60
seconds. A second two-minute window measures recovery. Restoring the declared
fixture source is environment behavior, not a learned repair. Crude interruption
may be absorbed by existing gas/pipe buffers; this is measured, not assumed.
Plastic is consumed by a declared sink fixture, not an earned downstream factory.

Metrics include completion, commissioning/first-output/repair time, engine
production/consumption, delivered output, recovery rate and construction choices.
Invalid, interrupted and operationally failed runs are excluded from usable paired
comparisons. All-failed arms supply failure evidence, never positive labels.

## Queue and observation

Run root: `data/laya-experiments/mechanics-matrix-20261004/`.
`queue.json` is the full manifest; `suite.json` records immutable code, copied
inputs, ports, services, authorization, start and deadline. `jobs.jsonl`,
`progress.json`, `analysis.json`, `checkin-*h.json` and `report.json` preserve results.
The queue begins only after the previous actor unit releases its workers. Eight
checkpoint jobs form the first dependency barrier; then families alternate so
all six receive early evidence. At most 12 queue jobs own endpoint leases.

Muse uses exactly **OpenCode Go Muse Spark 1.3 Contributor, xhigh**. It waits
**120 seconds after a review finishes**, so the actual cadence includes review
time, and processes two-hour checkpoint requests. It observes this full campaign
through cumulative case summaries, paired outcomes, selected trajectories and
previous saved findings. After eight successful reviews, a fresh session limits
conversation growth while preserving old session IDs, transcripts and cumulative
findings. File locks exclude duplicate observers; failures preserve transcripts,
remain pending and respect a cooldown. The model cannot edit, run shell/RCON,
delegate or mutate worlds. No post-deadline external model call is allowed.

## Isolation and lifecycle

The old campaign remains frozen and ends on its original deadline. The new actor
and observer use dedicated user-systemd units, control-group shutdown and an exact
remaining-window runtime cap. Queue endpoints are game **34400–34411**, RCON
**27300–27311**, all loopback. Probe endpoints are separately identified in their
manifests. Saves and mod copies are private; installed mods, real saves, clean
runner/mall/viewer servers and console 9140 are unchanged.

Scoped code: `tools/run_laya_mechanics_matrix.py`, `training/laya_checkpoint.py`,
component/context/helper modules and focused tests. Shared additive seams are
`training/laya_clean_runner.py`, `training/laya_guided_policy.py`,
`tools/run_laya_clean.py` and `tools/run_laya_context_suite.py`. Concurrent mall-slot
changes are excluded from this experiment's frozen implementation.

Required lifecycle: launch only the new queue/observer and its private workers.
Private lab copies include the new fixture code from startup. No installed mod
deployment or restart of another runtime is required.

## Verification and probe failures

54 focused Python tests and native Lua parsing cover matched controls, all physical
layouts, default behavior, context views, checkpoint integrity, timed source/sink
interruptions, scoped bot cleanup, observer locking and session rotation.

Live probes are preserved in `<run>/probe-evidence.json`. The initial circuit
probe exposed inappropriate internal-power compilation for a single-machine
repair. The next exposed an odd-sized machine snapping from integer coordinates,
and the third showed real dropped input items obstructing ghost revival. These
are retained failed probes, not usable policy wins. The execution seam now uses
the shared power alternatives, aligned coordinates, previously selected asynchronous
construction attribution, the executor's collision check and an offered scoped
bot-cleanup action. No live world was manually repaired to conceal those failures.

The verified circuit probe chose extra capacity, then bot cleanup and repair,
with **3,000 game ticks** from damage to repair, zero pending ghosts and no
infrastructure failure. Its deliberately constrained bot transport sustained
**0.75 circuits/s**, below the 3/s target. This is a successful recovery mechanics
check, not a throughput-target win. The plastic probe measured **1.9333/s** during
a 30-second crude interruption and **1.9833/s** in the following two-minute recovery
window, zero ghosts and no infrastructure failure.

The raw checkpoint smoke run reached C0, then stalled at 39 decisions with zero
execution failures and an input-starved gear chain. It exported no checkpoint,
as required. Checkpoint hash/continuation handling is unit-verified; a live earned
C1 export/continuation remains to be demonstrated by the queued measured runs.
