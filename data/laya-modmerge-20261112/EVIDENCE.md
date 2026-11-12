<!-- Path: data/laya-modmerge-20261112/EVIDENCE.md | Purpose: Frozen evidence for the additive Track A pack + Track B protocol port. -->
# Merged mod pack — build and acceptance evidence

**Status:** continued per parent decision (2026-11-12): keep the port additive and gated; the merged pack becomes the single pack only after acceptance (a), (b1) and (b2) pass.

**Base:** Track A's pack `3e02f726` (the pack C94/C95b run), copied to `mods/` and patched by `patch_mod.py`. Rebuild from a clean copy: `cp -a <A pack> mods && python patch_mod.py <this directory>`. Every Lua file passes `luac -p`.

## Ported changes (all additive)

1. **Correlated layout receipts, streamed v2.** B submits `stream_id`+`sequence` layouts (request id = stream + 16 hex, 30 s timeout) and requires `layout-submission-receipt-v2`. Added B's v2 stream journal and v1 receipt journal; correlated calls write the request-id report and A's tick-named report; `/layout_executor_capabilities` untouched.
2. **Two actions:** `place_ghost_no_reconcile` and `configure_entity_if_unit_number`, with B's semantics (guarded failure reason `guarded_ghost_position_occupied`, `expected_unit_number` check, no reconcile/repair for the guarded variant, ghost tags and mirror carried). A's own actions unchanged.
3. **Requester-chest build evidence:** `trash_requesters.revive_with_trash` now returns the replacement chest (`return true, nil, created`) as B does, so the build record binds to it. A's caller ignores the extra values.
4. **Opt-in natural-obstacle clearing**, gated on `build_plan.clear_natural_obstacles == true`: `clearable_obstacle`, the `clear_natural` clause in `blocked_only_by_removals`, the pass-through in `atomic_placement_blocked`, `order_obstacle_deconstruction`, and the clear-before-place pre-pass. A's programs never set the flag.

## Instrumented executor check (why #4 exists)

The failing plans were traced with temporary file-writing instrumentation in `diag-mods/` (not part of the pack):

```
ATOMIC BLOCK transport-belt at 81.5,-22.5 blockers=[]
ATOMIC BLOCK transport-belt at 81.5,-21.5 blockers=[]
ATOMIC BLOCK transport-belt at 97.5,7.5 blockers=[]
ATOMIC BLOCK transport-belt at 98.5,7.5 blockers=[]
```

No entities occupied those points; RCON showed dry dirt and `can_place_entity=false`. Trees at (80.6875,-21.625) and (79.5625,-24.0625) block the footprints. B's `submission_plan` always stamps `clear_natural_obstacles: True` (`training/laya_saved_world.py:510`), which A's executor ignored. After the port, B ran with 0 blocked plans.

## Runs

| Run | World | Result |
|---|---|---|
| b-run4 (before #4) | `b-merged4-s0` | 107 decisions, 3 `atomic_footprint_blocked`, stop stalled, C0-C1 |
| b-run8 (after #4) | `b-merged8-s0` | **103 decisions, 0 failures, proxy C0-C3, stop stalled** — same phase reach as B's own pack on the same save (`b-on-a2`: 91/0/C0-C3) |
| a-smoke2 | `a-merged2-s0` | **201 decisions, 0 failures, stop window_timeout, proxy C0-C1** |
| first attempt (before port) | `b-on-a-s0` | 1 decision, 3 `Timed out waiting for layout receipt response` |

Summaries are in `runs/`. Strict C4 did **not** latch in b-run8 (B stalls at proxy C3 on A's world with either pack); (b1) is therefore not met on this seed and needs a save where B reaches C4.

## A-inertness proof

`git grep clear_natural_obstacles 369b7d6` finds nothing in Track A's Python or Lua; only B's `submission_plan` sets the flag. The gated branches cannot run for A's plans.
