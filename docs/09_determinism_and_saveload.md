# Determinism and Save/Load Safety

Rules:
- All mutable state lives in `storage`
- No reliance on local Lua variables across ticks
- No random numbers without fixed seeds

This ensures:
- Multiplayer safety
- Replayability
- Debuggability
