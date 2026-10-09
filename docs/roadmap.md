<!-- Path: docs/roadmap.md | Purpose: Current priorities per system; details live in the linked audits. -->

# Roadmap

Updated 2026-10-09. The August roadmap is in the [archive](archive/roadmap_2026-08-10.md).

## Main focus: Laya over deterministic tooling

Target: C0–C6 (`orchestrator/checkpoint_milestones.py`), then mining-productivity-4.

1. Truthful operation receipts and stock/bill/lender facts on every option.
2. Break the foundation/steel dependency cycle that blocks C2.
3. Actions and a terminal predicate for later mission goals; deterministic milestone predicates as acceptance, not proxies.
4. Faithful replay labels from `decision-states.jsonl`, then compatible policy training.

Open findings and their evidence: [Laya system audit](rl/Laya_System_Audit_2026-10-08.md), [workflow audit](rl/Laya_Workflow_Audit_2026-09-30.md).

## Showcase: deterministic runtime

Keep the tooling reliable as Laya's action library. Follow the incremental migration in the [deterministic workflow audit](deterministic/Deterministic_Workflow_Audit_2026-10-08.md): causal dependency diagnosis, deferral that survives re-promotion, and shared preview/execute/reconcile contracts.

## RL test bed

Matched A/B campaigns under the [variant adoption rule](rl/laya_variants/README.md), with complete-cohort accounting. No real-base promotion until held-out evaluation beats the deterministic baseline.
