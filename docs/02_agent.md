# Agent Design

The agent is hierarchical.

## High-Level Planner
- Deterministic
- Symbolic
- Math-based

## Low-Level Executor
- Reactive
- Time-optimized
- Trained via imitation / RL

The agent never reasons about recipes or ratios.
Those are fixed, external knowledge.

## Supervisory Intelligence

At large scale, the agent shifts from planning to supervision.

Responsibilities:
- Detect supply chain stress
- Coordinate planetary roles
- Manage time-latency tradeoffs
- Maintain resilience over optimality

## Phased Large-Scale Operation

At large scale, the agent operates in phases:

1. Measure (metrics)
2. Judge (policy evaluation)
3. Propose (intent generation)
4. Plan (deterministic planning)
5. Execute (RL / heuristic)
