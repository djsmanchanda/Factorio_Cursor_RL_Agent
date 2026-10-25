<!-- Path: docs/rl/laya_helper_runs/reset-continuation-20261017/luna-medium-release-review.md | Purpose: Preserve Luna-medium review evidence for the finite-cell implementation. -->
# Luna medium release review — October 17, 2026

Read-only helper: GPT-6-Luna, medium reasoning, task `/root/luna_test_review` via the active Codex helper harness. No helper file edits or live actions.

First pass found two P1 issues: clearing the ownership marker before restoration creates a restart loss window; legacy recipe-switch recovery can spill WIP while acknowledging completion. It also requested a provider identity check and empty-section restart tests.

The implementation was revised before commit. It retains the empty request marker through a verified restoration and final acknowledgement. Engine-returned real items are held in `storage.laya_cell_releases` until the owned provider accepts them; held inserter stacks move without a spill fallback. Pending recovery validates requester/provider unit IDs. Tests now include partial/full destination, held items, machine refund/quality, module reload, changed provider, wrong recipe, acknowledgement, interrupted restore and finite-cell vacancy.

Second pass: the helper considered those findings addressed and found no further confirmed material bug. Its remaining concern is that standalone Lua uses mocked Factorio APIs. It specifically requires a fresh disposable-engine smoke for `set_recipe(nil)` refund/quality records and partial insertion before broad matched testing. This is review evidence, not an engine or production success claim.

Recommended live protocol: preserve completed day campaign; commit/freeze current code and staged mods; use committed save SHA 347eba65065014f82261330d84d5ca2fa3e3427cad3ab18dcfdf496dd9c442b0; first verify cleanup and restart boundaries, then matched fresh cohorts with fixed concurrency/deadline and all planned outcomes accounted. Measure strict new plastic production AND delivery, C5 sustained service, release failures and crashes.
