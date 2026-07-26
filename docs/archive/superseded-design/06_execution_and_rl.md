# Execution and RL

RL is used only for execution efficiency.

## State
- Player position
- Inventory
- Nearby ghosts
- Bot availability
- Zone completion

## Actions
- Move
- Craft
- Place entity
- Request items

## Reward
- Negative ticks to completion
- Penalty for rework
- Bonus for zone completion

## City Execution Constraints

The executor:
- Never edits rail corridors
- Never modifies deployed blocks
- Executes only approved build phases

RL is scoped strictly to execution efficiency,
never structural planning.
