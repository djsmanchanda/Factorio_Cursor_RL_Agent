<!-- Path: docs/rl/Laya_Checkpoint_Regression.md | Purpose: Define offline checkpoint regression and its live-integration blockers. -->

# Laya checkpoint regression: offline contract only

Source baseline: `f40b875696f48ed104242bcdd5261700fad16ac3`. This is not the later frozen reference `9d9b279`, nor C45/C46 candidate code. No campaign or runtime has been changed. C43's reference strict C6-ever was 12/60 and final 10/60; the accelerator was 11/60 and 7/60. Those are cohort results, not validation of this code. C44 was active and C45/C46 were queued in the supplied October 28 evidence; recheck their actual status before any lifecycle action.

## What exists

`training/laya_checkpoint_bundle.py` provides an atomic new-generation capture, verification, and restore **hook contract**, not a real Laya runner hook. It requires a valid save ZIP, a paused nonnegative tick, equal before/after fingerprints, a strict earned predecessor receipt, and seven registered JSON state objects: controller, ownership, construction, material claims, policy RNG, phase baselines and strict evaluator. The generation records creator, source/save ancestry, code, mod, Factorio, strict predicate source hash and schema identities. Contents are hash-checked; suites pin the manifest hash. The hook is responsible for the truth, completeness, coherence and durable capture of the fields inside those objects; the current contract cannot establish those properties. A ZIP plus placeholder objects is not a usable checkpoint. Legacy `laya-earned-checkpoint-v1` bundles are incompatible, with no migration proof.

`training/laya_segment_queue.py` persists matched C0 and selected checkpoint jobs in SQLite. Each arm receives the same checkpoint generation, seed and tick budget; the queue retains every planned job and separately records terminal fault categories. Strict successor telemetry must declare its actual predicate version, baseline verification, completeness and an achievement tick later than the checkpoint. A predecessor latch is not a successor. Missing telemetry is unknown; proxy results remain separate. C6 has no successor in this C0–C6 registry. Results are conditional on the checkpoint and **do not establish a full-run gate or adoption**. The report is bounded JSON with `planned`, `terminal`, `unknown`, and `rows`; no dashboard file is required. This is not yet an experiment-registry artifact.

The Python manager protocol is an integration seam, not an operational scheduler. `submit_or_observe(job)` must atomically and durably deduplicate attempt IDs, obtain global host admission, and return `waiting`, `running`, or a typed terminal status/result. It must allocate unique disposable server/RCON ports, roots and credentials and never kill active lanes to reduce capacity. A coordinator crash after admission is recovered by observing the same attempt ID. The included fake manager proves only the seam. There is no real scheduler binding, CLI dispatcher, retry API, full-run acceptance calculation, or process reconciliation yet. CLI `dispatch` deliberately refuses to launch. C0 uses the pinned suite source identity; no engine input is materialized by the offline queue.

## Offline commands

All paths below are rooted in this checkout. The CLI accepts spec filenames under `data/laya-regression/specs/` and generations under `data/laya-regression/catalog/`; it rejects path escapes and symlinks. `plan` uses an in-memory queue and does not write. `enqueue` is explicit and persists only a local SQLite plan, not a live run. Create an input JSON in the specs directory using this shape:

```json
{
  "suite_id": "example", "seeds": [101, 102], "generations": [],
  "budget_ticks": 540000, "predicate_version": "c6-v1",
  "predicate_sha256": "<64 lowercase hex>", "source_sha256": "<64 lowercase hex>",
  "mod_sha256": "<64 lowercase hex>", "factorio_version": "<exact version>", "state_schema": "<hook schema>",
  "arms": {
    "reference": {"commit": "<revision>", "code_sha256": "<64 lowercase hex>", "freeze_sha256": "<64 lowercase hex>", "dirty": false},
    "candidate": {"commit": "<revision>", "code_sha256": "<64 lowercase hex>", "freeze_sha256": "<64 lowercase hex>", "dirty": false}
  }
}
```

```sh
python tools/laya_checkpoint_regression.py catalog
python tools/laya_checkpoint_regression.py validate --generation GENERATION
python tools/laya_checkpoint_regression.py plan --spec SUITE.json
python tools/laya_checkpoint_regression.py enqueue --spec SUITE.json
python tools/laya_checkpoint_regression.py report --suite SUITE_ID
python tools/laya_checkpoint_regression.py dispatch --suite SUITE_ID  # deliberately unavailable
python tools/laya_checkpoint_regression.py export --spec JOINED.json > outcomes.json
```

These fields are declarations, **not verified freezes**; do not dispatch based on them. Dirty code is rejected, not automatically frozen. Actual generation capture is only `capture(catalog, generation, hook, checkpoint=..., identity=..., lineage=...)`; it is intentionally not exposed as a fake CLI command. It must be called by the future runner only at a quiescent decision boundary. `restore(...)` calls the complementary hook with a new attempt ID; it never copies an old journal itself. No hook exists in the current runner.

## Required integration before real use

1. In `training/laya_ops_runner.py` and its caller, introduce a versioned capture/restore API at a safe decision boundary. Pause and query before/after a completed save, compare a canonical physical-state fingerprint (see `training/laya_saved_counterfactual.py`), and serialize all actual controller locals, site/construction ownership, outstanding transactions and material claims, policy RNG, rolling measurement history, phase baselines, and strict evaluator inputs. Reconcile in-flight actions rather than taking an inconsistent snapshot. Prove the schema contains every durable field for the deployed candidate; the seven outer objects alone are not that proof.
2. On restore, create a fresh isolated runtime and journal/attempt identity, load the save and all state, restore policy RNG and evaluator histories, and compare the physical fingerprint. Reevaluate predecessor/baselines using source-pinned structured predicates; reset successor latches. `CheckpointEvaluator` otherwise returns `monotonic_reached` for a previously latched milestone. Strict C6 `c6-v1`, ops proxy v2, and candidate v3 remain separate. Pin the predicate **source hash** as well as the version: the evidence documents differing C5 behavior under the same version label.
3. Bind the queue to the host scheduler's durable idempotent global admission API. Validate actual frozen commits, code/mod/source hashes, executable and state schema before every admission and scoring; unique port/root reservation must cover all live workloads. Implement retries with new attempt IDs and explicit planned-job linkage; account for every attempt. Add cross-process crash/restart, cancellation, and resource collision tests. Implement a source-verifiable full-run early-regression and target gate and publish the registry-compatible bounded report. Do not promote checkpoints automatically.
4. Once campaigns have ended and a separately authorized freeze is committed (or its diff recorded), validate one real disposable capture/reload, then a matched same-generation replay, multi-server isolation, and finally a separately authorized capacity test. Compare candidate/reference against declared strict early-service, C6, safety and typed-fault gates over all planned jobs; preserve unknowns. A segment improvement alone cannot pass the full-run gate. Never use a production Nauvis save or command channel.

## Observational export

`training/laya_outcome_export.py` joins `choices`, `receipts`, and terminal `outcomes` by attempt/choice/lineage, checks the complete saved menu hash, legality and behavior probabilities, selected propensity, time bounds, strict version and censoring, and preserves waits and failures. Inputs are one JSON object containing those three lists. It emits observational records only; same-seed cross-arm jobs are not same-state branch pairs. Save ancestry determines train/validation/test buckets, so siblings cannot leak across splits; one ancestor cannot establish held-out terrain generalization. Missing joins or invalid distributions reject the export. This is a validation contract, not proof the saved menu was exhaustive or that logged propensities were actually sampled; reconstruction/audit and independent terrain are still required before training. No model training or causal value claim is made.
