<!-- Path: docs/rl/Laya_C32_Design_2026-10-25.md | Purpose: Preregister the user-authorized C32 pole-clearance comparison and its ownership/deadline. -->
# C32: connected pole clearance before refinery growth

Queued 2026-10-25 01:30 IST by thread `94dcfaf2-9531-4234-bde5-b6ae616de9b1`.
The design was written into `freeze.json` before the unit was started.

| Field | Frozen value |
| --- | --- |
| Root / unit | `data/laya-experiments/laya-c32-2123-miniship-20261025` / same name plus `.service` |
| Planned denominator | 120 jobs: 60 seeds 79860–79919 × control / clearance |
| Control | `cec3008`: release base + foundation census + C5 startup contract and corrected reporting |
| Clearance | `ab7e8d3`: same common code + pole clearance and its safety/retry checks |
| Runtime difference | `orchestrator/autonomous_builder.py`, `orchestrator/pole_obstruction.py`; mod trees identical |
| Engine / save | Factorio 2.1.23 build 87750; SHA256 `347eba65065014f82261330d84d5ca2fa3e3427cad3ab18dcfdf496dd9c442b0` |
| Allowance | 540,000 game ticks / 7,200 wall seconds per job |
| Ports | game 39300–39329; RCON 32300–32329 |
| Dependency | C31, unchanged; no adoption-dependent transplant of its treatment |
| Admission deadline | 2026-10-27 18:00 IST; last full admission 16:00 |
| Independent cleanup | unit RuntimeMaxSec expires by 18:02, KillMode=control-group, TimeoutStopSec=60; kill by 18:03 |
| Primary | strict C6 ever attainment, clearance versus control |

C31's output-backpressure change and C30's unadopted steel-site change are excluded.
The census supplies policy facts as well as structural measurement. Historical
pre-census C2/C3 counts are therefore not directly comparable. The C5 change is
common to both arms and follows [the user contract](Laya_C5_Startup_Contract_2026-10-25.md).
Research, reconciliation, and wait pacing remain off/default.

Secondary endpoints: strict C1–C6 and Laya C6-v2 separately; time to milestone
(150-game-minute RMST, reached medians, paired faster/slower); fresh-final
C2/C3/C5/C6 with unknown endpoints retained; earlier service; stopped/caught,
typed and logged bugs; safety/tool failures and window timeouts. Retained C5
startup is not a claim of uninterrupted later plastic service.

Measure offered and selected iron-foundation/expansion choices in each arm;
collision refusals, `POLE RELOCATION` attempts, missing-material refusals and
`SMELTER POLE CLEARANCE` successes. Keep event counts and affected worlds
separate. Check intervention exposure for candidate-only wins. No encounter
means unexercised, not a negative mechanism result. Analyze matched seeds;
do not pool other cohorts or turn A/A discordance into an arbitrary threshold.
Every planned attempt, including unstarted, interrupted and unknown, remains
in accounting. Same-map policy seeds do not prove terrain generalization.
Adopt only by `laya_variants/README.md`; there is no automatic adoption.

At 01:31 IST the owned unit was active, all 120 jobs queued, admission reason
`waiting_for_predecessor:laya-c31-2123-miniship-20261024`, zero worlds reserved,
and all freeze checks passed. The host scheduler was 17 running / target18,
p95/p98 UPS 110.7/110.7, 89.2°C, 14.1 GiB available (snapshot, not a guarantee).

Persistent 55-minute checks return to this thread. First next check: October 25
02:26:49 IST. State and timer ID: `data/laya-orchestration/c32-20261025/state.json`.
The sibling `checkin.py` checks all statuses, hashes and the exact unit. No other
owner's timer, cohort, or shared scheduler is owned by this task. Starship is
closed; S30r evidence is local.

At completion, preserve all 120 terminal statuses and a final snapshot; verify
this exact unit is inactive, its cgroup/world processes empty, and reservations
reconciled. Finish matched attainment/time/exposure/service/bug analysis,
update the records and disable only the timer ID in this state. The systemd
cleanup cap is independent of model authentication.
