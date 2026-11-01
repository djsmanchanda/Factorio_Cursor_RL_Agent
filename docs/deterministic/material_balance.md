<!-- Path: docs/deterministic/material_balance.md | Purpose: Record the initial fixed material-balance solver and its boundaries. -->

# Fixed material balance

`core.material_balance.solve_fixed_balance` solves a caller-supplied system of
material equations exactly with standard-library `Fraction` arithmetic. Rows
are materials, columns are fixed recipe or source rates, and the right-hand
side is net demand. It distinguishes a unique algebraic solution from an
inconsistent or underdetermined system; a unique solution may contain negative
rates. It does not decide physical feasibility, recipe choices, disposal,
source caps, machine counts, or strategic preferences. In particular, an
underdetermined result is not evidence that a nonnegative solution exists.

The primitive has offline catalog adapters described below, but is not connected
to runtime execution. Callers must establish supported deterministic recipe
semantics and validate nonnegative rates and physical capacities.
Float inputs are rejected rather than silently approximated. The historical
catalog fixture used in offline comparisons is not evidence of the selected
live save.

`core.fixed_recipe_balance.solve_fixed_recipe_balance` adapts explicitly
selected, enabled and supported recipes into those equations. Callers specify
net output demands and fixed external input rates or explicitly allowed inputs
whose rates are solved as additional unknowns. Optional caps on solved source
rates produce explicit shortages without changing the solution. Underdetermined
systems do not establish whether caps or nonnegativity can be satisfied; no
source allocation or disposal is optimized. The adapter rejects unsupported part fields, catalysts,
and conflicting item/fluid identities. It reports algebraic nonnegative
feasibility as unknown for underdetermined systems. A positive result still
depends on the accuracy and completeness of the supplied catalog and does not
validate machine, transport, power, or runtime constraints. Catalog numeric
floats are interpreted by their decimal string representation.

`core.catalog_demand.plan_catalog_demand` validates an exact catalog byte hash
and a caller-supplied provenance manifest, then expands a single-output acyclic
chain only through unique or explicitly preferred recipes. Inputs must be
declared as fixed rates or capped solved sources; unsupported, ambiguous and
cyclic cases fail closed. The manifest is an assertion from the caller, not
proof of the game's actual mod set, surface or bonuses.

`core.fixed_recipe_machines.compute_fixed_recipe_machines` reports nominal
machine counts for explicit machine choices and a uniquely solved balance.
It checks recipe-part signatures against the supplied catalog. Machine facts
use exported tick-rounded recipe times and prototype speeds, not observed
performance; effects, power, transport and feeder limits remain unmodelled.

`core.block_flow.check_block_flow` compares declared block-port rates with an
explicit global net balance and reports external input and output separately.
Explicit internal links enforce item/fluid identity and prevent allocating a
port more than once; unresolved links remain conditional. Shared-capacity
facts account for existing and proposed allocations without treating unknown
supply or commitments as zero. Source identities and measurements are caller
assertions, not independently verified physical routes or live observations.

`core.bounded_linear.solve_bounded_linear` maximizes a caller-provided exact
rational objective over explicitly bounded nonnegative variables. It enumerates
vertices within a deterministic candidate budget; budget exhaustion never
proves optimality or infeasibility. `core.bounded_recipe_balance` translates
caller-selected recipes, capped sources and explicit disposal into this model.
This is a small bounded-system tool, not a general simplex or a runtime planner.
`tools/benchmark_optimizer.py` measures small historical oil/cracking models and
synthetic scaling with explicit budgets. In one local run the nine-variable
synthetic case exhausted 1,000 candidates without a feasible incumbent. Timing
is host-specific; Python allocation peaks exclude native memory and fixture
loading. The solver does not expose time to first valid candidate. These cases
do not establish performance on a full factory or live catalog.

`core.science_line_candidate` relates an explicitly selected line geometry to a
provenanced demand balance and caller-selected machines. It verifies the catalog
bytes and machine identities but only compares nominal machine counts. Its
geometry remains non-deployable: no site, power, transport, funding, or measured
throughput has been verified, and direct placement/removal actions are retained.

`planners.ghost_line_artifact` can derive a ghost-only offline artifact from a
supported line plan. It rejects unsupported actions and uses an explicit
caller-declared prototype-to-item mapping for its gross bill; neither the
mapping nor availability is verified against a live runtime. A synthetic replay
in `tests/test_optimizer_build_slice_replay.py` exercises shortage, held
reservations and recovery in a temporary ledger. It is not an execution or
commissioning receipt.

`core.optimizer_artifact_binding.bind_artifact_to_construction_plan` binds a
validated ghost artifact, its declared bill and throughput settings to a
canonical digest. Artifact reads are detached copies of retained canonical
bytes. Entity ghosts are supported; tile ghosts fail closed. This binds the
declared mapping and geometry, not their correctness in a particular world.
An optional versioned `planning_context` is included in both canonical hashes,
but the binding validates only its envelope, not its contents. Its mandatory
`unverified` trust label is not evidence of provenance or execution authority;
without an independently pinned expected hash a caller can replace or omit it.
Binding version 2 also carries each explicit ghost recipe into the construction
entity identity. Old version 1 binding hashes do not match and require a fresh
binding and receipts. An observation must assert the matching planned recipe;
when no recipe was planned, its identity uses `None` and makes no claim about
the runtime recipe. Other settings are not checked by the reducer.

`core.optimizer_construction_state` reduces ordered, scoped construction and
throughput receipts, preserving funding and rejecting a prior state that does
not match replay of its receipt history. Throughput is calculated from an
explicit counter window in the declared unit. Neither a matching hash nor a
consistent receipt proves that a disposable instance, bot construction or
consumer delivery was independently observed. No execution authorization is
granted, and the adapter is not connected to a runtime dispatcher.

Throughput units distinguish `items/tick`, `items/second`, `fluid/tick` and
`fluid/second`; the plan and measurement must match exactly. Per-second rates
use 60 ticks per second. The reducer accepts integer cumulative counters only,
including for fluids; it does not round fractional observations or establish
that a runtime supplies a scoped fluid counter. Oil-block site validation,
fluid measurement provenance and runtime commissioning remain unimplemented.

`planners.oil_block_artifact.build_oil_block_artifact` re-verifies a bounded
oil balance, sizes the selected oil and cracking machines, and combines
caller-located rows with caller-ordered, caller-columned fluid trunk links. It
checks plan collisions and fluid surface purity before deriving a ghost-only
artifact using a caller-declared item mapping. Planned underground endpoints
must have an explicit in-range, correctly facing partner and no closer
opposite-facing planned endpoint. More distant candidates, pre-existing world
entities and actual engine pairing remain outside that check. Each fluid's
candidate pipe tiles and full machine footprints are also checked against the
repository's pump-free bounding-box span model. That conservative candidate-only
screen does not validate the engine's current rules or outside connections.
Header attachments expose where
future source and destination connections may be planned; they do not prove
any such connection. Oil artifacts carry unverified context for the canonical
catalog digest, machine rates, fluid boundary rates, caller row/trunk choices,
ports and candidate spans, plus explicit fluid-related pending checks. The
digest identifies a canonical JSON representation, not original file bytes.
The historical 2.1.21 test binds a 10-refinery, 12-plant
candidate to a fluid-rate target offline. Terrain, source capacity, electric
power, pipeline throughput, world underground pairing, bots and measured production
remain unverified; the candidate is not execution authority. This is not an
integrated runtime dispatcher or evidence of oil production.

`build_and_bind_oil_block` derives the selected commissioning target from the
candidate's positive net fluid demand rather than accepting an independent
number. It rejects an output with any modeled external source, preventing that
source from being credited as production by the block. Its synthetic replay
holds the declared construction bill, rejects a mismatched machine recipe as
completion, and commissions only after a matching rate window. These are
caller-asserted offline receipts; a scoped runtime observer, site verification,
source supply and execution isolation are still required.

The shared material ledger rejects non-integer, negative, boolean and coerced
string counts in bills, stock, reservations and bootstrap counts. A malformed
stock input cannot change a project's reservation or construction state. The
hold flag must be a boolean; malformed persisted counts or flags fail closed
rather than being coerced during loading. Existing lifecycle policy is unchanged:
construction reservations stay active only when `hold_until_producing` was set.
The ledger's scope remains caller-provided and is not proof of runtime identity.
