# Data and State Management

## Snapshot Data
- Exported as JSON
- Fully deterministic
- No functions or metatables

## Planner State
- Ephemeral
- Recomputed each request

## Execution State
- Stored in Lua `storage`
- Save/load safe

## Canonical Data Contracts

All inter-layer communication must use versioned schemas.

Documentation describes intent.
Schemas define truth.

If a mismatch exists:
- Schemas win
- Docs must be updated
