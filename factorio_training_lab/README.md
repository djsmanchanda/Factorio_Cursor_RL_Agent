<!-- Path: factorio_training_lab/README.md -->
<!-- Purpose: Document the independently deployed training-only Factorio mod. -->

# Factorio Training Lab

This mod provisions, executes, observes, and recycles isolated `training/*`
surfaces and `training-*` forces. It is intended only for a dedicated training
server. It is not part of the normal deterministic-mod deployment and has no
authority over `nauvis`, `player`, or `planner-sandbox`.

Registered RCON-only commands:

- `/training_provision <json>`
- `/training_observe <json>`
- `/training_recycle <json>`
- `/training_cleanup_orphans <json>`
- `/training_focus <json>` (configured connected observer only)
- `/training_upload <json>`
- `/training_execute <json>`

Client-only observation command:

- `/training_view training/<scenario-id>`

Training surfaces use Factorio's actual grey lab chequerboard: alternating
`lab-dark-1` and `lab-dark-2` tiles. Viewing switches the client to Factorio's
no-character spectator controller, then moves it to the selected training surface.
The spectator is moved to the training observatory before its viewed episode is recycled.
The loopback Observatory may invoke the same spectator transition through `/training_focus`,
but it derives the target surface from the owned episode and cannot address Nauvis.

Large immutable plans are uploaded in bounded chunks, then executed only after
the Python side has validated the BuildPlan and issued its authorization. The
lab accepts only physical `place_entity` actions within the episode's scenario
budget and force/surface identity. It never changes the deterministic executor
or the Nauvis runtime.
Each disposable training force completes every finite primary technology before
its episode begins. Repeatable/infinite technologies remain unresearched. This
keeps layout exercises focused on construction behavior; later research-focused
scenario families can declare their own narrower technology baselines.

Reports are written beneath
`script-output/factorio_training_lab/reports/`. Recycling completes
asynchronously because Factorio removes a merged force at the end of the tick;
the first recycle report says `pending_force_merge` and the final report says
`completed`. A mod-side watchdog scans the `training/*` namespace every ten
seconds. If a surface is no longer registered to an episode for one minute,
it is treated as an orphan, spectators are left untouched on that surface, and
the surface plus its matching training force are recycled automatically.
The Observatory also exposes an explicit stale-surface cleanup button. It only
targets unowned `training/*` surfaces, protects active episode registrations,
skips surfaces with connected players, and requires a confirmation token.

Observations also include `metrics.item_production` for the objective item,
its declared input, the resource patch, and up to 13 optional
`objective.workflow_items`. Counts and 600-tick-window rates come from the
episode's isolated force and surface. Missing statistics remain unavailable;
the report does not infer stock, committed construction bills, or capacity.

Multi-product objectives require every output target to hold over a common
sustain interval. `metrics.multi_requirement_version = "joint-sustain-v2"`
identifies this measurement contract. A requirement's `closed` flag reopens
when its current window falls below target. The top-level `sustained_ticks`
reports the shared interval, and the objective's sustain duration is the
longest required duration. Separate historical successes cannot complete a
simultaneous production objective.

The executor preserves entity ownership by plan phase. Observations expose
`metrics.live_entities_by_phase` with actual surviving counts, excluding
ghosts and entities outside the episode scope. A retry does not transfer an
entity to another phase. Phase names are bounded to 16 per episode and 128
characters. This lets expansion completion use its own furnace rather than
the total number of furnaces, including copper smelters.

Dropped items are reported as `metrics.ground_item_entities`; they do not
count toward construction budgets, occupied infrastructure or forbidden
builds. Existing entity, fixture, force and bounds protections still apply.

These changes require deployment of the training-lab mod and an affected
Factorio runtime reload before live use. Revised level-3 Python runs reject
reports without `joint-sustain-v2`. Use fresh episodes after deployment; no
old measurement result becomes evidence for the new contract.
