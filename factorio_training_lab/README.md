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
- `/training_upload <json>`
- `/training_execute <json>`

Client-only observation command:

- `/training_view training/<scenario-id>`

Training surfaces use an explicit grass floor rather than Factorio's black lab
tiles. Viewing switches the client to Factorio's no-character spectator controller,
then moves it to the selected training surface. The spectator is moved to the
training observatory before its viewed episode is recycled.

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
`completed`.