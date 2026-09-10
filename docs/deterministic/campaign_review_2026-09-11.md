<!-- Path: docs/deterministic/campaign_review_2026-09-11.md | Purpose: Audit the twelve-hour campaign and define a more discriminating experiment cycle. -->

# Campaign review — 2026-09-11

The campaign improved several bootstrap mechanics, but did not demonstrate research completion. Its later iterations optimized observation coverage more than factory progress. Longer runtime and larger inventories are not sufficient acceptance criteria.

## What worked

- Starter retirement now uses delivered replacement output while preserving outstanding construction. Steel furnace recipes are inferred from inputs. These are reusable lifecycle/Factorio-legality corrections, not coordinate exceptions.
- Ghost-bill reconciliation and verified stock-cap readiness address real mismatches between construction obligations, installed capacity, and current crafting.
- The ladder-waiter yield fix addresses a dependency cycle. The isolated run lineage and terminal records make investigation possible.
- Stopping repeated failures and declining speculative planner edits were sensible. Preserving a failed episode makes targeted diagnosis possible without replaying its entire bootstrap.

These are code/design assessments. Historical green tests and live observations in the brief remain distinct evidence levels; they do not establish that every fix improved end-to-end performance.

## What went wrong

1. **A stock scope error contaminated the final diagnosis.** Cycle 17's terminal snapshot has zero cable in the splitter requester and 45 + 36 cable in the other two requesters. Those sum to the reported `net=81`. `live_base.available_items` counts all containers, including reserved requester/buffer inputs; it does not measure transferable supply or a particular logistic network. The assertion that adequate cable supply ruled out starvation was unsupported. The saved observation even says the 81 cables were absent from the network. Assembler status can help, but this evidence already warrants examining allocation and delivery before relaxing the guard.
2. **Duration became a proxy for success.** Cycle 15's 93 minutes, accumulated iron, and 258 progress credits do not demonstrate a working science line or a later research milestone. The outer budget counts uncredited passes, so calling it simply “100 passes” also hides productive passes.
3. **Telemetry was collected one field per expensive restart.** The automatic fallback prompt asks for another telemetry patch after a no-change verdict. A changed file is not an experiment: each patch should predict which competing hypotheses the next observation resolves.
4. **The controller did not honor its stated stop contract.** A changed tree overrode `stop`/`no-change`; conversely a `change` verdict could advance with an unchanged tree. This review corrects both cases. The fingerprint is still only a coarse change detector, not verification or run-code provenance.
5. **Evidence bookkeeping drifted.** The brief contains a duplicate final episode, missing verdicts/timeouts, and runs carrying uncommitted changes under the same HEAD label. The claim that all tests were green was not reproducible in the campaign test file: its RUN END fixture predated the committed timestamp-aware parser. The fixture now uses real elapsed-time markers.

## Changes from this review

- Finish the pending iteration-limit and per-loan status diagnostics.
- Report missing-input `containers` and `transferable` counts separately. Unreadable probes and unknown recipes remain `?`; an empty readable requester remains empty.
- Compare requester ingredients against one complete recipe craft. One cable cannot satisfy an electronic circuit that needs three.
- Require both an explicit `change` verdict and a detected tree change before another cycle; `stop` always stops.
- Add behavior tests for reserved/partial/unreadable stock and actual campaign continuation decisions.

No scheduling threshold, live factory state, or server lifecycle was changed. These corrections improve diagnosis and retry control; they do not claim to fix the underlying splitter production stall.

## Recommended replacement cycle

**Freeze inputs → run → capture causal evidence → test a hypothesis → focused fix → local verification → matched rerun → accept or revert.**

Before each run, record the commit plus dirty-patch hash, save hash, bootstrap profile, configuration, active mod version, and one hypothesis with a predicted measurable result. Preserve the same starting conditions for before/after comparisons. Once a fix passes, test a second scenario to expose overfitting.

Observe continuously with cheap structured events; use model reviews at milestone changes, meaningful stalls, and RUN END. Keep periodic liveness checks, but do not ask the model to re-read an ever-growing journal every two minutes. Compact summaries should link to immutable evidence and retain uncertainty.

At a stall, capture the whole blocked dependency once: target and recipe, per-craft amounts, requester/machine input and output inventories, transferable stock, reservations, inserter and assembler status, power, ghost backlog, and craft/delivery deltas over a measured interval. Reuse existing read-only probes on the preserved episode when authorized. A single terminal snapshot cannot establish a rate or prove a deadlock.

Define success before editing: for example, the splitter batch reaches its required count, plastic produces sustained output, or the first science line runs. Record time to that milestone and resource/entity cost. Survival time and unrelated stock growth are secondary diagnostics.

Use a small reproduction of the observed failure before another full run. Accept one telemetry-only rerun only when its missing evidence cannot be obtained from the preserved episode and it has explicit distinguishing outcomes. After that, pause for diagnosis rather than requiring a code edit to keep the loop moving.

For guard work, compare progress in the blocked dependency's craft/delivery/construction chain over game time. Preserve a separate overall experiment budget. Do not raise the twelve-pass limit merely because the factory is accumulating unrelated plates; first demonstrate which relevant progress the guard missed.

Make each run journal entry unique by episode ID, and distinguish behavior fix, diagnostic change, infrastructure fix, and no change. Record tests and activation separately. Keep the same-failure stop as a resource bound, but group investigations by typed blocker, dependency and observed mechanism—not just selected item and percent.

## Verification and activation

178 focused construction-stock, campaign-controller, run-budget and lifecycle tests passed. This is local test evidence, not a deployed or observed factory result. Restart the campaign controller and deterministic runner to activate their respective Python changes when the next experiment is authorized. No Lua mod redeploy or Factorio restart is required for these changes.
