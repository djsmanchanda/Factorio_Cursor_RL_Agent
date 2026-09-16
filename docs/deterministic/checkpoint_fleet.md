<!-- Path: docs/deterministic/checkpoint_fleet.md | Purpose: Define concurrent deterministic checkpoint regression runs and promotion. -->

# Deterministic checkpoint fleet

## Purpose and boundary

The checkpoint fleet shortens deterministic regression feedback by starting
isolated Nauvis/player runs from several proven timeline positions. A run from
checkpoint `Ci` passes its core test when it reaches `Ci+1`; it may continue to
`Ci+2` and beyond. These segment results do not erase or replace complete
checkpoint-zero evidence.

Every active lane owns one Factorio server, one Python runner, one state root,
one save, one mod copy, one script-output tree, one log tree, one RCON secret,
and unique game/RCON ports. Lanes never share a command channel or writable
runtime state. The fleet uses at most eight active servers and one Nauvis
runner per server. RL training workers remain a separate runtime.

## Default timeline

The ordered registry starts with these versioned predicates:

| ID | Predicate |
|---|---|
| `C0` | Canonical tracked base input is verified. |
| `C1` | Required plate starters are healthy and at least four mall assemblers work, including iron gears and copper cable. |
| `C2` | Opening iron and copper districts produce and deliver output; their temporary starters are absent. |
| `C3` | The opening stone-brick district produces and delivers output; its temporary starter is absent. |
| `C4` | New plastic production and provider delivery are observed. |
| `C5` | The same plastic producer cohort advances throughout 120 game seconds with provider delivery. |
| `C6` | Advanced circuits are newly produced, then an owned recipe which consumes them records new output. |

Stable checkpoint IDs are independent of display order. An inserted checkpoint
such as `C1a` creates a new registry version and explicit order
`C1 -> C1a -> C2`; it does not rename bundles or rewrite historical results.
Predicates consume structured observations and distinguish unknown evidence
from a failed condition. Log substrings are not acceptance signals.

## Run and result semantics

Each suite pins its candidate commit, mission, Factorio version, registry
version, and exact checkpoint generations when it enters the queue. A run has
separate lineage, suite, and attempt identities so episode-scoped durable state
can resume without reusing result or helper identities.

- Reaching `Ci+1` records one tick.
- Reaching `Ci+2` records two ticks and makes a middle lane reclaimable when
  another test is waiting.
- A run continues while capacity is free. A later failure appends an `X` and
  its reason without removing earlier ticks.
- `C0` records every checkpoint crossed. `C0` and the current frontier run
  continue until mission completion, failure, or explicit stop.
- Terminal evidence retains the final 100 runner-log lines.
- Incompatible checkpoints, infrastructure errors, cancellations, functional
  failures, and performance timeouts remain distinct even when the compact UI
  renders a tick, warning, or cross.

For each transition, timing uses the latest twenty successful canonical runs
with the same predicate and Factorio versions. Ad-hoc, cancelled,
incompatible, and infrastructure-failed attempts are excluded. After at least
three samples, a trimmed-average duration multiplied by 2.5 is the slow
threshold. A slow run shows a warning. When work is queued it may end as a
performance timeout; otherwise it continues and an eventual pass retains both
the tick and warning.

## Queue policy

The scheduler has no commit-count limit and an eight-lane active cap. It
prioritizes:

1. the newest commit's `C0` and frontier lanes;
2. explicit checkpoint verification;
3. previously queued lanes;
4. newest-suite middle lanes;
5. ordinary ad-hoc attempts.

One newest-suite middle lane receives a reserved dispatch after every five
ordinary starts. A verification request which waits through five available
slot assignments becomes the highest-priority next dispatch. Active endpoint
runs are never automatically killed. Double-ticked middle runs are the first
automatic reclaim candidates. Operators may remove an individual queued lane,
stop an active lane, or release older-commit runs explicitly.

A persistent user service watches the checked-out branch. Each new descendant
commit enqueues the default suite exactly once when automatic runs are enabled.
Branch switches, history rewrites, and dirty working-tree edits do not trigger
automatic suites. Scheduling can be paused without terminating active lanes.

## Checkpoint bundles and promotion

A runnable checkpoint is an atomic, hashed bundle of the world save, Lua
storage, episode manifest, mission state, material reservations, bootstrap
district/work ledgers, oil transactions, and every registered durable sidecar.
Capture quiesces the controller, saves Factorio at a known tick, copies the
paired sidecars, verifies the bundle, then resumes or ends the lane. A world ZIP
alone is not a checkpoint.

Creator provenance remains immutable. A derived attempt manifest records the
candidate code, mod and dependency identities separately. Versioned sidecar and
Lua-storage migrations run before functional scoring; unsupported input is
`incompatible`, not a planner regression.

Checkpoint defaults are manual pointers to immutable generations:

- a `C0`-origin save may become a normal checkpoint candidate;
- a run which began at the then-current frontier may create the next
  provisional `Cn*` checkpoint;
- a starred checkpoint is replaced by a `C0`-origin generation before losing
  its star;
- an ad-hoc recent-save start never silently changes canonical results;
- a creator more than ten ancestor commits behind the selected candidate is
  highlighted; divergent and unavailable provenance are separate warnings.

Before changing a default, the console runs one matched verification pair from
the current and proposed inputs with the same code, mission, Factorio and
predicate versions. Both must load, reconcile, preserve hard invariants, and
reach the successor; layout equality is not required. A mismatch keeps the
current pointer and exposes evidence.

## Retention and naming

Each checkpoint origin, including sub-checkpoints, retains its newest twenty
rolling bundles across commits. Current/provisional defaults, manual pins,
verification inputs, and active-run inputs are protected from collection.
Capture occurs every five minutes and at milestone crossings and terminal
failure.

Readable names follow
`checkpoint3_a1b2c3d4_09_15_02pm_s07`, with a collision suffix when needed.
The provisional star is metadata, never a literal wildcard in a path. All
generated saves and fleet state remain outside the repository or ignored;
only the canonical base save is tracked.

## Helper and Operations Console

The helper is fleet-selected instead of runner-global. Default suites enable
one helper on the frontier lane for every candidate commit. Custom suites can
select any lane. A middle failure before its second tick exposes **Restart with
helper** using the same original input. Helper data, reports, manifests,
inventory endpoints, and comparison history are lane-scoped.

The console provides **Run default**, **Custom run**, **Verify checkpoint**,
**Pause scheduling**, and **Auto-run commits**, plus queue removal, per-run
stop/retry/helper/log controls and a bottom promotion panel. The main
view is a checkpoint-by-commit matrix. Commit ranking prefers the longest
contiguous failure-free prefix, fewer intermediate failures, furthest progress,
more double ticks, fewer slow warnings, then normalized speed. A separate table
shows the last ten frontier runs with provenance, outcome, reason, helper link,
and final log excerpt.

Responsive layout uses wide, medium, and narrow modes. At partial desktop
width the matrix becomes full-width ahead of selected-run controls and logs;
small screens keep readable cells in a horizontally scrollable matrix rather
than compressing them.

Custom mission overrides are deliberately bounded to `produce:<item>` or
`research:<technology>` and are stored with the pinned suite inputs. Arbitrary
commands are rejected. One-off inputs must resolve to a catalog generation or
coordinator-listed candidate; a typed filesystem path cannot bypass the
catalog.

## Evidence and activation

Offline schema/unit tests prove only contracts. Acceptance proceeds through a
fake-manager fleet integration, live capture/reload of one checkpoint, matched
old/new-code replay, multi-server isolation, and finally a bounded eight-server
capacity probe. No checkpoint segment counts as a fresh end-to-end acceptance
run. Python changes require the fleet coordinator, affected runners, and
Operations Console to restart. Lua changes require deployment and restart of
each affected Factorio lane; GUI synchronization is separate and occurs only
when a GUI client will join.
