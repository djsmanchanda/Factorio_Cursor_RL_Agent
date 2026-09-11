<!-- Path: docs/system_invariants.md | Purpose: Protect runtime isolation, production safety, and truthful evidence without forbidding RL planning. -->

# System invariants

These are boundaries, not layout recipes.

## 1. Runtime isolation

- Real Nauvis/player, disposable training workers, and legacy experiments are separate runtimes.
- Training may not mutate the real save, consume its command channel, or silently replace its planner.
- Surface, force, server, port, save, mod set, and policy generation are explicit in evidence.

## 2. Structural learning with protected production

- RL may plan zones, entity layouts, belts, pipes, power, logistics, and phased expansion inside disposable training environments.
- Candidate plans must pass shared hard validators for Factorio legality, collisions, available resources, fluid purity, and scope.
- Design preferences such as short routes, direct belts, zoning, expansion room, and stability are rewards or priors unless explicitly promoted to a hard safety rule.
- Real-base authority requires an explicitly promoted policy, bounded action scope, observable results, and rollback. Training success alone grants no production authority.

## Deterministic construction

All new player-force deterministic infrastructure must be blueprint ghosts,
funded by real items and built by construction bots. No direct-placement or
bootstrap item-grant exception exists. Existing-entity configuration may not
create a missing target or replace a bot-built entity to apply settings.
Disposable training/sandbox setup is a separate isolated lifecycle.

## 3. Evidence and lifecycle truth

- A unit test proves only the tested behavior.
- A repository change is not deployed code.
- A deployed mod is not active until the correct runtime reloads it.
- A running episode is not successful until its measured outcome is recorded.
- Reports state the exact validation, deployment, restart, surface/force, and live observation performed.

When an older document conflicts with these boundaries, archive or update it. Historical documents do not regain authority because code still references their names.
