# Migration to City Mode

This document defines how an existing factory transitions
into city-scale block-and-rail planning without breaking production.

---

## 1. Migration Philosophy

Migration is:
- Incremental
- Reversible
- Non-destructive

The system never tears down a working base blindly.

---

## 2. Migration Trigger

Once City Mode is activated:
- Organic growth is frozen
- New production must follow block rules
- Existing production is grandfathered

---

## 3. Step 1 — Snapshot & Classification

Planner:
- Takes a full factory snapshot
- Groups entities into proto-blocks
- Classifies production roles

Outputs:
- Candidate blocks
- External dependencies
- Throughput estimates

---

## 4. Step 2 — Rail Spine Deployment

Before touching production:
- Reserve city grid
- Lay main rail corridors
- Power the corridors
- Validate loops

No stations yet.

---

## 5. Step 3 — Shadow Blocks

For each major production area:
- Build a new block elsewhere
- Connect it via rail
- Ramp up output gradually

This avoids hard cutovers.

---

## 6. Step 4 — Traffic Migration

- Divert consumers to new blocks
- Drain old production naturally
- Verify stability

Only once stable:
- Decommission old layouts

---

## 7. Step 5 — Enforcement

After migration:
- External belts disabled
- Cross-block bots disabled
- All new production must be block-based

City Mode is now fully active.

---

## 8. Rollback Strategy

At any point:
- Stop migration
- Fall back to previous blocks
- Rail infrastructure remains usable

Migration failures are non-fatal.

---

## 9. Why This Works

This mirrors real infrastructure upgrades:
- Parallel systems
- Gradual load transfer
- Zero downtime

The agent behaves like a civil engineer,
not a speedrunner.
