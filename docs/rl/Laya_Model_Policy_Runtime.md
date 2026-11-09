<!-- Path: docs/rl/Laya_Model_Policy_Runtime.md | Purpose: Define checkpoint decision authority, complete-input inference and weak-supervision boundaries. -->

# Checkpoint policy runtime

`tools/run_laya_clean.py run --actions deterministic --policy model
--model-bundle /absolute/bundle.json` selects a checkpoint-backed policy over
the original deterministic operation menu. Geometry and legality stay in the
shared executor. Guided and uniform policies remain explicit alternatives.
Exact model recovery, jumpstart and ops checkpoint capture are unsupported and
rejected. A failed model request terminates the job without a policy fallback.

`training/laya_model_policy.py` renders `factorio-system-one-v2`: causal brief,
shown rule statements, phase, stock/reservations, and all structured operation
descriptions. Feature columns and shared facts remove repeated serialization;
unknown values remain unknown. Historical logs lack free-text summaries, so
those stay in evidence rather than the shared training/serving input. Gate
previews expose the active bill shortage and current/target gate; geometry
identities remain in the executor and original evidence.

Bundles name a loopback scoring endpoint, model, pinned artifact hashes,
renderer, exploration floor and request timeout. Client and server both verify
the artifacts at startup. The three isolated Python environments use their
released Kev, Vela or Jiwo runtimes behind `tools/serve_laya_model.py`; precision,
temperature, versions, token use and peak RSS are reported with every score.
CPU FP32 is qualified separately from any future acceleration path. A bundle
must enumerate its source, model, tokenizer and serving artifacts; the generic
loader cannot certify dependencies omitted from that manifest.

Every offered ID must have a finite probability in both the raw and calibrated
distribution. Kev's native logits avoid its public API's four-decimal rounding.
Selection samples the calibrated distribution with probability `1-epsilon`
and the full menu uniformly with probability `epsilon`. The logged behavior
propensity is `(1-epsilon)*p_calibrated + epsilon/N`; guided preference weights
are never multiplied into model probabilities. Requests are persisted before
inference, then choices/errors before execution, with state/menu/request/bundle
hashes, RNG state, runtime, token counts and latency in `model-decisions.jsonl`.

Kev and Jiwo reject overflowing complete inputs. Vela's declared native
overlapping state-window mode covers every state token, includes the complete
menu in every pass, then averages raw logits before temperature calibration.
Coverage checks reject gaps, lost options and further per-pass truncation.
Windowed coverage does not imply simultaneous visibility of the whole state.
An oversized menu that cannot fit with this method fails closed.

Batch arms explicitly declare `policy` and, for models, `model_bundle`.
Optional per-arm `concurrency` limits preparing/running jobs and retained
cleanup reservations. Set it to one for each single-request server; the queue
can admit another arm while an arm is capped. Global host admission remains
authoritative. The model cohort retains live speed 2 and the common tick-only
18,000-tick idle guard, 540,000-tick window and 7,200-second trial cap. Inference
latency consumes game time, so this compares deployable policy packages,
including their inference cost. It does not isolate strategic ranking quality.

`tools/prepare_laya_model_warmstart.py` streams indexed raw/zstd historical
trajectories and exports grouped train/calibration/dev partitions. Its narrow
receipt-qualified release teaches weak imitation of locally useful submission
or production decisions. It excludes unresolved fault suffixes, waits, gates
and retirements; deduplicates/quarantines shared inputs; caps trajectories;
and validates complete tokenizer admission and target permutations. A positive
local receipt is not optimality, episode success, or a counterfactual preference.
The first reviewed release is 2,506 rows from C35–37: 1,729 train, 376
calibration, 401 dev. Environment RL and adoption require later independent
evidence. Raw archives and earlier releases are preserved.

Offline validation: 73 focused adapter/runner/batch checks and the fast gate
(1,345 passed, one skipped). Independent implementation review found no
material defect. Real inference qualification receipts and launch identities
live outside source under `data/laya-models/qualification/` and the dated
`model-launch-20261026` evidence. No completed Factorio model cohort or model
performance improvement is established by these tests.

Lifecycle: use a new frozen disposable cohort after its predecessor drains;
never replace an active campaign's source or installed mods. Kev's separate
Starship supervised warm start uses a frozen data/recipe/asset manifest and an
eight-hour user-service limit. It does not promote a checkpoint or change the
released base-model arms. No push is authorized.
