<!-- Path: docs/rl/Laya_Model_Cohort_2026-10-27.md | Purpose: Record the admitted 120-job released-model comparison and its capacity/wiring gates. -->

# Released-model cohort — 27 October 2026

The authorized 120-job cohort launched at **2026-10-27 01:08:26 IST** on
Miniship. It compares guided control, released Kev, released Vela and Jiwo v2 on
30 matched decision seeds80300–80329. These are **120 planned jobs, currently
running**, with no outcome/adoption conclusion. New Kev/Laya fine-tunes are
separate artifacts and do not replace these released arms.

All four arms use frozen implementation
`2511e4bab85c28cebdb31144a880c8eb7eced121` on retained mechanical base
`9d9b2796a3ecb5bc1faed213ea825a6614e8eaff`. The
[final C39 decision](Laya_C39_Result_2026-10-27.md) rejected integrated default
adoption after all120 outcomes and cleanup were accounted. Source-save, mods,
validators, model assets, released calibrators and environment locks remain
identical to the prepared cohort.

Shared execution: live speed2, 540,000-tick game window, 7,200-second job wall
cap, tick-only18,000 idle guard, learned epsilon.15, one active job per arm.
The initial common global concurrency was **three**, reduced from the unqualified
proposal of four before admission. Seeds, arm order and all120 planned IDs are
unchanged. CPU inference consumes live game time, so this compares complete
deployed policy packages including latency; it cannot isolate strategy quality.

The three final model servers concurrently processed the same complete
37-option historical input. Kev:7,962tokens/155.78s/9.055GiB process peak;
Jiwo:8,063tokens/128.40s/4.710GiB; Vela:14,178 aggregate tokens in two native
full-menu windows/117.80s/4.092GiB. Aggregate model cgroup peak was
15,421,833,216bytes (14.36GiB); sampled host available memory stayed above
10,324,226,048bytes (9.61GiB), with no memory-limit/OOM events. Vela windows
cover every state token and each includes every option. No input/menu truncation
or guided fallback is permitted.

Four separately counted wiring jobs, seed80330, concurrency1, 3,600ticks/900s,
all completed and cleaned up. They made13 decisions: guided6, Kev1, Vela4,
Jiwo2. The seven learned choices join exactly to the original state, rendered
request, full offered menu, bundle identity, probabilities and decision receipt.
All four stopped at the game window with zero caught failures, backend errors,
tool/safety/untyped faults. Wiring checks no policy quality. Systemd reported
1.7G peak for the serial four-job batch, including charged page cache.

The admitted package uses a **4GiB aggregate world/runner pool** and a
**19GiB aggregate own slice** containing that pool and the model servers, with
swap disabled. Measured simultaneous model peak plus the hard world-pool limit
is18.36GiB. This protects other host processes; long developed-world memory is
not certified by short wiring. Resource failures remain in the planned ledger;
there is no automatic restart or omitted failure. The original concurrency4
proposal and preparation freeze are preserved; a new capacity-v2 admission
freeze records the common adjustment. Host scheduler target15 is unchanged.

Registry: `data/laya-experiments/laya-model-base-2123-miniship-20261026`.
Bounded unit: `laya-model-base-2123-miniship-20261026.service`, invocation
`0d6fd9c4e51d4a5a822dc972ae074327`. Main freeze SHA256:
`00cf8cad717957d7fe2d65e9d526409b4f44612466bffe418506d95ab8a9f23b`.
Admission ends2026-10-31 00:00IST; independent unit bound ends00:10.
The owned30-minute continuation must account for all120 outcomes, cleanup and
unstarted/cut jobs and disable by00:20IST. Preserve the independent read-only
supervisor and expired older queue metadata.

Operational receipts:
`data/laya-orchestration/model-launch-20261026/{concurrent-serving-qualification,wiring-qualification}.json`,
`admission-capacity-v2/{capacity-decision,freeze}.json`, main registry
`launch-evidence.json`, and `state.json`. The initial qualifier used system
Python without jsonschema and failed before requests; its corrected .venv launch
passed. Kev's first server unit omitted the pinned HF cache and failed before
readiness; the replacement explicitly sets the existing cache. Failed logs are
retained. These are launch corrections, not discarded experiment jobs.

Lifecycle: leave the active freeze/worlds/services untouched. Observe bounded
counts/health, then perform final matched strict/proxy/fault/latency accounting
after every planned terminal and scoped cleanup. No automatic adoption,
production change, installed-mod deployment, root action or push.

## User-authorized capacity increase — 2026-10-27 02:06 IST

The user explicitly requested increasing concurrency toward15. The initial
three-world limit was an operational choice, not a demonstrated host ceiling.
The original supervisor was drained through its existing admission protocol;
its4completed and3running attempts were preserved. Exactly113 never-started
IDs were released with an acknowledged administrative queued cancellation and
assigned disjointly to four new capacity segments:29guided,28Kev,28Vela,28Jiwo.
Those113 source cancellation rows transfer ownership; they are not additional
experimental outcomes. Original actual7 + transferred113 remains120logical
jobs,30per arm, with no reruns. Original batch/freeze/code/assets are unchanged.

New registries/units are `laya-model-scale15-{guided,kev,vela,jiwo}-2123-miniship-20261027`
under `data/laya-experiments/`. Each has its own immutable batch, planned IDs,
freeze and launch receipt. All use unchanged runtime2511e4 and the same source
save, mods, validators, released bundles, seeds and game/wall budgets. Wrapper
commit `f08eb7308b52dd3e3b76a96a59e0562427464df0` validates the acknowledged
never-started transfer, rejects duplicate assignments, and counts per-arm
reservations across the draining original and all four segments under the
existing host admission lock. Four focused tests pass; the1347-test fast gate
exited0. The wrapper was committed before freezing; no push.

Global capacity is15 through the unchanged host gate, with a shared per-arm
ceiling4. New segments share an11GiB/no-swap/CPU1200% world pool; individual
segment units have5GiB/CPU400% ceilings. The own aggregate slice was expanded
from19 to25GiB as part of this user-authorized capacity revision. Original
4GiB batch budget and all three one-thread model servers remain unchanged.
Their shared serialized HTTP serving retains the original600-second timeout;
more worlds can queue for inference. Capacity therefore changes observed
latency, and original/scale15 results must be shown as separate capacity
strata alongside the complete120-job accounting.

First live verification: **15running,4completed,101queued**; guided4/Vela4/
Jiwo4/Kev3 active, fresh heartbeats (maximum9.9s),7.09GiB host available,
93recorded learned choices across currently running worlds,0backend errors
and no memory-limit/OOM events. This checks live operation, not policy quality
or every future context length. Receipts and original-source snapshots are in
`data/laya-orchestration/model-launch-20261026/capacity15-20261027/`, especially
`transfer-plan.json`, `segment-freezes.json`, `launch-evidence.json` and
`first15-health.json`. The transfer map is authoritative for final joins.

Lifecycle: all five supervisor components must finish and release their worlds
and reservations before final accounting and stopping the exact owned model
servers. Never restart the original transferred queues or treat its113cuts as
113failures or120actual completions. The same owned30min continuation now
tracks all five components, next2026-10-27 02:28:19.804IST; terminal accounting
and disable-by2026-10-31 00:20IST remain mandatory. No root, production, mod,
host-target, model-weight or policy changes. No automatic adoption or push.
