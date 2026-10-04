<!-- Path: docs/rl/Laya_Followup_Window_2026-10-04.md | Purpose: Pin the authorized eight-hour continuation, isolated follow-up screens and evidence limits. -->

# Eight-hour experiment continuation

The latest authorization starts a new supervision window on **2026-10-04,
15:54:54–23:54:54 IST**. The running 554-trial mechanics matrix keeps frozen
814be8d code and private mods. A separate follow-up waits for its DONE evidence
and inactive actor before using its own 12 workers (34500–34511 / 27400–27411).

The follow-up's initial queue has **450 trials**: 396 earned-C1 C2 screens and
54 circuit/plastic repair replications. Three earned C1 checkpoints, three
seeds, and 120/180-game-minute windows compare the current workflow against
mall slots, gate scales, buildable-request horizons, retarget/gate/settle/coverage
preference strengths, dependency/combined context, and one gate/request/context
interaction. All C2 arms use the same 48-decision patience. Controls run first;
remaining screens are seeded-shuffled. If the queue completes before the window
ends, new seeds replicate the comparisons. No policy or default is adopted.

Gate/request knobs are additive; normal runner defaults remain unchanged.
Per-stage line-site counts expose missing zones, construction reach, mine
coverage, hauling, logistic reach, placed collisions and invalid plans in the
recorded observation. Declared component production footprints are reserved
from power relay placement before their stages are built.

The old matrix exposed `recipe_switch_held_stack_source_missing` while repairing
a belt-fed machine. The executor now returns held ingredients to a pickup chest
when available and spills any remainder at the pickup position for bots. Native
Lua tests protect quantity/quality preservation and failed-spill handling. A
private live replication (seed 51001, belt-fed 3:2 circuit chain, machine fault)
repaired in **1200 ticks**, then produced and delivered **3 circuits/s for 7200
ticks**, with zero ghosts and no infrastructure error. Evidence:
`data/laya-experiments/mall-slot-followup-20261004/probe-code/data/laya-saved-world/belt-repair-51001/evidence/component-result.json`.
This proves the repaired production path; physical recipe-return inventory
arrival still needs a separate live item-balance measurement.

Muse uses `opencode-go/muse-spark-1.3-contributor`, xhigh, with a 120-second wait
after each review. It observes the queued follow-up and separately linked
predecessor evidence, then the live follow-up. Findings are advisory and saved
under `helper/`; sessions rotate after eight successful reviews. Window check-ins
at 17:54:54, 19:54:54, 21:54:54 and completion preserve separate outcome domains.
The deadline limits workers and observer calls; incomplete trials remain
interrupted and excluded from successful comparisons.

The guarded shared-mod deployer waits for **both** campaigns' terminal evidence
and inactive actor units, plus stopped shared consumers, before applying pinned
committed files. It starts no server and changes no save. Its guard expires one
hour after this window if conditions remain false.

Lifecycle: existing workers continue unchanged. New disposable workers load
private updated mods on start. Shared mod redeployment remains conditional;
a running GUI must reload after that future deployment.

## Verified launch

The follow-up and Muse units are active, with the actor correctly queued behind
the still-running predecessor. Muse saved its first xhigh review at 16:26:23 IST.
**69 focused tests pass**, and all 22 frozen variant catalogs load and verify.
A separate two-game-minute earned-C1 mall-slot continuation completed with two
recorded decisions, zero failures, and line-site counts present in both states;
its server stopped afterwards. It did not reach C2 in that short smoke window.
This checks the continuation path, not the long-window hypothesis.

Launch proof: `data/laya-experiments/mall-slot-followup-20261004/launch-proof.json`.
Frozen follow-up commit: **7c31722**. The existing systemd version rejected an
in-place RuntimeMaxSec update, so an owned-services-only cutoff timer enforces
the window, leaving 30 seconds for shutdown. New units also have runtime caps.
The old actor caches its original deadline, so its progress countdown may show
the older cap; the cutoff timer is authoritative for this supervision window.
