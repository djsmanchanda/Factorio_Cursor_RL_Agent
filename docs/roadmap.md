<!-- Path: docs/roadmap.md | Purpose: Record current priorities without freezing speculative architecture. -->

# Roadmap

## Primary: make learning real

1. Build small scenario families for mining, solid crafting, fluids, power, transport, repair, and phased expansion.
2. Give policies enough observations and actions to discover structure rather than select only prebuilt layouts.
3. Improve rewards and bottleneck attribution so failures teach the next population.
4. Run diverse attempts in parallel, evaluate on held-out seeds, and preserve reproducible lineage.
5. Use bounded local autoresearch to propose changes to policy, reward, curriculum, and model configuration.
6. Promote only when learned policies beat simple and deterministic baselines without violating validators.

## Supporting: improve the deterministic runtime

- Finish fuels, oil, chemical production, and remaining science dependencies.
- Replace fragile transport afterthoughts with coherent mine, refinery, and production layouts.
- Improve recursive supply diagnosis, repair-before-duplicate behavior, and recovery from blocked placements.
- Extract reusable observation, validation, execution, and measurement primitives for training.

## Later

City, rail, planet, and interplanetary planning remain useful research directions, not present-day invariants. Their previous detailed designs are archived until evidence justifies restoring them.
