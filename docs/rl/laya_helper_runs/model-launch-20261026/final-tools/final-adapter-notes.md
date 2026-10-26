<!-- Path: data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/final-adapter-notes.md | Purpose: Document the final-only logical120 adapter, parent evidence contract and explicit accounting gaps. -->

# Final adapter — 2026-10-27 IST

Implemented `final_accounting.py` and `test_final_accounting.py` alongside this note. This is a derived report seam, with synthetic CPU verification only. **No final invocation or live outcome calculation was performed.** The current cohort remains under parent supervision. No services, SSH, lifecycle, routing, account/model/options, refs, commits, freeze, worlds or configuration were changed. No other agents were used. Only the three requested files were written; test fixtures use automatically removed temporary directories outside the repository.

The scope follows the [accounting/admission brief](../../../../docs/rl/laya_helper_runs/model-launch-20261026/autonomy12h-accounting-admission-20261027.md): authoritative original7/transferred113 union; fresh-ever, explicit-end and version projection; full planned denominators; parent-owned closure and raw join certification. The prior 55 terminal certifications remain status-based semantic receipts. References supplied in `certification_receipts` are retained without being promoted into fresh/version certification. This implementation does not import the top-level delta helper, construct a Batch/CohortLedger, or call run/resume/save/report methods from the frozen runner. It has no model, tokenizer, runtime-source or serving imports.

## Final invocation, owned by the parent

Only after closure and immutable evidence collection, run this with **explicit absolute paths** (placeholders below are not current receipt paths):

```bash
python -B /absolute/repo/data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/final_accounting.py \
  --snapshot /absolute/closed-evidence/host-health.json \
  --transfer-plan /absolute/immutable/transfer-plan.json \
  --closure-proof /absolute/closed-evidence/closure-proof.json \
  --output-dir /absolute/analysis/new-final-projection
```

The output directory must not exist; its parent must exist. It cannot contain an input or lie inside an authoritative registry/world. Inputs are never rewritten. The adapter writes only `report.json` and `report.md` into the fresh derived directory. Exit **2** refuses invalid/nonterminal/ambiguous inputs before report creation; exit **3** emits a guarded projection with explicit material gaps; exit **0** means the projection and required parent supplement envelopes are present, not independently audited truth, admission, adoption or lifecycle authorization. A write failure can leave a partial new output directory; preserve it and choose another fresh directory after resolving the failure.

The report checks the exact Cartesian set `{guided,kev,vela,jiwo} × 80300..80329`, 120 unique IDs, 30/arm and the seven named original IDs. Each transferred row must come from its arm's registry named by the authoritative plan. The original registry must contain the 113 unstarted/unreserved administrative `cancelled_queued` copies; these enter an appendix with **zero additional trials**. Segment budget cuts remain logical outcomes. Original/transfer per-arm denominators remain 1/29 for guided and 2/28 for each other arm. Logical rows must match their owner snapshot exactly; duplicate IDs, attempts, worlds, run IDs, journals and terminal acceptance/summary ambiguities are refused.

## Parent closure proof contract

All JSON inputs are objects. Use the actual `laya-model-cohort-host-health-v1` collector output and `laya-model-capacity-transfer-v1` plan. `registry_snapshots` references the **five copied status snapshots**, not mutable live registries. File references have `{"path":"/absolute/evidence/file","sha256":"<64 lowercase hex>"}`; optional line/offset/byte/record metadata is preserved. For JSON files, file SHA256 means the exact bytes, not canonicalized JSON.

The closure object has these fields:

```json
{
  "version": "laya-final-accounting-closure-v1",
  "snapshot_sha256": "<exact host-health.json SHA256>",
  "transfer_plan_sha256": "<exact authoritative plan SHA256>",
  "registry_snapshots": {
    "/absolute/source-registry": {"path": "/absolute/copied-source-status.json", "sha256": "<file SHA256>"}
  },
  "owned_reservations": [],
  "reservation_receipt": {"path": "/absolute/reservation-proof.json", "sha256": "<file SHA256>"},
  "units": {},
  "attempts": [],
  "predicate_contract": {},
  "comparisons": [["guided", "kev"], ["kev", "vela"], ["vela", "jiwo"]],
  "capacity_periods": [],
  "supplements": {}
}
```

The abbreviated registry map above must contain all five entries, with hashes equal to the health snapshot's `registry_metadata[registry].sha256` and the copied files. The exact five cohort unit names are the original `laya-model-base-2123-miniship-20261026.service` plus `laya-model-scale15-{guided,kev,vela,jiwo}-2123-miniship-20261027.service`. Do not include model servers, slices or scheduler units in the required-unit map. They may remain in the preserved health context.

Each required `units[name]` entry contains `query_ok:true`, `ActiveState`, `MainPID`, `Result`, `ExecMainStatus`, `cgroup_empty:true`, `processes_empty:true`, and a pinned `receipt`. The four systemd values must exactly match the embedded health observation, including string versus numeric representation. Only `inactive`/`failed` and PID zero close physical activity. A failed unit or nonzero/non-success result remains visible and creates `unit_non_success`; it is never turned into a successful inactive receipt. Missing queries, process/cgroup release or reservation receipts refuse accounting. The parent establishes collection timing, physical scope and authenticity; hashes do not establish simultaneity.

For every actual preparation/runner attempt (including prepare failures), supply an `attempts` entry:

```json
{
  "job_id": "kev-s80300",
  "attempt": 0,
  "registry": "/absolute/authoritative-owner-registry",
  "job_sha256": "<canonical attempt snapshot SHA256>",
  "run_id": "<actual run ID, or null if no runner existed>",
  "start_receipt": {
    "path": "/absolute/preserved-batch-events.jsonl", "sha256": "<file SHA256>",
    "event": "attempt_started", "event_id": "start:kev-s80300:0"
  },
  "terminal_receipt": {
    "path": "/absolute/preserved-batch-events.jsonl", "sha256": "<file SHA256>",
    "event": "attempt_terminal", "event_id": "terminal:kev-s80300:0"
  },
  "release": {
    "runner_released": true, "factorio_released": true, "ports_released": true,
    "receipt": {"path": "/absolute/scoped-release.json", "sha256": "<file SHA256>"}
  },
  "journal": {"path": "/absolute/closed-clean-decisions.jsonl", "sha256": "<file SHA256>"},
  "provenance": {},
  "certification_receipts": []
}
```

`job_sha256` uses this module's `digest(attempt_snapshot)`: UTF-8 JSON, sorted keys, `separators=(",", ":")`, `allow_nan=False`. For the current row, remove injected `registry` and `previous_attempts`; hash each prior attempt as recorded. Do not hash a reconstructed subset. The ledger start event actually precedes runner launch and can belong to a failed preparation. Both receipt event identities must bind the job and attempt. An actual attempt must have a terminal status, explicit `admission_reserved:false`, no `cleanup_error`, preparation/cleanup timestamps in order, and, if started, a runner exit timestamp/return code in order. All release evidence is required. Unstarted logical dispositions receive no fabricated attempts, world, run ID or journal.

`journal` is optional when unavailable, creating explicit unknown/unobserved endpoints and gaps. With a journal it needs the actual run ID. The adapter streams the explicitly supplied clean file once, verifies its full hash/newline integrity and stable file identity, and retains only acceptance/summary fields with exact line/offset/byte/line-hash receipts. Other rows are parsed for JSON framing and discarded; no model/source/outcome semantic re-audit occurs. JSON parse/framing/hash failures refuse the invocation. Receipt paths other than the explicitly loaded JSON inputs and journals are **retained parent attestations, not dereferenced or rehashed**. In particular, this seam does not independently certify ledger matches, pending joins, stopped processes or receipt authenticity.

Retained attempts are never silently dropped. Duplicate attempt numbers are refused. More than one retained row requires `attempt_resolutions[job_id]` with `basis:"current_attempt_only"`, `selected_attempt` equal to the current recorded attempt number and a nonempty `reason`. All actual attempts must still have terminal/release proofs and separate projections. Logical endpoint aggregation uses the current recorded row, never whichever attempt passed. A historical nonterminal/unreconciled attempt remains a refusal until the parent provides reconciled terminal evidence; this adapter does not repair or resume it.

## Predicate and endpoint contract

`predicate_contract` declares `predicate_source_sha256`, `milestone_versions` (`C2:c2-v1` through `C6:c6-v1`), `source_save_sha256`, `freeze_sha256`, `mods_sha256`, `runtime_revision` and `window` (retain the full window settings). `freeze_sha256` means the same original freeze receipt as `transfer-plan.source_freeze_sha256`. Each attempt's `provenance` carries those bindings plus its own `baseline_sha256`; model/bundle identities and other source receipts can also be retained there. The parent obtains and certifies these bindings from preserved identities, without asking this adapter to load code/models. Missing context yields unknown endpoints and material gaps; unequal source/window/mod/runtime/freeze context is incompatible and excluded from known denominators.

The inspected frozen `OperationSession.acceptance()` writes `fresh`, latched `statuses` and `checks` for **nonpassing phases only**. It does not journal each predicate's version label. Therefore compatible versions are explicitly labeled `parent_source_binding`; absent check/version fields are not invented. Any explicit `checks[phase].predicate_version` is also checked. An equal `c5-v1` label with a different predicate source hash is incompatible. This avoids pooling historical C39 C5 semantics. The module does not re-evaluate C5's 120-second production window or C6's new advanced circuits plus owned downstream consumer output; those semantics depend on the parent's source binding/raw certification.

For each phase C2..C6:

- **Ever** uses `fresh[phase]=="passed"` over acceptance rows, crosschecking running latched statuses, summary `accepted`/`accepted_ever`, and any corresponding cached snapshot fields. A latch-only pass creates a mismatch/unknown, never a fresh pass. With no pass, any missing/unknown sample or missing end leaves ever unknown; a fully observed failed sequence with an explicit end is failed.
- **Final** uses only the `at:"end"` fresh observation. An observation `statuses.error`, null/missing phase, absent end or incompatible context stays unknown/incompatible. A last partial sample or cached final list cannot substitute. An actual attempt with no acceptance has unobserved ever and unknown final; an entirely unstarted logical job has unobserved endpoints.
- Every arm, ownership stratum and capacity group has passed/planned, passed/known, failed, unknown, unobserved and incompatible counts. Strict final is not sustained service. Proxy `phases`/`phase_times`, `laya-c6-choice-output-v2`, and research availability/earned receipts remain separate raw endpoint columns.

Each explicitly declared comparison produces C2/C3/C4/C5/C6 **ever and final** paired tables over all 30 intended seeds, regardless of completed/error/unstarted status. All 25 left/right state cells are retained. Known pairs require two passed/failed endpoints; unknown/unobserved/incompatible pairs never become losses or disappear from planned N. The exact two-sided conditional McNemar/binomial probability is `min(1, 2*sum(comb(b+c,k), k=0..min(b,c))/2**(b+c))`, with rational numerator/denominator retained. Zero discordances give p=1, including no known pairs, which is not affirmative equivalence evidence. Comparisons are parent-declared; undeclared pairs are named rather than invented after outcomes. Matched policy seeds on one copied save do not establish new-terrain generalization.

## Capacity and required supplements

`capacity_periods` contains nonoverlapping named intervals with `start_utc`, `end_utc`, and a pinned scheduler/UTC-mapping `receipt`. Time values may be UNIX seconds or ISO timestamps with an explicit timezone. Supply original target3, scale15 before quiet, quiet target4 and restored ceiling15 intervals from real parent evidence. An attempt is wholly contained only when its actual preparation-to-cleanup timestamps fit in one interval; crossings are `mixed`, uncovered times are `period-unknown`, and unstarted rows are labeled `unstarted`. No file mtime or game tick supplies choice UTC. Actual-period attribution is independent of ownership; parent scheduler gate/observed occupancy and overlapping exposure evidence belong in the supplement. The report does not sum overlapping period exposure counts or make a quiet-period causal claim.

These parent supplement envelopes are required: `raw_join_certification`, `pending_records`, `latency`, `resource_deltas`, `identity_evidence`, `capacity_evidence`, and `faults`. Missing ones stay explicitly unverified/blocked. Non-fault verified envelopes need `status:"verified"`, a pinned `receipt`, and the parent's actual payload; they are retained without being independently audited. In particular, the parent must certify original 55 carried plus newly terminal joins/prefix hashes, pending request/outcome explanations, one-run/index identities, final closure, save/freeze/mod/model/bundle identities and scoped release. A nonempty pending set must be explained, including terminal backend errors; it cannot be hidden by supplying only a boolean envelope.

`faults` can be provided as a single, already joined, parent-certified normalized supplement:

```json
{
  "status": "verified",
  "receipt": {"path": "/absolute/fault-certification.json", "sha256": "<file SHA256>"},
  "covered_attempts": ["kev-s80300:0"],
  "events": [{
    "event_id": "<unique incident ID>", "job_id": "kev-s80300", "attempt": 0,
    "run_id": "<actual run ID>", "index": 1,
    "category": "typed_fault", "code": "<source code>", "classification": "bug",
    "receipt": {"path": "/absolute/joined-source-outcome.jsonl", "sha256": "<file SHA256>"}
  }]
}
```

Coverage must include **every** actual retained attempt (the example is abbreviated). Preserve the `(run_id,index)` source→clean join once; source and clean copies are not separate incidents. Duplicate event IDs or identical attempt/run/index/category/code identities are refused. Counts and affected-job counts are broken out by category, code, classification, arm, ownership, arm×ownership and capacity. Categories remain separate: `typed_fault`, `ordinary_block`, `tool_error`, `safety_error`, `untyped_fault`, `backend_error`, `runner_failure`, `harness_failure`, `prepare_failure`, `timeout`, `oom`, `reclaim`, `cleanup_failure`, `evidence_parse_error`. Without certified coverage, counts are **null**, not zero. Summary `failures`/`codes`/`classifications` are retained as observations and are never added to joined incidents.

Latency/resource/model-menu auditing is intentionally a required supplement rather than another large journal auditor. Supply success client end-to-end latency n/median/nearest-rank p95/max from individual values merged once, backend error/timeout coverage separately, and menu-size/token coverage. Do not average percentiles or invent queue-versus-compute timing. Resource events require counter deltas and their interval/receipt, not sums of repeated cumulative snapshots. The adapter does not implement, certify or infer those measurements. `status:"verified"` is a **parent claim**; status presence alone is not substantive certification. Executive use must inspect the retained payload and receipts.

## Validation and remaining boundary

Focused command (no broad suite, pytest cache or bytecode writes):

```bash
python -B data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/test_final_accounting.py
```

Result: **17 focused CPU tests passed**. No full suite or live final report was run.

Fixtures use 120 tiny planned rows and eight small synthetic clean journals, matching the frozen acceptance/summary structure with absent passed-phase checks/version labels. Tests exercise 7+113 ownership/admin exclusion, 30/arm/seed bounds, nonterminal refusal before journal reads, duplicate/retained attempt handling, unit/reservation/cleanup evidence, fresh-ever versus latch/final, missing end/error/null observations, version/source/window mismatches, exact paired arithmetic/full N, mixed capacity periods, distinct fault categories/deduplication, pinned input hashes and fresh derived-only output.

Material limitation: this is unit-test evidence only. Parent final invocation, raw join certification, resource/latency supplements, lifecycle proofs and all executive decisions remain outstanding. There is no adoption verdict or successor admission. **Lifecycle action from this implementation: none.** Preserve Personal_2 and the existing selected model/options; no routing check/change was attempted.
