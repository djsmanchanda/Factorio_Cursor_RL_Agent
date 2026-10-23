<!-- Path: docs/rl/Laya_Frontier_2026-10-23.md | Purpose: Preregister the bounded Starship high-risk experiment matrix and confirmation protocol. -->
# Starship frontier experiments, October 23, 2026

The user authorizes twelve hours of Starship experimentation, with **60 total
trials maximum per cohort**, and no subagents. The fixed window is October 23
15:51:47 IST through October 24 **03:51:47 IST**. Admission requires a full wall
allowance before 03:46:47; the final five minutes are reserved for owned-world
cleanup. Systemd enforces the hard end independently of T3 wakeups.

The experimental reference is `9ac7937`, recovered from Miniship thread
`cb16c604-339b-4b74-8f39-a19a263d9853`. It contains steel admission, physical loan
backing, research operations and recovery. These are common to every new arm;
this is **not adoption** of that combination and cannot isolate those earlier
changes. Miniship C22–C25 and its owner remain untouched. Starship uses a clean
implementation worktree and new committed freeze. The dirty root is preserved.

`tools/laya_frontier_plan.py` generates the exact variant JSONs and matched,
rotated-order job manifests. All candidates compare with their own cohort's
control, never an adjacent unrelated candidate. Each candidate changes one
parameter or one rule. Geometry, hard legality, funding, ownership, source save,
engine/mod set, RNG seeds, acceptance predicates and opportunity windows match
within a cohort. No neural model fitting occurs in these guided-policy trials.

| Cohort | Arms including control | Seeds per arm | Total | Experimental factors |
| --- | ---: | ---: | ---: | --- |
| Sampling | 5 | 12 (80600–80611) | 60 | epsilon .45; temperature 3; family-balanced exploration; binary novelty strength 4 |
| Capital | 5 | 12 (80700–80711) | 60 | mall-slot priors x180/x6 → x1; ready foundation x1 → x8; active-batch suppression x.05 → x1; steel x5 → x30 |
| Bottleneck | 5 | 12 (80800–80811) | 60 | deepest bottleneck x2 → x16; side-goal suppression x.2 → x1; unchanged-failure penalty removed; measured feedstock growth x4 → x16 |
| Research | 3 | 4 (80900–80903) | 12 | science-demand growth x2 control, x1.25, x4; all start at .25 with identical earned sustain requirements |

The three screening cohorts run in sequence, at most 18 worlds. Research has
at most six worlds in parallel so it can receive long windows. One fresh shared
registry and scheduler cap the whole window at 24, initially 20, with existing
memory/UPS/thermal derating. Other local Laya services must be absent at launch;
new campaigns must use this window's registry or wait. Runtime update-thread
settings remain the engine defaults, equal across arms. This is not a hardware
performance experiment. Each screen has 540,000 game ticks / 6,000 wall seconds;
research has 1,080,000 ticks / 15,000 wall seconds. Every trial enables the same
mining-productivity-4 objective. Research captures coherent generations;
nondefault sampling modes are rejected for checkpoint capture/resume because
recovery identity does not yet include those fields. No fresh-world retries are
silently treated as continuations.

A separate wiring cohort of at most 15 trials may exercise variant loading and
initial decisions before full admission. It is reported separately and does not
establish performance. No supplied-input fixture counts as factory-earned
research. The source save is the unchanged C24 canonical copy, SHA-256
`347eba65065014f82261330d84d5ca2fa3e3427cad3ab18dcfdf496dd9c442b0`.

## Endpoints and decisions

Primary screening endpoint: `laya-c6-choice-output-v2` per planned job. Research
primary: factory-earned requested-level completion under
`laya-research-consumption-v1`. Report proxy milestones, strict checkpoint
ever/final, C5 service, elapsed game/wall time and typed faults separately.
Unknown observations, failed prepares, crashes, cuts, caps, interruptions and
unstarted jobs remain in all planned denominators. Preserve attempt history.
Strict stages are sequential; losing starter-based C1 after valid retirement
is not itself earlier-service regression. Final C5 is not a sustain measurement.

These small, multiple-comparison screens are exploratory. Report paired
candidate-only/control-only counts, effect sizes and uncertainty; never adopt
from an interim lead or treat 12 policy seeds on one map as unseen-terrain proof.
Record actual intervention exposure: distribution changes, visible rules, the
number of research rungs advanced and whether the modified demand was reached.
An unexercised factor is inconclusive, even if aggregate results differ.

After all three screens terminate, the monitor may preregister one confirmation
cohort of **30 new seeds × 2 arms = 60 trials**, seeds 81000–81029, ports
39400–39417 / 31400–31417, same 540,000/6,000 windows. Select the candidate with
highest positive paired C6-v2 difference, provided C2/C3/C5 evidence shows no
clear earlier-service decline and there is no increase in safety/tool faults.
Break ties by C5-final difference, then lexicographic variant ID. Require at
least 10 fully observed pairs in its screen and nonzero intervention exposure.
Record every candidate's result before selection, exact tie-breaks, immutable
variant hash and selected contrast. Selection is exploratory and subject to
winner bias; confirmation uses unused seeds and cannot by itself prove broad
terrain generalization. If no candidate qualifies, keep the control and record
no confirmation. Never weaken gates merely to fill the schedule.

If the screens finish with sufficient time but no candidate qualifies, a
**60-trial A/A** (30 fresh seeds, identical reference arms) may instead check
runtime stochasticity. Label it A/A and preserve both arms. Do not make a
post-hoc change to the current source freeze. New fixes require focused checks,
the fast gate, a new commit/freeze and a separate <=60-trial cohort; never extend
the hard end. Apply the normal variant adoption rule after complete evidence.

## Ownership and lifecycle

Window state, freeze hashes, unit IDs, schedules and reports live under the main
checkout's `data/laya-orchestration/frontier-starship-20261023/`. A local dated
handoff points to them. T3 wakes perform bounded reads at 30-minute intervals,
then disable their own schedules after the hard end and publish terminal
accounting. Systemd runtime caps and cgroup cleanup do not depend on model auth.

Only private disposable copies are started. Their explicit scope is Nauvis/player
inside a verified disposable manifest, never the production server's Nauvis.
Both project mods are copied from the frozen source into those private worlds;
installed GUI/server mod copies and real saves are not touched. No user restart
is required. Further code changes need new frozen runners after their predecessor
finishes. The one implementation helper is an ordinary top-level T3 thread on
Personal_2 / GPT-6.1-Sol xhigh; no subagent harness is used.

## Capacity amendment, 2026-10-23 22:27 IST

The owner prioritized the existing 12-job report-freshness comparison after the
shared scheduler thermally derated from 14 to four slots. Idempotent admission
controls drained capital and bottleneck and cancelled only their queued jobs:
capital retained 44 completed and four running attempts, with 12 queued cuts;
bottleneck retained all 60 planned jobs as `cancelled_queued`, with zero starts.
All original planned denominators and append-only cut events remain intact.
No running attempt was interrupted. This is a capacity cut, not a selection
based on interim arm outcomes.

The bottleneck terminal cut releases the existing freshness dependency. At
verification all 12 freshness jobs remained queued, admission reason `ready`,
zero reserved; they can enter in their frozen rotated order as host slots free.
The automatic four-slot thermal gate remains authoritative. No manual target,
hardware control, source, variant, save, mod or manifest was changed. Rerun
check-in exited zero, with no freeze errors and unchanged frozen source.

The final admission time remains October 24 02:06:47 IST for the full 6,000-second
allowance, cohort end 03:46:47 and hard cleanup end 03:51:47. All 12 starts are
not guaranteed. Incomplete pairs, capacity cuts and unexercised bottleneck
factors remain explicit; no confirmation or adoption follows from interim
capital leads. The shared 55-minute observer and 03:53 terminal accounting stay
in place. Remote decision evidence is
`data/laya-orchestration/frontier-starship-20261023/operator-decisions/frontier-thermal-freshness-priority-20261023-v1.json`;
it binds before/after status, four accepted requests, cut receipts and unchanged
manifest/freeze/plan hashes.

At 22:30 IST the owner acknowledged the observer's separate host-only change,
made on explicit user instruction around 22:24: `temp_limit_c` 93 -> 96 and
`temp_hard_c` 95 -> 98. Readback against `config.pre-warm-20261023.json` verifies
that only those two settings differ. Retain the user-approved values; target
remains four with no manual override. This is distinct from the queue cuts.
The source helper `_package_temp_c` falls back to the hottest sysfs thermal
zone when AMD Tctl is absent. On Intel Starship its `temp_c` is not necessarily
the `coretemp` package reading; firmware TCPU and package sensors have different
reported trips. Preserve sensor provenance when interpreting peaks. No capacity
gain or sustained package-temperature conclusion follows from these spot samples.

At 22:34 IST readback acknowledged a second explicit user instruction reported
by the observer, given around 22:30: remove the scheduler thermal limit and set
12 servers. Current host config has `temp_limit_c=200`, `temp_hard_c=210`,
`manual_target=12` and previous target four; target status confirms 12 with no
expiry. The backup of 96/98 settings is `config.pre-nothermal-20261023.json`.
These settings supersede the preceding thermal-limit acknowledgement. Prior
controls remain in operational history. The owner made no further config or
target change on acknowledgement.

Observed ten active worlds: four capital plus six freshness, three per freshness
arm; six freshness jobs remain queued, zero completed pairs. The frozen freshness
cohort has concurrency six, so a host target of 12 does not by itself allow more
than six of its worlds concurrently. Keep that frozen cohort limit and original
paired order. Memory/liveness controls, 72 queued cuts, sources and deadlines
remain as recorded; do not label the target increase as a completed comparison
or a measured performance improvement. Sensor provenance caveat still applies.

## Original screens terminal and confirmation decision, 2026-10-23 23:51 IST

The user added ten host slots: target12->22, no expiry, within configured max24.
Live target readback confirms22 with six active freshness worlds; existing
200/210 thermal settings remain. No controller-thread notification was sent,
per the user's follow-up. Freshness's frozen concurrency6 is unchanged.

All original192 planned jobs are terminal: sampling60 completed, research12
completed, capital48 completed/12 cancelled_queued and bottleneck60 cancelled.
Capital control/mall-flat/foundation8/batch-free/steel30 C6-v2 counts are
5/6/6/4/7 of12 planned, known10/10/9/9/10 respectively. Candidate-only versus
control-only C6 pairs are2:1,3:1,2:2,3:1 with known pairs10/9/9/10. Sampling
candidates have negative paired C6 differences; bottleneck is unexercised.
Foundation8's+.2222 has only9 known pairs. Among candidates meeting the10-pair
primary gate, steel30 leads (+.20,3:1), ahead of mall-flat (+.10,2:1); no tie-break
is needed. Steel30 has actual exposure89, no observed earlier-service decline,
zero exceptions and8 typed bug outcomes versus control76. These are exploratory
screen results, not adoption or proof of a general speed improvement.

No confirmation or A/A is launched in this window. Even ignoring the six active
freshness worlds and any cohort ceiling,60 jobs with full6,000-second budgets
require at least16,364 seconds on22 slots, versus14,152 seconds remaining to
03:46:47 at decision time. Launch would rely on early terminations or leave an
underfilled confirmation. Preserve the declared windows and03:51:47 hard end.
Freshness continues:6 completed/6 running,3 complete pairs, C6-v2 tied2/6 each
(3 known each); research availability1876/1876 repair vs0/1615 control. Earned
research is unobserved, with control research acceptance unavailable. The repair
does not enter selection among sampling/prior candidates.

Remote `operator-decisions/confirmation-not-launched-20261023-v1.json` retains
all candidate contrasts, fault/exposure facts, steel30 variant hash, full-budget
calculation and no-launch decision. `host-target-plus10-20261023-v1.json` retains
the exact target command, config diff and readback. Existing freshness, observer
cadence and terminal accounting continue; no new world, freeze, unit or source
was introduced by these decisions.
