# Path: docs/21_external_game_knowledge.md
# Purpose: Data-only external game knowledge (wiki-derived). Never overrides system standards (docs/19, docs/20).

# External Game Knowledge (Factorio Wiki)

Source: wiki.factorio.com Tutorial:Quick_start_guide + Tutorials index. Ingested 2026-07-18.
Per docs/19: reference data only — validated before use, never authoritative over our standards.

## Early-game progression order (Quick Start Guide)
1. Resources: coal, copper ore, iron ore, stone near spawn; water for steam.
2. Burner phase: burner drill → stone furnace direct-insert; paired coal drills fuel each other.
3. Belts + burner inserters (self-fuel from coal belts) for transport.
4. Electricity: offshore pump → boilers → steam engines; ratio **1 pump : 20 boilers : 40 steam engines**; replace burner drills with electric.
5. Research Automation (assembling machine 1, long inserters), then Logistics (splitters, underground belts, fast inserters).
6. Automate science: gear assembler + red-science assembler → inserter into labs.

## Ratios / heuristics
- Miners: ~2:1 iron:copper early game.
- Belt lanes: keep items split across both lanes of a belt for throughput ("ore split more or less evenly on each side").
- Leave room to expand around every production area.

## Relevant tutorials for later phases
- Main bus (base organization standard for mid-game) — candidate input for CityPlanner block design.
- Applied power math, Nuclear power (PlanetPlanner-era power planning).
- Train signals (rail standard alignment check for docs/14).
- Circuit network cookbook (control logic, far future).

## Implications adopted (validated against our invariants)
- Two-lane belt feeding supports 2-ingredient recipes on a single input belt
  (inserters only pick up items their destination accepts).
- Science automation chain (gears → red science → labs) is the first
  multi-line dependency target for LocalLayoutPlanner chaining.
