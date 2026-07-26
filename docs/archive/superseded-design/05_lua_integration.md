# Lua Integration

The Lua mod exposes:

- Entity snapshots
- Construction APIs
- Zone prioritization
- Progress tracking

All persistent state is stored in `storage`.

Lua never:
- Performs optimization
- Computes ratios
- Makes long-horizon decisions
