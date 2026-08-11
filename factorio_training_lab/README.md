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
