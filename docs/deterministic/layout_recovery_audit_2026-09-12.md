<!-- Path: docs/deterministic/layout_recovery_audit_2026-09-12.md | Purpose: Record bounded recovery changes and remaining deterministic layout assumptions. -->

# Recovery and layout audit

## Implemented recovery

- Unchanged optional core-mall waits yield to queued production. Configuration
  changes still require re-observation. A capped prerequisite can therefore get
  another scheduling turn instead of waiting until a global timeout.
- Oil fluid routing tries land with margin 48, underground bypasses at 48,
  bypasses with margin 96, then water crossings at 96. Each attempt is pure
  planning against the same observed obstacles. Exhaustion reports every
  attempted strategy; it does not skip validators or change an already submitted
  oil transaction. Non-routing exceptions are not silently retried.
- A combined planner returns the validated placement plan and its fluid segments
  from one search. Previously the oil caller independently calculated both.
  This removes one successful route search per link; no wall-clock speedup or
  live production improvement is claimed yet.
- Observer and primary verdict parsing share one implementation. Tool-only
  transport cannot become a missing assistant verdict, and reviewer statuses
  cannot be used as campaign change statuses.

## Hardcoded assumptions found

This is a targeted source audit, not a proof that no other constants exist.

| Location | Assumption | Meaning / follow-up |
| --- | --- | --- |
| `orchestrator/mall_builder.py` `_CELL_PITCH` | 11 by 6 cells | Relative grid template translated from the selected start; not a fixed world placement. Changing pitch needs shared requester/provider collision tests. |
| `orchestrator/stage_chemical.py` `_find_oil_cell_site` | 64 by 44, radius 80 | Fixed search footprint around observed oil. Future footprint should come from complete candidate geometry. |
| `orchestrator/stage_chemical.py` `_find_plastic_site` | 12 by 12, radius 60 | Fixed search footprint around the computed source midpoint. |
| `orchestrator/autonomous_builder.py` starter transition checks, loan restore, `ensure_produced` | `(3.0, -1.0)` fallback/default | Real absolute-anchor assumption. Thread explicit runtime reference through these callers with translated-base tests before removing it; changing a default alone risks inconsistent discovery. |
| `tools/autonomous_run.py` `--reference-point` | `(0.0, 0.0)` default | Configurable CLI anchor; differs from internal fallback above. |
| `planners/plan_validation.py` collision explanation | `(49.5,47.5)`, `(49.5,46.5)` | Historical comment illustrating a bug; no coordinate-specific validator exemption. |
| Pumpjack siting regression tests | Recorded failure coordinates | Replayed at a translated offset too; runtime siting checks sibling footprints/output stubs generically. |

Deterministic templates remain intentional reference behavior. Replacing them
with learned layouts belongs to isolated RL training and requires held-out
comparison before production promotion. Do not convert every failed site into
another coordinate-specific branch.

## Next acceptance

Restart the affected Python runner/controller and start the supervised plastic
acceptance workflow. Check circuit output and AM2 completion first, then working
plastic. Three same-candidate fresh runs must demonstrate sustained production.
No live lifecycle actions or manual base repairs were used for this change.
