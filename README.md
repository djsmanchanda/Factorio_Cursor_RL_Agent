<!-- Path: README.md | Purpose: Project entry point and measured progress across the deterministic, Laya and RL runtimes. -->

# Factorio Cursor RL Agent

Build a factory from a starter kit, measure what it actually produces, and improve the decisions that get it there.

The **deterministic runtime is the current showcase**: reusable mine, furnace, mall, belt, power and fluid operations plan legal layouts and build them with funded construction bots. **Laya is the main development focus**. It chooses which of those operations to run using documented preferences and seeded exploration. The separate **RL test bed** supports training and controlled experiments on disposable worlds.

The target is shared C0–C6 checkpoint acceptance, followed by verified **mining-productivity-4** research. Rate proxies, a one-time checkpoint pass and completed research are distinct measurements.

## Measured progress

Every cohort runs frozen code on paired policy seeds from one copied starting save, with explicit game and wall budgets, and counts every planned job. Cohorts C45 onward use Factorio **2.1.24**; C58 onward run at **2x game speed** (about 113–117 UPS per world). Results do not establish performance on unseen terrain.

**Where it stands — 9 November 2026**

- **Strict C6 is not yet reached.** Since the corrected c6-v2 predicate (C58), which requires a production consumer of advanced circuits, **1,085 completed runs** across 23 cohorts have produced no valid pass; the single recorded pass (C72) was an accounting artifact. Earlier C6 counts use c6-v1, which mall chests could satisfy, and are not comparable.
- **C4 (first plastic) is reached in about 63% of control runs** (177 of 283 in C80b–C85). Most runs end on the 18,000-tick no-progress stall rule, not the game-tick horizon.
- **The binding blocker is the C6 consumer's construction bill**: long-handed inserters are never stocked (shortfall equals the full requirement in every productivity-module mall attempt), and the belt-fed consumer cell still billed requester chests that cannot be built before logistic science. C86c (requester-free cell) and C87 (long-handed inserter stock) test those two causes now.
- **Adopted bases**: vacancy + foundation (C16), loan credit (C18) and stale-reservation release (C29). Since C44 the **reference line** (`9d9b279` → `ccbecab`) is the behavioural baseline for experiments, because the integrated main-line runtime regressed against it on the same seeds (C39, C44). Main's runtime defaults are unchanged by those results.
- **Provisional only**: direct requester replenishment (`3345f8b`) led the control on C4 in three cohorts (pooled 33 wins / 20 losses, p = .098). It is not adopted, and further C4-only screens are closed because 50-seed cohorts cannot resolve gains below about 20 percentage points.

### All cohorts

One row per cohort that ran. Counts are strict milestones ever reached, per arm, over all planned jobs; restart cohorts start at a checkpoint, so milestones at or below their origin are reached trivially. Per-arm revisions, C2/C3, wall time and predicate versions are in the generated [cohort performance table](docs/rl/Laya_Cohort_Performance.md); full decisions and statistics are in the [results index](docs/rl/Laya_Cohort_Results_Index.md) (C1–C43) and the [current handoff](docs/rl/Laya_Current_Handoff.md).

| Cohort | Date | Start | Arms: reached C4 / C6 of planned jobs | Decision |
|---|---|---|---|---|
| C1–C11 | 18–21 Oct | C0 | Ledgers condensed; per-arm counts in the [results index](docs/rl/Laya_Cohort_Results_Index.md) | No adoption in any of C1–C11. |
| C12 | 21 Oct | C0 | ref 14/1 of 48; raw 11/1 of 48; furnace 10/0 of 48; seed 6/0 of 48 | No adoption; ref best on C4. |
| C13 | 21 Oct | C0 | ref 12/1 of 48; menu 19/4 of 48; loan 19/5 of 48 | No adoption: menu lifts C6 but lowers C2. |
| C14b | 21 Oct | C0 | ref 9/1 of 75; steelbill 8/2 of 75 | No adoption: steelbill loses C2/C5/C6. |
| C14c | 21 Oct | C0 | ref 1/0 of 40; steelbill 1/0 of 40 | No adoption (short-window rerun). |
| C15d | 21 Oct | C0 | steelbill 6/2 of 50; vacancy 10/5 of 50; foundation 4/1 of 50 | No adoption in C15; vacancy/foundation retested in C16. |
| C16 | 22 Oct | C0 | ref 13/6 of 50; vacancy 24/8 of 50; foundation 25/9 of 50 | **Adopted** vacancy + foundation as the cohort base. |
| C17 | 22 Oct | C0 | foundation 47/12 of 75; release 45/16 of 75 | Kept as a correctness fix (loan-release refusals 169→1). |
| C18 | 22 Oct | C0 | release 29/7 of 60; credit 44/10 of 60 | **Adopted** credit into the base (C5 20:5, p≈.004). |
| C19 | 22 Oct | C0 | credit 20/2 of 51; started 18/5 of 51 | Stopped by user; `b372672` reverted. |
| C20 | 22 Oct | C0 | started 11/3 of 75; integrated 7/1 of 75 | Superseded by C22. |
| C21 | 22 Oct | C0 | integrated 17/4 of 75; retain 17/2 of 75 | Superseded by C22. |
| C22 | 23 Oct | C0 | credit 25/11 of 40; integrated 23/6 of 40; retain 27/11 of 40 | Baseline `d8a4825` retained; retention not adopted. |
| C23 | 23 Oct | C0 | integrated 35/9 of 60; receipt 37/14 of 60 | Receipt not adopted. |
| C24 | 23 Oct | C0 | control 30/5 of 60; steel 33/8 of 60 | Steel not adopted. |
| C25 | 23 Oct | C0 | control 35/13 of 60; backing 33/10 of 60 | Backing not adopted. |
| C26b | 24 Oct | C0 | control 19/6 of 30; ready 14/3 of 30 | Ready loans not adopted. |
| C27b | 24 Oct | C0 | control 20/7 of 30; linear3 12/6 of 30 | Linear3 not adopted. |
| C28 | 24 Oct | C0 | control_a 4/1 of 15; control_b 7/1 of 15; steel30 8/1 of 15; feedstock16 7/1 of 15 | Policy-weight screen; no adoption. |
| C29 | 24 Oct | C0 | control 30/13 of 60; release 38/15 of 60 | **Adopted** release `5a5ed28` (C3 19:6, p≈.015). |
| C30 | 24 Oct | C0 | release 32/10 of 60; site 37/8 of 60 | Site not adopted. |
| C30a | 24 Oct | C0 | release 20/4 of 20; site 12/4 of 20 | Site not adopted. |
| C31 | 24 Oct | C0 | census 0/0 of 60; outblock 0/0 of 60 | Census defect; not a result (rerun as C34). |
| C33 | 25 Oct | C0 | control 35/10 of 60; clearance 38/11 of 60 | Clearance not adopted (C3 regression). |
| C34 | 25 Oct | C0 | census 35/10 of 60; outblock 37/11 of 60 | Outblock not adopted; census verified. |
| C35 | 25 Oct | C0 | census 31/8 of 60; lhi 35/6 of 60 | Finite long-handed batches not adopted. |
| C36 | 25 Oct | C0 | control 31/5 of 60; clearance 38/15 of 60 | Held, not adopted: C6 5→15 but untyped faults ×3. |
| C37 | 26 Oct | C0 | control 36/11 of 60; robots 36/7 of 60 | Robot-occupancy exclusion not adopted. |
| C38 | 26 Oct | C0 | robots 0/0 of 60; clearance_robots 0/0 of 60 | Both arms fail C2; not a C36 replication. |
| C39 | 26 Oct | C0 | reference 39/13 of 60; integrated 0/0 of 60 | Reference `9d9b279` retained; integrated main line rejected. |
| C39 preflight | 26 Oct | C0 | base 0/0 of 2 | Wiring preflight. |
| C39 smoke | 26 Oct | C0 | base 0/0 of 2 | Chest-hold smoke (fix `8eb4690`). |
| C39w wiring | 26 Oct | C0 | reference 0/0 of 1; integrated 0/0 of 1 | Wiring check. |
| C40 | 27 Oct | C0 | control 0/0 of 40; gateheld 0/0 of 40 | Gate fact kept as a finding; no adoption. |
| C41 | 27 Oct | C0 | reference 23/8 of 40; gateheld 2/0 of 40; mineres 3/0 of 40 | No adoption. |
| C42 | 27 Oct | C0 | gateheld 6/0 of 40; siting 9/1 of 40; resiting 15/0 of 40 | No adoption. |
| C43 | 28 Oct | C0 | reference 44/12 of 60; accell 38/11 of 60 | Accell rejected; reference retained. |
| C44 | 28 Oct | C0 | control 4/0 of 40; steel 12/1 of 40; ownservice 13/0 of 40; nofluid 11/0 of 40; reference 25/8 of 40 | Main line regressed vs reference; reference line becomes the behavioural baseline. |
| C45 | 28 Oct | C0 | reference 33/14 of 60; steel 41/14 of 60 | Steel not promoted (fault gate); first 2.1.24 baseline. |
| C46c | 29 Oct | C0 | reference 35/9 of 60; consumer 35/19 of 60 | Consumer not adopted (tool-failure gate) → builder filter `580eb11`. |
| C47 | 30 Oct | mixed | reference_a 76/33 of 100; reference_b 80/31 of 100 | A/A mixed-start calibration (C0 + C2–C6 checkpoints); no adoption. |
| C48 | 30 Oct | C0 | reference 39/10 of 75; consumer 43/7 of 75 | Consumer `580eb11` null on C6 (10 vs 7). |
| C49 | 3 Nov | restart C3 | reference 72/0 of 75; facts 75/0 of 75 | C3→C4 facts: neither adopted nor rejected; replicated in C50. |
| C49 smoke | 3 Nov | restart C3 | reference 0/0 of 1; facts 0/0 of 1 | Wiring smoke. |
| C50 | 3 Nov | restart C3 | reference 72/0 of 75; facts 74/1 of 75 | Not adopted; C3→C4 line closed as saturated. |
| C51 | 3 Nov | restart C4 | reference 74/29 of 75; facts 73/25 of 75 | Park/facts bundle closed on C4→C6. |
| C52 | 3 Nov | restart C3 | reference 72/0 of 75; ownership 70/0 of 75 | Ownership link closed. |
| C53 | 3 Nov | restart C4 | reference 4/1 of 75; ownership 4/2 of 75 | Truncated after C52 closed the factor. |
| C55 | 4 Nov | restart C4 | reference 75/43 of 75; densemall 73/40 of 75 | Dense-bay fix rejected; exposed the c6-v1 chest defect → c6-v2. |
| C56 | 4 Nov | restart C4 | reference 11/8 of 75; densemall 10/9 of 75 | Stopped after C55. |
| C57 | 4 Nov | restart C4 | reference 70/28 of 75; loanhold 69/37 of 75 | Loan hold inert; every c6-v1 pass chest-only. No adoption. |
| C58 | 5 Nov | C0 2x | reference_a 49/0 of 75; reference_b 45/0 of 75 | A/A under c6-v2: **0/150** — C6 needs a production consumer. |
| C62 | 5 Nov | C0 2x | reference 6/0 of 6; service 5/0 of 6 | Service smoke: gate failed (provider-chest deadlock). |
| C63 | 5 Nov | C0 2x | reference 0/0 of 6; service 0/0 of 6 | Stopped (diagnosis changed). |
| C65 | 5 Nov | C0 2x | service_a 4/0 of 6; service_b 5/0 of 6 | A/A provider-chest diagnostic: no difference. |
| C66 | 6 Nov | C0 2x | reference 6/0 of 6; guard 4/0 of 6 | Guard null. |
| C67 | 6 Nov | C0 2x | reference 3/0 of 6; reserve 4/0 of 6 | Provider reserve: guard never fired. |
| C68 | 6 Nov | C0 2x | reference 0/0 of 6; reserve 1/0 of 6 | Stopped. |
| C71 | 6 Nov | C0 2x | reference 4/0 of 6; scope 4/0 of 6 | Compact scope: clean null. |
| C72 | 6 Nov | C0 2x | service_a 5/0 of 6; service_b 5/1 of 6 | A/A; its one c6-v2 pass was an accounting artifact. |
| C73 | 6 Nov | C0 2x | reference 2/0 of 6; permanent 0/0 of 6 | Closed: PM consumer never reached. |
| C74b | 6 Nov | C0 2x | service_a 3/0 of 6; service_b 4/0 of 6 | A/A cable baseline: 62% same-code wall spread. |
| C75 | 6 Nov | C0 2x | reference 4/0 of 6; cable 4/0 of 6 | Manipulation failed (tool_error). |
| C76 | 6 Nov | C0 2x | reference 4/0 of 6; cable 5/0 of 6 | Cable bill is chest-bound; hypothesis closed. |
| C77 | 6 Nov | C0 2x | reference 4/0 of 6; hold 3/0 of 6 | Hold mechanism works; no cell built. |
| C78 | 6 Nov | C0 2x | hold_a 4/0 of 6; hold_b 4/0 of 6 | A/A hold baseline. |
| C79 | 6 Nov | C0 2x | reference 4/0 of 6; terminal 6/0 of 6 | Terminal "lead" was the C5 latch. |
| C80b | 7 Nov | C0 2x | reference 26/0 of 40; terminal 25/0 of 40; chest 30/0 of 40 | Chest floor dropped (C4 +4, converts late); terminal null. |
| C81b | 7 Nov | C0 2x | reference 22/0 of 40; direct 25/0 of 40; plastic 26/0 of 40 | Inconclusive by exposure (16 of ≥25 AC pairs). |
| C82 | 7 Nov | C0 2x | reference 32/0 of 50; lastmile 36/0 of 50; consumer 32/0 of 50 | Consumer cell never built (requester bill); lastmile +4 C4 (13W/9L). |
| C84 | 8 Nov | C0 2x | reference 30/0 of 50; direct 36/0 of 50; plastic 33/0 of 50 | Inconclusive: direct 10W/4L (p=.18), pooled 33W/20L (p=.098). C4 screens closed. |
| C85 | 8 Nov | C0 2x | reference_a 34/0 of 60; reference_b 33/0 of 60 | Running: A/A calibration of reached C4. |
| C86c | 9 Nov | C0 2x | reference 12/0 of 40; chestfree 14/0 of 40; chestfree_early 16/0 of 40 | Running: requester-free PM cell (± offered from C5). |
| C87 | 9 Nov | C0 2x | reference 13/0 of 50; lh24 17/0 of 50; lh48 14/0 of 50 | Running: long-handed inserter stock 24 / 48. |

Superseded or cancelled before start, so no results: the first attempts of C12, C14, C15 (b, c), C26, C27, C32, C45 and C46 (2.1.23, b), C52–C54 alternates, C58-reference, C59, C60, C61 (four smokes), C64, C69, C70, C74, C80, C81, C83, C86 and C86b. Their freezes are kept and their planned jobs counted in their own records.

Main also carries the [C5-v2 plastic startup contract](docs/rl/Laya_C5_Startup_Contract_2026-10-25.md) and [strict C2 reliability reporting](docs/rl/Laya_C2_Reliability.md); the experiment line measures C5 with the older c5-v1 latch. Every experiment arm's exact code is preserved in history and under `archive/20261109/cohort/<cohort>/<arm>` tags. No experiment result grants Laya or RL authority over the real base; see the [adoption rules](docs/rl/laya_variants/README.md) and [current status](CURRENT_STATUS.md).

## How the systems fit

| System | Responsibility | Scope |
|---|---|---|
| Deterministic toolkit — `orchestrator/`, `planners/` | Observe, plan, validate, fund and execute reusable construction operations | Explicit real-base missions or isolated test worlds |
| Laya — `training/laya_ops_runner.py` | Choose operations; record choices, bills, outcomes and acceptance evidence | Disposable worlds |
| RL — `training/`, `factorio_training_lab/` | Train policies, evaluate experiments and compare variants | Disposable training worlds |
| `experimental/legacy_autonomy/` | Historical reference | Archived workflow |

The runtimes share contracts and geometry while keeping their mutable state, world identity and authority separate. Distinct deterministic and Laya mod namespaces come from one shared Lua source. Read the [system invariants](docs/system_invariants.md) before changing that boundary.

## Start here

- [Documentation map](docs/README.md) and [architecture](docs/architecture.md)
- [Deterministic runtime](docs/deterministic/README.md) and [checkpoint fleet](docs/deterministic/checkpoint_fleet.md)
- [Laya runner contract](docs/rl/Laya_Clean_Runner.md) and [strategy](docs/rl/Laya_Strategy_Guidelines.md)
- [RL training](docs/rl/README.md) and [training runbook](docs/rl/training.md)
- [Live operations](docs/factorio_operations.md) and [test suites](docs/testing.md)

## Local checks and tools

```bash
# Per-change safety gate
.venv/bin/python -m pytest -m fast

# Complete suite, including slow and integration coverage
.venv/bin/python -m pytest

# Offline scenarios and configured disposable training workers
.venv/bin/python tools/generate_training_scenarios.py --count 100
.venv/bin/python tools/run_training_batch.py --workers training-workers.json --count 100 --attempts-per-scenario 20

# Observatory and deterministic operations console
.venv/bin/python tools/training_observer.py serve
.venv/bin/python tools/dashboard_server.py --help
```

New cohorts use one host-wide slot scheduler, a 7,200-second trial allowance, recorded deadlines and immutable freezes. Verified retention preserves recent worlds, cohort best/median exemplars and hash-checked fine-tuning records. See the [scheduler](docs/rl/Laya_Host_Scheduler.md) and [retention contract](docs/rl/laya_variants/README.md).

Use explicit host, port, surface and force for live work. The [operations runbook](docs/factorio_operations.md) defines deployment, save and restart procedures.
