<!-- Path: README.md | Purpose: Project entry point and measured progress across the deterministic, Laya and RL runtimes. -->

# Factorio Cursor RL Agent

Build a factory from a starter kit, measure what it actually produces, and improve the decisions that get it there.

The **deterministic runtime is the current showcase**: reusable mine, furnace, mall, belt, power and fluid operations plan legal layouts and build them with funded construction bots. **Laya is the main development focus**. It chooses which of those operations to run using documented preferences and seeded exploration. The separate **RL test bed** supports training and controlled experiments on disposable worlds.

The target is shared C0–C6 checkpoint acceptance, followed by verified **mining-productivity-4** research. Rate proxies, a one-time checkpoint pass and completed research are distinct measurements.

## Measured progress

Experiments use frozen code, paired policy seeds, explicit game/wall budgets and complete job accounting. Recent cohorts use Factorio **2.1.23** on one copied starting save; these results do not establish performance on unseen terrain.

| Evidence | Result | Decision |
|---|---|---|
| [C29: release stale mall reservations](docs/rl/Laya_C29_Result_2026-10-24.md), 120/120 jobs | Strict C2: **44 → 50 / 60**; strict C3: **25 → 38 / 60**, final C3: **23 → 36**. C3 restricted mean time: **120 → 103 game minutes**. | Adopted for disposable Laya cohorts. |
| [C30: steel-site fallback](docs/rl/Laya_C30_Result_2026-10-25.md) | The larger comparison did not support a consistent improvement. | Candidate retained outside the release. |
| [C33: pole clearance](docs/rl/Laya_C33_Result_2026-10-25.md), 120/120 jobs | Removes the known obstruction, but strict C3 regresses. | Not adopted. |
| [C34: corrected foundation census](docs/rl/Laya_C34_Result_2026-10-25.md), 120/120 jobs | **4,323 evaluated acceptance checks, zero evidence errors**. The output-backpressure arm did not improve its strict C3 primary endpoint. | Census verified; output-backpressure policy not adopted. |

[C35](docs/rl/Laya_C35_Result_2026-10-26.md) completed 120/120: finite long-handed batches relieved their kit shortage, but strict C6 fell **8→6 / 60**; the candidate is not adopted. [C36](docs/rl/Laya_C36_Result_2026-10-26.md) completed 120/120: clearance raised strict C6 **5→15 / 60** (12:2 pairs), while `untyped_stuck` rose **12→36** and final C5 fell **17→7**. It remains experimental pending its guards and a clearance comparison with robot occupancy corrected in both arms. [C37](docs/rl/Laya_C37_Result_2026-10-26.md) completed 120/120: robot exclusion removed named robot refusals, but strict C6 fell **11→7 / 60**; it is not adopted.

C29's release reached strict C6 in **15/60** runs and fresh final C6 in **13/60**; the separate Laya circuit-choice/output predicate passed in **30/60**. These are different endpoints. Its zero `tool_error` and safety-stop counts coexist with typed service faults, detailed in the result. Strict C1 remains a weak point in these historical cohorts, and C5 uses the older `c5-v1` definition.

Main now includes the requested [C5-v2 plastic startup contract](docs/rl/Laya_C5_Startup_Contract_2026-10-25.md), with Laya game-clock sampling and recovery, and [strict C2 reliability reporting](docs/rl/Laya_C2_Reliability.md). Live deterministic replays explicitly leave C5-v2 unknown until an attempt-scoped startup protocol exists. These are repository-tested capabilities; C29–C38 remain historical C5-v1 experiments. The [coverage audit](docs/rl/Laya_Convergence_Coverage_2026-10-26.md) records recovered omissions and links the original cohort commit histories, preserved under archive tags. Main contains one squash commit; use those histories to inspect the individual cohort commits.

See the [current evidence and running cohorts](docs/rl/Laya_Current_Handoff.md), [cohort ledger](docs/rl/Laya_Cohort_Ledger_2026-10-21.md), [adoption rules](docs/rl/laya_variants/README.md), and [current status](CURRENT_STATUS.md) for later results and limitations. No experiment result grants Laya or RL authority over the real base.

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
