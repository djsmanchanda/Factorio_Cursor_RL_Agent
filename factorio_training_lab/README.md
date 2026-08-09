<!-- Path: factorio_training_lab/README.md -->
<!-- Purpose: Document the independently deployed training-only Factorio mod. -->

# Factorio Training Lab

This mod provisions, observes, and recycles isolated `training/*` surfaces and
`training-*` forces. It is intended only for a dedicated training server. It is
not part of the normal deterministic-mod deployment and has no authority over
`nauvis`, `player`, or `planner-sandbox`.

Registered RCON-only commands:

- `/training_provision <json>`
- `/training_observe <json>`
- `/training_recycle <json>`

Reports are written beneath
`script-output/factorio_training_lab/reports/`. Recycling completes
asynchronously because Factorio removes a merged force at the end of the tick;
the first recycle report says `pending_force_merge` and the final report says
`completed`.

The deterministic mod remains responsible for validating and executing approved
BuildPlans. This mod only owns training environment lifecycle, delivery
measurement, and enforcement of each scenario's virtual construction budget.
