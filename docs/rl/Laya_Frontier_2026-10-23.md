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
