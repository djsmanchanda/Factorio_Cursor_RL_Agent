<!-- Path: docs/rl/Laya_C2_Audit_2026-10-04.md | Purpose: Record the C2 failure audit, reusable line-action fixes and isolated matched rechecks. -->

# C2 line action audit

The requested subagent traced completed `mechanics-matrix-20261004` decisions.
All 141 C2-target trials stopped stalled; 118 first reached C1, 13 only C0,
and 10 neither. **No trial presented or selected a legal smelting-line option.**
Terminal copper rejection episodes included 62 no-candidate, 55 drill-collision,
29 medium-pole-shortage and 25 belt-collision cases. Counts overlap because a
menu can reject different candidates for different reasons. More time alone
cannot resolve missing legal actions.

The current frozen mall-slot follow-up has a second problem: a six-furnace
line's full output uses five provider chests, while earned C1 checkpoints
41000/41401 have nine left and checkpoint41400 seven. Two full output rows cost
ten before additional mall construction. Provider-chest manufacture needs
advanced circuits and steel chests, absent from the observation/action recipe
catalog; the checkpoints also have zero plastic and advanced circuits. Trying
another chest cell does not resolve that supply chain.

`training/laya_lines.py` now offers a resource/throughput tradeoff: three or five
output taps on six-furnace lines, and seven or fourteen on eighteen-furnace
lines. The default `output_taps(...)` remains full size. Furnace Start/Middle/End
geometry is unchanged. Estimated capacities are explicit: three regular taps
are estimated at 2.49 plates/s versus 3.75 for the full six-furnace output.
These are planning estimates, not live measured rates.

Line origins are sampled every four tiles within the existing smelting strip,
so a fixed sixteen-tile column phase cannot hide legal placements around poles.
The nominal parallel pitch remains sixteen; all actual actions must clear placed
entities, pass resource/reach/power checks and stay on their reserved land.
Mine geometry is cached and mine/body collisions checked early. No new stock,
technology, policy weights, coordinates or recipe exceptions are injected.

Offline replay of current live observations found:

| Slots world | Prior copper candidates | Updated candidates | Replay time |
| --- | ---: | ---: | ---: |
| followup-0001 | 2 | 32 | 1.04 s |
| followup-0005 | 0 | 8 | 1.87 s |
| followup-0009, copper already built | 0 | 0 | 1.34 s |

All eight restored checkpoint1 options passed land reservation checks. Their
shifted line origin includes (82,-6), an observed-world replay result, not a
hardcoded choice. Source: each corresponding root under
`data/laya-experiments/mall-slot-followup-20261004/code/data/laya-saved-world/`,
its `evidence/site-plan.json` and observed transaction `after` facts. Historical
aggregation comes from the completed matrix's `jobs.jsonl` and each raw root's
`evidence/decision-states.jsonl`.

**46 focused tests pass** across line generation, runner, site planning and mall
slots, including reduced-output legality, unchanged furnace counts, truthful
capacity metadata, candidate caps and shifted placement around existing poles.
Highest evidence so far: unit tests and offline replay of live facts. Full C2
success and actual reduced-tap throughput require the new private live tests.

## Matched live rechecks

A separate 18-trial queue compares the previous frozen compiler against the
updated compiler on three earned C1 checkpoints and three new seeds. Both arms
use identical private mods, mall slots, policy weights, patience48, speed4,
120-game-minute caps and source checkpoint hashes, with no grants. It uses two
extra isolated workers, game34600/34601 and RCON27500/27501. The existing450
trial campaign stays frozen. All rechecks share the original23:54:54IST cutoff;
trials that cannot finish remain interrupted, not positive labels.

Recheck evidence: `data/laya-experiments/c2-rechecks-20261004/`; the driver reuses
the shared Supervisor lifecycle/measurement/cleanup and matched analyzer. Muse
observes this separate domain read-only using the same xhigh model and120s wait
after review. No automatic promotion of a winning arm is performed.

Lifecycle: new disposable runner starts load the Python change. **No mod
redeployment or restart of existing campaigns is needed.** Shared installed
mods remain unchanged; their separate guarded deployment must await all owned
campaigns and stopped shared consumers.

## Launch and early live observations

Matched actor and Muse services started at **17:27:09 IST**. Fixed compiler is
**b22ebfa**; control is frozen **7c31722**. Both private worlds prepared normally,
and Muse saved its first xhigh findings at17:28:38. Launch proof is in
`data/laya-experiments/c2-rechecks-20261004/launch-proof.json`.

The first patched world initially had zero drills and belts. Its recorded
sequence built drill production; by decision9 it had19 drills,77 inserters and
16 medium poles. Decision15 had22 belts; a later observed transaction had98.
Iron-line checks therefore changed from drill shortage to belt shortage. This
is supply progress, not proof of a stalled geometry or completed C2.
Copper checks still include a mix of engine collisions and funding failures.

Source: patched world `raw-recheck-001-tap-options-s65000`, its decision journal
and engine validation report
`server/script-output/laya_saved_world/70547bb8459a4f7089946b4f014cefb2.json`
at tick364853. `collision:transport-belt` names the proposed entity, not its
blocker; initial infrastructure counts show zero existing transport belts.
Existing inserters/chests or neutral obstacles may still matter, but the
reports do not yet establish the actual blocker. If collisions remain terminal,
the next observation improvement is exact rejected-action/blocker evidence.

**C2 reliability remains unproven.** The18 matched tests continue under the
original deadline; the existing450-trial campaign keeps its own frozen code.
The shared-mod guard now waits for all three campaigns before its previously
authorized conditional deployment. No new mod deployment is required by this
Python compiler fix.
