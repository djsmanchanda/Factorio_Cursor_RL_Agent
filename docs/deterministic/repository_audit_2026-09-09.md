<!-- Path: docs/deterministic/repository_audit_2026-09-09.md | Purpose: Evidence-led expansion and repository-maintenance audit. -->

# Expansion and repository audit — 2026-09-09

## Scope and evidence

Reviewed the supplied `episode-20260909T135221Z-22428` log (+2509s
`no_progress` on splitter), the accompanying factory image, the current
deterministic control paths, shared planning/executor contracts, and the RL
architecture and operating boundaries. Performed an AST/name-reference inventory
across Python source and tests. This is a repository-wide static inventory plus
targeted behavioral review, not a claim that every function or Lua path has been
manually audited. No live factory mutation, deployment, or loop changes.

Inventory at the audit scan (before final cleanup):

| Area | Python files | Lines |
| --- | ---: | ---: |
| core | 19 | 3,407 |
| orchestrator | 43 | 29,517 |
| planners | 52 | 13,361 |
| training | 32 | 4,695 |
| tools | 41 | 9,343 |
| experimental | 13 | 1,954 |
| tests | 169 | 44,490 |

There were 2,109 named test functions before this cleanup, with parameterization
producing more collected cases; 22 test files used `inspect.getsource`.
Line counts alone do not justify deletion. The large controller and repeated
state/accounting policies are more important maintenance risks than test count.

## Findings addressed

1. **The refinery storage tap incorrectly drove an advanced production chain.**
   `planners/smelter_block.py` sized a basic refinery's provider inserter for
   the aggregate furnace output. The 6→12 expansion therefore requested a bulk
   inserter although the primary output is a continuous belt. Keep this
   nonblocking construction-storage tap on a fast inserter; remove the obsolete
   full-throughput tap selector. This does not reduce the main belt's capacity.

2. **Chemical admission could mistake stock or failed telemetry for capability.**
   `_unfunded_ladder_ingredient` now requires observed chemical production for
   downstream batches. A handful of stocked advanced ingredients does not
   authorize a new batch before the predecessor chain exists. Failed production
   observations defer explicitly. The chemical ladder still establishes its own
   rungs in order, rather than gating itself. Ordinary bootstrap solids can
   still use legitimately stocked inputs.

3. **Power routing ignored stocked long-reach alternatives.**
   `extend_power` defaulted to medium poles even for long links. It now compares
   fully stocked big-pole/substation routes with the medium route, preferring
   fewer entities or a stocked alternative when medium poles are exhausted.
   Every large pole needs a clear 2×2 footprint; mixed-tier edge reach,
   reservation funding, and measured post-build connectivity still apply.
   The image alone does not prove that every existing pole can be removed:
   shared branches and supplied consumers remain authoritative.

4. **Steel startup introduced a wood dependency unnecessarily.**
   At +2119s medium poles required steel; steel repeatedly required two small
   poles, while the run had stocked substations. The one-furnace steel planner
   now retains its existing full-row substation geometry when that stock funds
   it, rather than choosing small poles. Medium poles remain preferred when
   funded. No speculative steel-chest replacement is required to make first
   steel. Literal reuse of a retired iron starter has not been implemented:
   it needs a separate ownership/retirement migration, whereas the existing
   persistent iron provider already offers a source without dismantling it.

5. **Configuring existing assemblers created false construction demand.**
   `generate_mall_stock_gate_update` used `place_ghost` for existing machines.
   Their updates therefore acquired assembler material bills. It now emits
   existing-entity-only `configure_entity` actions with an empty construction
   bill; the executor must not create a missing machine to satisfy configuration.

## Cleanup performed and retained coverage

- Removed unused private helpers `_count_by_name` and
  `_orthogonally_adjacent`. Whole-repository name searches found no callers;
  neither was a documented public entry point.
- Removed the obsolete full-rate refinery tap selector with the policy change.
- Removed five redundant tests checking exception text, exception ancestry, or
  counting handlers in `test_prep_shortage.py`. The actual queued-shortage
  transition remains exercised by
  `test_submitted_foundation_shortage_is_queued_for_the_mall`.
- Removed two source-order assertions requiring standing prep before mall or
  extraction work. Construction handoff behavior, prerequisite production,
  pending-foundation reconciliation, and safe retirement tests remain.
- Corrected chemical admission tests that tolerated unknown capability.
- Added focused behavior coverage for side-tap bills, configuration-only bills,
  stocked steel power, long-reach bridge selection, and complete pole footprints.

Do not remove collision, force/surface isolation, real-entity ownership,
electrical connectivity, recovery, recipe legality, or retirement tests to make
expansion easier. They prevent real damage. Platform managers and the retired
experimental runtime have explicit documented consumers/boundaries; lack of use
in this Linux episode is not proof that those files are dead.

## Highest-priority remaining work

1. **One project progress record:** reconcile planned, installed, delivered,
   reserved, and still-missing quantities in one place. Restoring a loan must
   not discard the evidence that bots consumed its completed output. Repeated
   inventory targets and stale loan demand remain a larger speed risk than
   selector score tuning. Test the complete produce→install→restore→survey
   sequence, not just the restore call.
2. **Demand-derived reserves:** the fixed belt reserve floor can block a
   splitter that is itself required to finish the reserved blueprint. Replace
   policy floors with remaining project demand and downstream input accounting;
   do not weaken collision or material funding checks.
3. **Bounded observation rather than repeated synchronous work:** the supplied
   log repeated steel layout attempts 30 times and mall power repair 27 times.
   Wake deferred work on prerequisite state changes and reuse valid site plans.
   Keep genuine no-progress detection rather than extending timeouts.
4. **Test consolidation by outcome:** migrate remaining source-introspection
   tests when changing their subsystem, rather than deleting them wholesale.
   Prefer small scenario traces that measure first usable output, no duplicate
   work, and material/entity budgets across several decisions.
5. **Controller decomposition only at stable boundaries:** move observation,
   project accounting, and scheduling behind explicit interfaces after their
   state semantics are corrected. Avoid a broad mechanical split of the large
   controller that preserves its conflicting policies in more files.

## Lifecycle and verification

Changes are Python/controller/planner only. A restarted runner activates them;
fresh-episode validation is still required to measure expansion speed and verify
the resulting live power layout. No Lua redeployment or Factorio restart is
required solely for these changes. Test results are recorded in CURRENT_STATUS
and the change handoff; passing unit tests is not live timing evidence.
