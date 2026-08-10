# External Knowledge & Layout Search

This document defines how the system leverages external optimized layouts
(similar to Cursor-style code search).

---

## 1. Motivation

Human-optimized layouts already exist:
- Megabase blueprints
- Speedrun designs
- UPS-optimized factories

The system should reuse knowledge, not rediscover it.

---

## 2. Layout Ingestion Pipeline

Sources:
- Blueprint strings
- Community repositories
- Curated datasets

Steps:
1. Import blueprint
2. Normalize orientation
3. Extract layout metadata
4. Benchmark in simulation
5. Store as layout primitive

---

## 3. Layout Evaluation

Each imported layout is scored on:
- Throughput
- Area efficiency
- Power usage
- UPS impact
- Compatibility with block rules

Only layouts passing constraints are promoted.

---

## 4. Layout Library

Layouts are versioned:

GC_GRID_COMMUNITY_V3
SMELTER_LINE_UPS_V2

The planner selects layouts based on context,
not popularity.

---

## 5. Safety Rules

The agent:
- Never downloads executable code
- Never executes unverified layouts
- Never modifies core rail standards

External knowledge augments, never overrides.
