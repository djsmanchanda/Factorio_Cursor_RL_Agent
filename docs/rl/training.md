ï»¿<!-- Path: docs/rl/training.md | Purpose: Describe training scenarios, workers, evolution, autoresearch, and observation. -->

# Training and autoresearch

## Adopted director migration

The target policy is the Laya autonomous director in
`docs/rl/Laya_director_final_plan.md`.  The current `DiagonalLinUCB` candidate
selector and hand-written production cascade are baselines to beat and retire,
not policy architectures to extend.  Training continues to own disposable
worlds, measured outcomes, lineage, held-out evaluation, and promotion.

During M1, while the runtime operation compiler and telemetry are not yet
integrated, the current loop remains active for evidence collection.  New
deterministic work is limited to observations, operation compilation, hard
validation, execution, measurement, or a blocker in those mechanics; new
strategic behavior belongs in the learned director and its curriculum.

The local Laya data/training path is now available:

```bash
.venv/bin/python tools/build_laya_dataset.py \
  --database data/training/experience.db \
  --derive-seed-splits \
  --step-level \
  --output /tmp/factorio-laya-cases.jsonl
.venv/bin/python tools/laya_preflight.py
.venv/bin/python tools/train_laya_4060.py \
  --cases /tmp/factorio-laya-cases.jsonl \
  --model-dir /path/to/convaiinnovations/laya \
  --output-dir /path/to/local/laya-4060-checkpoint
.venv/bin/python tools/evaluate_laya.py \
  --cases /tmp/factorio-laya-cases.jsonl \
  --checkpoint /path/to/local/laya-4060-checkpoint \
  --base-model /path/to/convaiinnovations/laya \
  --split held_out \
  --output /path/to/local/laya-4060-checkpoint/evaluation-held-out.json
```

For a later run, the profile is tunable without changing the learning
objective. After a short VRAM smoke test, `LAYA_MICRO_BATCH=2` with
`LAYA_GRADIENT_ACCUMULATION=16` preserves the effective batch while reducing
per-step overhead; `LAYA_GRADIENT_CHECKPOINTING=0` can improve throughput when
the measured peak remains safely below the 8 GiB limit. The active run is not
changed by these settings.

The training command requires separate `train` and `calibration` case splits,
an exclusive CUDA window, and the optional Laya/Transformers/PEFT/SafeTensors
dependencies.  It fails closed when CUDA or calibration data is unavailable;
it does not fall back to the deterministic policy or publish to a model hub.
The checkpoint is a PEFT adapter plus the fully trained typed decision head;
`tools/evaluate_laya.py` loads it with the immutable base model and writes the
held-out promotion evidence.  A checkpoint is eligible for disposable shadow
only after that report is ready; real Nauvis promotion remains a separate
explicit gate.
The RL Observatory `/laya` page is the control and telemetry surface for this
pipeline: it can start/stop disposable fine-tuning after explicit confirmation
and shows the trainer's loss, steps, gradient norm, entropy, rewards,
throughput, VRAM, loss history, and benchmark reports. It never controls
Nauvis.

### Completion-first counterfactual evidence

Counterfactual labels now use `laya-counterfactual-v4`, defined in
`schemas/laya_counterfactual_label.schema.json`. Each alternative preserves
its terminal status, cause, failure kind, safety counts, progress, replay
identity and cost. Preference targets give positive credit only to completing
alternatives. A cheap timeout cannot outrank an expensive completion.
All-failed continuations remain in the label store with an unresolved reason;
they do not produce a positive wait training case. Tool, environment,
validator and safety failures are quarantined from policy preferences.
Confidence compares the best two completing alternatives against replay noise.

Versions 1–3 can be re-exported by `tools/laya_labels_to_cases.py` only when
their original replay journals are available. Costs-only converted `cf-*`
cases are excluded from both dataset building and features training. Re-export
old round labels into a separate directory, preserving originals:

```bash
mkdir -p data/laya-pi/relabel-v4
.venv/bin/python tools/laya_labels_to_cases.py \
  data/laya-counterfactual/labels/ov-r27-p*.jsonl \
  --temperature-ticks 100 \
  --output data/laya-pi/relabel-v4/ov-r27-cases.jsonl
```

The overnight driver's future launches prefer corrected round files with the
same basename under `data/laya-pi/relabel-v4/`; override that directory with
`LAYA_RELABELED_CASE_DIR`. The features teacher trains only the seed-derived
training split and now applies case/question weights. Existing checkpoints
retain their old training history and require a new fine-tune for this fix.
Dataset ETL also resolves corrected siblings when older snapshot drivers pass
the original case paths. Rounds 19–30 were re-exported without changing their
source files: 4,915 usable cases remain and 1,138 all-failed labels are excluded.

New workflow batches write a frozen expected-seed manifest before execution
and record declared scenario, guideline, renderer, context, decision-budget
and clock conditions plus safety counts. The lab comparator rejects duplicate
or missing seeds, absent evidence and mismatched conditions. It reports mean,
p95, completion wins/losses, completed-pair cost wins and errors separately.
Lower cost when both runs fail is not a completion win. Legacy batches lacking
these conditions require fresh evaluation. New overnight launches use fresh
`rollout-v4` baselines and retain their continuation when comparison is blocked.
Comparisons run from the driver's frozen code checkout.

These are disposable lab diagnostics; they do not establish deployed mod/save
hashes, a win against the full deterministic workflow or production authority.
The stricter matched-workflow promotion gate remains separate. Already running
snapshot-based jobs keep their old labelling/training behavior. No lab Lua
deployment is required: launch a new driver/consumer to use the new code, then
train and independently evaluate a new checkpoint.

### Revised level-3 shared supply and joint service

The revised level 3 uses `connected-workflow-l3-v2-*` scenario identities and
the `shared-plate-supply-v2` / `joint-sustain-v2` curriculum tags. Both iron
furnaces feed one plate chest, with separate plate feeds for gears/belts and
circuits. The second furnace can contribute to either chain. Level-1 and
level-2 scenario hashes and cell layouts retain their earlier contracts.

The lab requires all product targets over a common sustain interval;
previously satisfied products reopen on a current shortfall. Expansion
completion uses actual phase-owned furnace counts through the lab adapter's
`branch_measurements`, never a global count that copper could satisfy. Lost
entities reopen that construction obligation. This adds completion evidence;
repair/upgrade operations remain the next action-space milestone.

Belt demand is provisionally sampled from 0.06–0.35/s and circuit demand from
0.02–0.18/s. Recipe arithmetic motivates the range, but it does not establish
real capacity or the distribution between the plate feeds. Geometry,
connections, budget and measurement behavior are tested offline. Before
adding this level to automatic rotation, deploy the lab to stopped disposable
workers, restart affected workers/GUI and Python consumers, and run a stepped
capacity sweep with and without expansion across basic/fast variants. Verify
that expansion improves **simultaneous delivered output**, check a longer
steady-state interval for buffering artefacts, and retain earlier-level
regression cohorts. Narrow or extend the demand range from those measurements.

The September 30 deployment and capacity checks are recorded in
`Laya_Level3_Live_Verification_2026-09-30.md`: the scripted rollout completed
32/32 validation seeds, and paired 25-minute measurements confirmed that a
second furnace roughly doubles average belt delivery across all four tier
combinations. Individual short delivery windows remain bursty.

New driver launches rotate levels 2, 1, 3 by default. Set `LAYA_LEVELS="2 1"`
to retain the earlier rotation. Level 3 starts with seed 8101 and a scripted
continuation until its first accepted features round; later rounds use the
learned continuation. Baseline, Laya and final features evaluations cover every
enabled level. Console resume reads explicit level/seed/count log records,
preserves consumed partial-round seed blocks and uses the accepted continuation
record when present. Older histories without those records retain their
two-level fallback. No new training run is launched by this configuration change.

The runner rejects level-3 completion from a lab lacking the declared joint
measurement version. Old level-3 checkpoints/results are not promotion
evidence for this new scenario. No fleet deployment or rotation change is
performed merely by changing these repository files; deployment evidence is
recorded separately.

### Resumable local fine-tuning

Local fine-tuning writes atomic adapter, optimizer, scheduler, scaler, and RNG
snapshots beneath `<output-dir>/checkpoints/`. By default it saves every 250
optimizer steps and retains the newest three snapshots. The interval and
retention can be changed without modifying the objective:

```bash
export LAYA_CHECKPOINT_INTERVAL=400
export LAYA_CHECKPOINT_KEEP=3
export LAYA_RESUME_CHECKPOINT=latest
```

`LAYA_RESUME_CHECKPOINT=latest` resumes from the newest compatible snapshot in
the selected output directory; an explicit snapshot directory may be supplied
instead. An explicit completed PEFT adapter (one without `trainer_state.pt`)
is treated as a deliberate warm start, not an exact optimizer resume. The
resumable launcher retries a failed training process from the newest snapshot,
while a failed run with no snapshot starts from the initial step-00000000
snapshot. Snapshots are local recovery artifacts, not promotion checkpoints;
held-out and test evaluation still gate every final adapter.

### Disposable shadow batch

An evaluated adapter can now be compared against the incumbent policy on a
single disposable worker. Laya receives the same validated candidates and
state, but the incumbent remains the only policy allowed to execute. Each
shadow decision is appended to JSONL with agreement, confidence/escalation,
and the baseline action:

```bash
.venv/bin/python tools/run_laya_shadow_batch.py \
  --workers training-workers-linux.json \
  --rcon-secret-file /path/to/training/rcon-password \
  --checkpoint data/laya-checkpoints/laya-flex-v1 \
  --base-model data/laya-base \
  --count 10 \
  --attempts-per-scenario 1 \
  --output data/laya-shadow/shadow-decisions.jsonl
```

This command is disposable-training-only. It does not mutate Nauvis, does not
update the baseline policy, and does not promote the Laya checkpoint.

### Full-loop workflow traces and human coaching

#### Bounded Laya Guided Runner protocol

The Laya Guided Runner advances one strategic seam at a time:

```text
observe -> serve_goal -> choose_operation -> compile legal options
  -> select site/scale/route -> execute one transaction -> measure
  -> accept, recover, wait, or escalate
```

Execution and safety remain deterministic toolset responsibilities. Laya never
receives a full build plan or unbounded factory history; it selects a compact
option identity and the tool registry resolves that identity to the complete
validated plan. `orchestrator/laya_decision_flow.py` defines stable step IDs,
parent trajectory links, bounded option summaries, and fail-closed transitions.

The `factorio-decision-v2` representation uses one shared, non-truncating
sequence builder for training, evaluation and inference. It measures actual
model tokens, including instructions, option markers and state. Compact
columnar fields and shared option features retain the information without
repeating JSON keys; rule/tool provenance remains in the journal. The model
receives the two most recent decision records, active failure and transaction,
while the complete history remains external. Overflow rejects the request.
The earlier JSON-envelope estimator is not a model sequence budget.

Existing `laya-flex-v1` was trained with the old truncating representation and
remains preserved. New default inference rejects incompatible trained adapters;
`--allow-legacy-sequence` is available only on evaluation and shadow commands
for explicitly labelled historical diagnostics. Do not relabel an old adapter
as v2; collect, train and evaluate the new representation.

Objective drift and failed transactions can be recorded as bounded
`FailurePacket` replay requests in `training/laya_failure.py`. Packets are
disposable-training-only, carry the state/decision/tool provenance needed for
reproduction, and may produce reviewable `ImprovementProposal` records in an
append-only ledger. Policy failures become fine-tuning cases; observation,
tool, validator, documentation, and environment failures become regression or
improvement work. The ledger never edits authoritative code or documentation
by itself.

The director boundary now accepts explicit multi-step workflow decisions rather
than assuming every case is `select_candidate`. A transition may carry typed
`workflow_decisions` for goal serving, operation family, site, scale, route,
research, upgrade, recovery, or wait/act choices. `training/laya_workflow.py`
validates those boundaries and fails closed on an unavailable or low-confidence
model response; candidate generation, hard validation, execution, and outcome
measurement remain outside the model.

The disposable episode runner now preserves every staged decision's available
candidate set, selected candidate, pre/post observations, execution report, and
outcome. Those stage traces are emitted as additional typed workflow cases by
the dataset adapter, so deterministic execution contributes evidence without
becoming an undisclosed strategic fallback.

The Factorio mod also includes a **Laya Coach** window. It is observation and
annotation only: it writes bounded JSON records to
`script-output/factorio_mod/laya_guidance/`. A note without options and a chosen
option is advisory context; it is never turned into a fabricated training
label. When a disposable Laya runner is started with `--guidance-path` pointing
at that directory, the newest note per focus is injected into the next model
state as a soft priority/goal. It does not select an action or bypass
validators. Labelled records can be merged into a dataset with:

```bash
.venv/bin/python tools/import_laya_coach_data.py \
  --coach-file /path/to/script-output/factorio_mod/laya_guidance/coach.json \
  --base-cases /tmp/factorio-laya-cases-all.jsonl \
  --output /tmp/factorio-laya-cases-with-coaching.jsonl
```

This creates the data path for the next full-loop fine-tuning run. The current
`laya-flex-v1` adapter is unchanged and remains mining-candidate-only until a
new workflow dataset is collected and evaluated.

## Current system

- `factorio_training_lab/` owns isolated `training/*` surfaces and `training-*` forces.
- `training/scenarios/` generates seeded tasks and budgets.
- `training/candidates/` currently supplies baseline candidates while the structural action space is expanded.
- `training/episode.py` executes, measures, rewards, and always recycles an episode.
- `training/store.py` preserves transitions, policy lineage, worker leases, and research evidence.
- `training/policies.py`, `population.py`, and `evaluation.py` select, mutate, and gate policies.
- `training/research/` accepts bounded proposals from a local OpenAI-compatible model.
- `tools/training_observer.py` exposes read-only progress, bottlenecks, lineage, proposals, and human guidance.

The current implementation is a starting point, not the architectural ceiling. Candidate selection should evolve toward parameterized structural generation and mutation while retaining validators and reproducibility.

## Episode contract

Each episode declares a seed, isolated surface and force, fixtures, inventory budget, allowed entities, objective rate, sustain duration, time limit, and reward weights. Electricity fixtures are role-tagged: generators must join a pole network, while accumulators are storage that must charge and discharge through that same network rather than being counted as generation. A transition records the observation, available or generated actions, chosen action, execution report, structured outcome, next observation, and decomposed reward.

Training evidence survives surface recycle, process restart, and save replacement.

A controller pins its scenario and transition schemas at process start. Repository edits therefore cannot change the contract beneath an active batch; activating a new contract requires an explicit controller restart and, when Lua changed, the matching training-mod deployment.

## Curriculum

1. Mine one resource and deliver a target rate to a sink.
2. Produce one solid item from supplied precursors.
3. Produce and route one fluid without contamination.
4. Generate and sustain an electricity target.
5. Scale one coherent line, such as 2 -> 6 -> 24 furnaces, in reserved space.
6. Connect distant areas using efficient belts, poles, and roboports.
7. Diagnose and repair deliberately damaged production chains.
8. Build multi-stage intermediates, science, and demand-driven expansion.

Each family should randomize geometry, resources, demand, clutter, fixture placement, and budgets. The mining-delivery source is an integral 2x2 producer sampled in clear space near the resource patch, not a special fixed origin. Train and held-out seeds remain separate.

## Rewards and selection

Safety failures are disqualifying rather than tradeable for scalar reward. Among safe attempts, measure:

- objective completion and sustained throughput;
- time to first output and time to target;
- material, entity, route-length, energy, and infrastructure cost;
- idle/starved capacity, failed placements, and unnecessary transfers;
- successful reuse, repair, and expandable structure.

Store reward components separately so the observatory and autoresearch can identify why a policy changed.

The active mining profile is `mining-throughput-cost-v1`. Its primary signal is the measured terminal-stage rate: reward rises linearly to the demanded rate, then remains positive at one-tenth that slope above demand. Material cost is normalized against the scenario's legal construction budget and scaled by useful output, so equal-output candidates prefer lower cost while an empty cheap plan cannot beat productive work. Ramp-up time is normalized against the episode limit, rewarding factories that reach sustained demand sooner. The relative scales keep small production gains more valuable than similarly small increases in material or time cost. Candidate geometry separately records route efficiency, poles, occupied land, and productive drill use as secondary evidence.

The shared fluid-structure candidate generator exposes the eight square
symmetries (four rotations and their reflections) and both long-row and
opposing-paired-row structures. Hard validation rejects entity
collisions, unreachable fluid attachments, contamination, and disconnected
required power. A fluid curriculum can record routed pipe entities, poles,
occupied land, and the cost of the retained expansion seam as decomposed
geometry evidence, allowing training to discover compact shared-header refinery
and chemical-plant structures without selecting from a catalog of complete user
blueprints.

Policy promotion remains lexicographic. Safety, completion and sustained output
come first. Candidate learning now includes safe strategy failures and objective
timeouts with measured rewards; safety, execution, fixture, power and contract
failures remain excluded. Learning counts and successful-episode counts are
reported separately. Frozen held-out promotion still requires completed objectives
and cannot trade partial throughput for a completion regression. Fresh mining
checkpoints use `mining-efficiency-v2` (V3); immutable V1/V2 checkpoints retain
their feature contracts. `--reinitialize-mining-features` explicitly starts new V3
weights from the old hyperparameters and records the migration. Available drill
capacity/working/blocked/idle measurements enter observations; absent data remains
unknown. Non-staged mining scales patch and candidates with demand rather than
capping at eight drills, and rejects infeasible single-sink demands before an episode.

The next production curriculum should compare **upgrade versus expand**, not only repair versus duplicate. It should randomize assembler tier and quality, speed/productivity/efficiency modules, beacon support, inserter and belt tier, available footprint, capital budget, energy price, recipe demand, and research state. Rewards should use measured output and total lifecycle cost so an assembler-3, quality upgrade, module change, or added parallel machine wins only when its throughput, resource efficiency, energy, and land tradeoff is better on held-out scenarios. Repeatable productivity and mining research must enter observations as live modifiers; research cost and the downstream savings it creates are separate actions and reward evidence rather than hard-coded upgrade rules.

Use seeded elitist selection with diversity protection. Preserve champions, mutate several dimensions, and periodically test novel populations so a locally successful layout does not collapse exploration.

## Parallel training slots

### Native Linux workers

On native Linux, workers use the private headless runtime at
`~/.local/share/factorio-rl/runtime/factorio-2.1.20` with its matching
read-data root and isolated writable roots. They do not use `wsl.exe`, Windows
bridge directories, or a real-base save. Start with one disposable worker and
one logical slot; increase capacity only after the documented five-minute UPS
gate passes.

```bash
scripts/manage_linux_training_worker.sh bootstrap --worker-count 1 --slots-per-worker 1
scripts/manage_linux_training_worker.sh status --worker-count 1 --slots-per-worker 1
scripts/manage_linux_training_worker.sh start --worker-count 1 --slots-per-worker 1
python tools/run_adaptive_training_batch.py \
  --workers training-workers-linux.json \
  --rcon-secret-file "$HOME/.local/share/factorio-rl/training/01/worker/rcon-password" \
  --count 1 --episodes-per-policy 1 --policy-rounds 1 --initial-slots 1 --minimum-slots 1 --maximum-slots 1
```

`bootstrap` creates a fresh worker-local save unless `--source-save` names an
explicit disposable seed. It writes `training-workers-linux.json` with direct
server-owned `script-output` paths. `deploy` requires stopped workers; changes
to the training Lua mod then require `deploy`, worker restart, and controller
restart. Use `--runtime-root`, `--factorio-bin`, and `--read-data` only for an
intentional, separately validated Factorio version.

Each WSL Factorio process can simulate multiple isolated `training/*` surfaces concurrently. The adaptive local setup uses twenty isolated headless runtimes, each starting with eight active logical slots and capped at sixteen. Each runtime has its own game/RCON endpoint, save, `script-output` directory, worker IDs, surfaces, and forces. This removes the single-server simulation bottleneck while retaining shared policy and evidence storage.

Slots belonging to the same Factorio runtime must declare the same `instance_id` and identical endpoint details. Different runtime instances must keep unique ports and `script-output` paths. A stage uses one shared queue: scenario identity serializes access to its stable surface, but never permanently owns a slot. A slot that finishes immediately claims another unlocked scenario. Each policy cohort has at least the requested 100 terminal episodes and is automatically topped up to the current active-slot count, with fresh seeded refill attempts, so extra capacity is not left idle. Scale slots only while UPS, memory, report latency, and cleanup remain healthy.

Staged mining demand uses one persistent isolated surface per attempt. The default ladder is 10/s into sink A, then 30/s into sink A, then 60/s split across independent 30/s corridors into sinks A and B. Each stage advances only after its target is sustained for --staged-sustain-seconds; staged plans use express belts and loaders so transport does not cap the measured mining rate.

Obstacle-detour staged scenarios place protected wall fields between the mining
corridor and sink. Candidate belts and power avoid those immutable tiles, so a
successful route must make justified turns around the obstruction. Long runs
may enable seeded epsilon exploration with `--exploration-rate`; held-out gates
remain greedy and reject a successor when its action trace is identical to the
incumbent, preventing timing jitter from masquerading as policy improvement.

For long runs, use the adaptive controller after configuring enough logical slots:

```powershell
powershell -File scripts\manage_wsl_training_worker.ps1 -Action configure -WorkerCount 20 -SlotsPerWorker 16 -StaggerSeconds 2
powershell -File scripts\run_wsl_adaptive_training_batch.ps1 --count 100 --attempts-per-scenario 20 `
  --initial-slots 8 --minimum-slots 1 --maximum-slots 16 --step 1 `
  --episodes-per-policy 100 --minimum-ups-p95 50 --minimum-ups-p98 45 `
  --stability-window-seconds 300
```

The adaptive controller starts at eight logical slots on each server, samples tick advancement over each server's RCON connection every five seconds, and changes that server's capacity by one slot. Each server's capacity target is held for a complete five-minute rolling window. A healthy window opens one more gate on that server; an unsafe window closes one gate. A healthy window
requires both lower-tail safeguards: **P95 UPS >= 50** and **P98 UPS >= 45**, meaning
at least 95% and 98% of samples respectively meet those floors.

Capacity changes are graceful. The controller never interrupts a live episode for a
capacity decision. When one gate is closed on a server, its current episodes drain naturally;
their workers are not refilled, and the next stage starts only after the live count is
at or below the reduced target. This keeps completed episodes as valid fitness evidence
and prevents a scale-down from creating an abort/requeue storm.

Capacity stages and policy generations are deliberately separate. A policy remains
immutable through as many capacity stages as needed to obtain 100 terminal episodes;
only then is it updated into the next generation. Capacity changes never discard
completed episodes; only terminal evidence enters policy learning.

Live episode measurement is intentionally every five seconds rather than once per
second, keeping shared RCON/report-file work bounded. In the Observatory,
**Heartbeat / elapsed** distinguishes the last telemetry update from Factorio ticks
spent in the current episode.

For an unattended overnight curriculum, `scripts/queue_overnight_training.ps1` waits for the current controller PID, then stops the old five-worker set, bootstraps and starts twenty staggered workers, and runs these phases sequentially against a separate `data/training-overnight/` store: the 100-scenario/20-attempt adaptive baseline, followed by 100-test fixed-demand probes at 3/s, 10/s, and 30/s. The high-demand probes intentionally expose the current compact mining footprint's capacity gap as reward evidence; they do not mutate the deterministic runtime.

The preferred local worker uses the Linux headless build under WSL2:

```powershell
powershell -File scripts\manage_wsl_training_worker.ps1 -Action bootstrap -WorkerCount 20 -SlotsPerWorker 16 -StaggerSeconds 2
powershell -File scripts\manage_wsl_training_worker.ps1 -Action start -WorkerCount 20 -SlotsPerWorker 16 -StaggerSeconds 2
powershell -File scripts\manage_wsl_training_worker.ps1 -Action status -WorkerCount 20 -SlotsPerWorker 16
powershell -File scripts\run_wsl_training_batch.ps1 --count 100 --attempts-per-scenario 20
```
An optional native-Windows worker can run alongside the WSL runtimes for a
like-for-like performance comparison. It uses a separate profile and ports
(`35006`/`28006` by default), so configuring it does not alter or restart the
active WSL controller. Bootstrap it only when a comparison run is planned:

```powershell
$env:FACTORIO_TRAINING_RCON_PASSWORD = "<local training password>"
powershell -File scripts\manage_windows_training_worker.ps1 -Action bootstrap
powershell -File scripts\manage_windows_training_worker.ps1 -Action start
```

The bootstrap command creates `training-workers-windows.json` with sixteen
logical slots sharing one native Factorio process. Merge that generated worker
list with the WSL worker list only when starting a new controller; do not edit
the configuration consumed by an already-running batch. The adaptive controller
groups the Windows instance independently and applies the same one-slot,
five-minute UPS gates. Stop it with `-Action stop` after the comparison.

The Windows and WSL servers share the host's CPU and memory. Compare per-server
P95/P98 UPS at equal slot counts and record host utilization; a native server
may have a higher individual ceiling while lowering the aggregate ceiling.

To find the safe concurrency limit on one laptop, run bounded live probes rather than assuming twenty surfaces are safe:

```powershell
powershell -File scripts\benchmark_wsl_training_slots.ps1 -MaximumSlots 20
```

The probe grows from 4 to 20 slots in steps of 4, testing one disposable episode per slot. It stops before the next level when a stage falls below its 75% completion gate, average host CPU exceeds 90%, available memory falls below 4 GiB, or wall time exceeds twice the initial stage. Ordinary candidate timeouts are reported but do not by themselves prove a laptop-capacity failure. Its database, checkpoint, and live telemetry are written beneath ignored `data/training-capacity/`, so the measurements are a local capacity gate rather than policy fitness evidence.

For an already configured worker file:

```powershell
python tools/run_training_batch.py --workers training-workers.json --count 100 --attempts-per-scenario 20
```

Generate contracts without Factorio:

```powershell
python tools/generate_training_scenarios.py --count 100 --start-seed 0 --output-dir data/training/mining-delivery
```

## Alternating ore/refinery cohorts

The first two production families use a shared bounded scheduler. Family A (`ore-production`) mines ore to a sink; family B (`furnace-refining`) supplies ore from an isolated chest, smelts it in an electric furnace, and measures plate output. A cohort is assigned until its queue is empty; newly released worker slots immediately claim the next family while active episodes drain. A complete A?B pair advances the family policy index. The scheduler serializes each stable scenario surface so one surface is never provisioned concurrently.

Run a bounded smoke before scaling:

```powershell
python tools\run_ab_training_batch.py --workers training-workers-wsl.json --count 1 `
  --episodes-per-policy 1 --cycles 1 --max-workers 1 `
  --rcon-secret-file "$env:LOCALAPPDATA\Factorio-training-wsl-01\rcon-password"
```

The production run keeps the requested 1,000 terminal attempts per family cohort and can use the configured worker roster:

```powershell
python tools\run_ab_training_batch.py --workers training-workers-wsl.json --count 100 `
  --episodes-per-policy 1000 --cycles 3 --max-workers 1000 `
  --rcon-secret-file "$env:LOCALAPPDATA\Factorio-training-wsl-01\rcon-password"
```

The training Lua changes in this family require deployment plus restart of the affected training workers. They do not affect the deterministic runtime.

## Bounded autoresearch

The local LLM receives aggregate evidence and proposes changes inside an explicit experiment envelope. Today the safest envelope is allowlisted numeric policy, reward, and search settings. A later code-editing envelope may touch training-only files in an isolated worktree, but every proposal must pass focused tests, paired evaluation, resource limits, diff review, and automatic rollback before it can compete.

Do not run a large local controller model on the GPU at the same time as GPU-heavy training unless measurements show both remain healthy. Record model fingerprint, quantization, context, offload settings, prompt/decode timing, RAM/VRAM, and experiment result. Per-expert MoE optimization is a later measured optimization, not an assumption available from ordinary `llama.cpp` logs.

```powershell
python tools/run_autoresearch.py --model <model-id> --policy policy.json `
  --evidence evidence.json --parent-policy-id <policy-id>
```

## Optional CUDA learner

Factorio simulation, RCON, SQLite, JSON validation, and report I/O remain CPU work. The small online LinUCB selector also stays CPU-bound because each episode ranks only a few candidates; GPU launch and transfer overhead would make it slower. The optional reward/failure surrogate instead trains a batched model from completed transition evidence between controller stages. It is advisory only: it cannot write the database, select a live plan, or promote a policy.

Install its isolated Python 3.12 CUDA runtime without replacing the controller interpreter:

```powershell
powershell -File scripts\setup_rl_gpu_environment.ps1 -Action install
powershell -File scripts\setup_rl_gpu_environment.ps1 -Action status
```

Then train an external checkpoint after at least 32 terminal transitions:

```powershell
.\data\rl-gpu-venv\Scripts\python.exe tools\train_gpu_surrogate.py `
  --database data\training\experience.db `
  --output data\training\surrogates\mining-v1.pt --require-cuda
```

The workstation has 8 GB VRAM, so do not run the surrogate, a local LLM, and other GPU-heavy work concurrently. Run it between Factorio collection stages and require CUDA for experiments intended to inform policy research. If CUDA is unavailable, the training controller remains unaffected.

## Observatory and guidance

```powershell
python tools/training_observer.py serve
# Open http://127.0.0.1:8765

python tools/training_observer.py snapshot

python tools/training_observer.py nudge --focus throughput `
  --message "Prefer experiments that sustain direct-belt delivery." `
  --expires-generation 12
```

The site may focus a connected spectator on the owned active training surface. It does not control Nauvis, modify plans, or bypass evaluation. Human nudges are typed, expiring research context; they do not directly rewrite rewards or promote policies.

## Lifecycle

- Python-only training change: restart the affected batch, controller, or observer.
- `factorio_training_lab/*.lua`: deploy the training mod, synchronize `%APPDATA%\Factorio\mods\factorio_training_lab`, restart the affected training worker and GUI Factorio session, then restart its controller if necessary.
- Training changes never require redeploying the deterministic mod unless shared Lua code was deliberately changed too.

## Independent Laya implementation (2026-09-22)

See `Laya_Implementation_Plan.md` for the coexistence contract. The presentable
deterministic workflow remains usable until Laya outperforms it under matched
starting conditions. No existing controller or dashboard entry point is
redirected.

`tools/run_laya_director.py` exposes a separate opt-in disposable runner for
`mining_delivery` and `furnace_refining`, requiring the exact worker, scenario,
endpoint and a checkpoint capability manifest. It chooses operations, candidates,
waiting and recovery through Laya, invokes validated bound handlers, and records
full states, observations, transactions and failures. It does not yet implement
connected factory progression, arbitrary repair, research or production authority.
The model capability manifest is required evidence of intended scope, not proof
of model quality. It must be issued after training/evaluation, never invented to
bypass the gate. Untrained data collection uses the separate exploration gate
below instead.

The first connected-workflow contract is now available without changing that
runner's authority. `training/laya_work_graph.py` tracks prerequisite and
independent branches, explicit blockers, reservations, and measured closure or
reopening. `training/laya_supply_evidence.py` projects observed per-item stock,
rates, committed construction bills, and optional live-recipe ingredient demand;
unknown rates stay unknown. `training/laya_branch_decisions.py` exposes the
runnable branches as bounded `serve_goal` choices on disposable state, with a
hash of the complete evidence and a fresh-state check before resolving a
selection. These are read-only decision inputs, not executable operations or a
trained capability. The next step is a connected disposable scenario with
measured belt, gear, iron, and independent construction branches, followed by
an evaluated checkpoint that supports the new decision sequence.

The disposable training lab now exposes bounded per-item production rates and
their measured window lengths. `training/laya_lab_workflow.py` adapts one
isolated lab report into the work graph through
`training/laya_workflow_observation.py`; absent stock, demand, or capacity stays
unknown. `tools/inspect_laya_workflow_report.py --report <path> --branches
<path>` prints the projected graph and supply evidence for review. A focused
lab-report fixture reaches a bounded `serve_goal` question and rejects a stale
selection after the observation changes. This is still a read-only workflow
seam: the current executable runner handles only mining and furnace scenarios,
so the next implementation slice must register and validate distinct iron,
gear, belt, and construction operations in a connected disposable episode.

That slice exists as the `connected_workflow` lab family
(`training/scenarios/connected_workflow.py`,
`training/candidates/connected_workflow.py`): ore chest -> electric furnace ->
plate chest -> gear assembler -> gear chest -> belt assembler -> belt sink,
plus an independent medium-pole line toward a remote site. Each branch owns
one validated plan with its own poles; the budget equals their union. For
this family the runner asks one `serve_goal` question per step: build one
runnable, not-yet-built branch (prerequisites measured and closed in the work
graph), wait, or escalate. Gears are never offered before iron production is
sustained, while construction is available at any time. The model state
carries the compact work-branch and item-supply digest; journals record the
work graph at each decision and the final branch outcomes at termination.
The lab now reports ghost-free `live_entities` counts (construction evidence)
and matches existing entities by tile, so re-executing a plan is idempotent.
A scripted live probe on 2.1.20 produced about 0.6 plates/s, 0.2 gears/s and
0.4 belts/s and completed the belt objective in 45 s.

Volume: `tools/run_laya_workflow_batch.py --instance <instance_id> --slots N
--seed-start S --episodes E --game-speed G [--max-len 2048]` runs E exploration
episodes concurrently on N slots of one worker, sharing one locked GPU model,
and restores `game.speed` to 1 afterwards. Waits are measured in game ticks,
so speed changes wall time, not the decision's meaning. Data collection
forbids model escalation by default (`allow_model_escalation=false` in the
gate record; the next-best non-escalation option is used and labelled
`model_non_escalation`); decision and no-progress budgets still end runs.
`--max-len 2048` is an explicit inference override on the untrained base for
data collection only; trained checkpoints must be trained at the length they
run at. Observed: 32 connected episodes on 8 slots at 8x in 115 s wall, 28
completed, completion 4,138-27,503 ticks (median 7,925).

Outcome labels: `tools/laya_counterfactual_labels.py` replays each decision
prefix on its own replica surface, forces every alternative, and finishes with
a fixed rollout; cost is the lab's `completed_ticks` plus 10 ticks per unit of
built material (rule.sustained-efficiency). Base rollouts are repeated to
measure noise (median 180 ticks). `tools/laya_labels_to_cases.py` keeps only
faithful labels and writes soft-target cases for `build_laya_dataset.py
--cases-jsonl`. First v2 set: 48 seeds, 446 labels, 398 faithful
(dataset `74e70f15`).

Evaluation on 32 held-out seeds (`tools/compare_laya_batches.py`, same
objective): rollout 32/32, median cost 7,751; random 30/32, +3,610; the
features-only baseline (`training/laya_feature_baseline.py`) 32/32, median
5,806, better than rollout on 30 seeds; Laya `laya-serve-goal-v1` (2048,
80 LoRA steps) 26/32, median 14,279. Laya built branches in the right order
but chose `wait.1s` at nearly every wait point and ran out of decisions. That
is the known one-step-label trap: labels score "short wait, then the default
rollout", never "short wait forever". Next: policy iteration (collect states
from the trained policy, relabel with it as the continuation, retrain) and a
per-decision cost in the objective.

Policy iteration (2026-09-29). Batches and labelling spread across workers:
repeat `--instance` and give `--slots` per instance, e.g. four workers x four
slots = 16 concurrent episodes (32 held-out episodes in 20 s wall at 8x). 8x is
the fidelity ceiling: at 16x report latency added about 1,850 ticks per episode
and changed decisions, so scale with slots, not speed. The objective now
charges `--decision-ticks` (default 60) per decision in labels and
`compare_laya_batches.py`, so a policy that burns its budget on 1 s waits pays
for it. `--continuation features --features-model <json>` makes the labeller
follow a learned policy after the forced choice (base trajectory included),
so each round labels Q under the current policy instead of the fixed rollout;
`tools/train_laya_features.py` fits the next policy from the resulting cases.

Stepped mode (`--stepped --slots 1 --game-speed 64`, both tools): each
instance runs one episode with `game.tick_paused = true`, and waits advance the
clock by exactly N ticks with `game.ticks_to_run`, so RCON and inference
latency cost no game ticks. Observed on 2.1.20: identical replays went from
137 ticks of spread at 8x to 0 (a sub-60-tick sampling phase remains between
separate episodes), and an episode took 3.5 s wall instead of 6.9 s. Bursts are
still paced by `game.speed`, so 64 is the ceiling. Scale by adding one-slot
instances; pausing is per instance, so a paused instance must never share slots.

Stepped labels exposed two measurement artefacts. Branch closure required the
lab's rate window to span 600 ticks, but the window was trimmed to at most 600,
so closure depended on sample boundaries; the window now keeps at least 600
ticks once available. Per-item `sustained_ticks` reset on any empty 60-tick
sample; it now resets only when the whole window is empty. Even with exact
measurement, the scripted rollout checks every 5 s, so a 1 s offset often
catches a closure one check earlier: one-step labels then prefer `wait.1s` in
states a memoryless policy cannot tell apart, and it loops. Features set 3 adds
elapsed time and the current wait streak, and policy iteration (the learned
policy as continuation) prices such loops as timeouts.

Unattended runs: `scripts/laya_overnight.sh [HOURS]` alternates level 1 and 2
policy-iteration rounds (`scripts/laya_policy_iteration_round.sh`) on 12
one-slot stepped instances at speed 32, retrains the features policy each
round, fine-tunes Laya every second round with a gentle GPU profile, evaluates
on held-out seeds against the scripted rollout, restarts stopped workers, and
stops the fleet at the deadline. Results: `data/laya-pi/overnight/summary.log`.

### Exploration gate (disposable data collection)

`--mode laya-exploration-disposable` is a separate, explicitly documented gate
that lets an untrained checkpoint (normally `data/laya-base/typed-decisions`)
drive the same runner before any trained director exists. It is not a
capability manifest and never reads, writes or implies one.

- It requires `--exploration-seed` and `--exploration-epsilon`. Each decision
  logs the model's distribution, then acts on the model's choice or, with
  probability epsilon, a seeded uniformly random validated option (escalation
  is never sampled at random; the model may still escalate). The draw is
  derived from the decision's own state hash, so it is reproducible and
  resume-safe.
- Low confidence does not escalate; escalation stays an ordinary option.
- The journal records `authority.kind = "exploration"`,
  `checkpoint_capabilities = null`, `capability_scope.trained = false` and a
  per-decision `selection.source` (`model` or `exploration_random`). The run
  summary reports `execution_authority = "exploration"`, which the comparison
  gate rejects because it accepts only `primary`.
- Evidence defaults to `data/laya-exploration/`, apart from primary runs.
- Exploration choices are never imitation targets. Only measured outcomes or
  evaluated counterfactuals may turn them into labels.
- All hard validators, pre-execution revalidation, disposable identity checks
  and cleanup are unchanged. There is no real-save or production path.

Watching a run: join the worker (`127.0.0.1:35000+index`) from a GUI client
with identical mods, pass `--observe` so the runner spectator-focuses a
connected player on the episode surface after provisioning, and follow the
decision feed with `python tools/watch_laya_run.py` (new runs) or
`python tools/watch_laya_run.py <journal>`. Observer failures are journaled
and never change the trajectory.

`docs/rl/laya_guidelines.json` selects reviewed excerpts from active documents.
`orchestrator/laya_knowledge.py` verifies those excerpts still exist and records
current document hashes. Historical code and archived advice are excluded.

Shadow output paths are exclusive. Each batch now preserves full baseline
transitions and scenario snapshots beside decision records and records baseline
completion separately from Laya selection coverage. A shadow success cannot
qualify as Laya execution success.

Dataset conversion rejects failed actions without explicit reviewer or measured
counterfactual support, preserves causal pre-action snapshots, and reports
exclusions. Successful historical choices remain weak imitation evidence, not
proof that every alternative is worse. Training applies case, question, source
quality and temporal weights to individual losses, including micro-batch one.

`tools/compare_laya_workflow.py` consumes a frozen suite and independent primary
execution records. It rejects missing pairs, duplicate artifacts, mismatched
seeds, easier initial inventory/research/budgets and non-disposable identities.
Only observed execution from the actual deterministic workflow is accepted as
replacement comparison evidence; LinUCB is a separate curriculum baseline.
Passing the gate offers review only and never switches the incumbent.

Current verification is offline tests and real-tokenizer checks. No new model
was trained or promoted and no live Factorio episode was run for this tranche.
A compatible checkpoint, broader training data, replay fidelity and matched live
outcomes remain prerequisites for progressing toward replacement.

### Laya Operations Console

`tools/laya_console_server.py` is a loopback-only (`127.0.0.1:9138`) console for
`scripts/laya_overnight.sh`, structured like the deterministic
[operations console](../factorio_operations.md) (same `ThreadingHTTPServer`,
per-session action token on every POST, and dark control-room UI). Start it
with:

```bash
.venv/bin/python tools/laya_console_server.py --port 9138
```

then open `http://127.0.0.1:9138/`. It never launches Factorio itself; it only
starts/stops the driver script and reads its logs and evidence files.

- **Status panel**: whether a driver process is active (detected by scanning
  `/proc` for `scripts/laya_overnight.sh`, disambiguated by each process's own
  `LAYA_RUN_NAME`/`LAYA_PREFIX` environment when more than one is running at
  once), its run configuration, current round/level, phase (`evaluating
  baseline` / `labelling` / `training features` / `evaluating` / `fine-tuning
  laya` / `done`) derived from `summary.log` plus the round's cases/features/
  eval-batch files, per-process labeller progress (flagging a process stale
  past 45 minutes with no new label), per-worker status, and `nvidia-smi`/host
  memory.
- **Results panel**: every `summary.log` evaluation versus that level's rollout
  baseline, the best features policy and best Laya checkpoint per level, and an
  inline per-round median-cost trend.
- **Live log tail**: `summary.log` and whichever round/eval log file in the run
  directory changed most recently.
- **Controls** (token-protected POST, with a confirming dialog in the UI):
  **Start run** refuses if any driver is already active; by default it
  auto-resumes from the newest `features-<prefix>-r*.json` on disk (36-seed
  rounds, odd = level 2, even = level 1, seeds from 5101/6001), or accepts
  manual `LAYA_RESUME_ROUND`/`LAYA_START_POLICY`/`LAYA_L1_SEED`/`LAYA_L2_SEED`.
  **Stop after current round** drops a `stop-requested` file in the run
  directory, which the driver checks at the top of every round-loop iteration
  before its normal final evaluation and worker shutdown. **Stop now** kills
  the driver's process group, sends `/sc game.tick_paused = false game.speed =
  1` to each of its instances over RCON, and stops its workers.

`scripts/laya_overnight.sh` accepts `LAYA_RUN_NAME` (run directory under
`data/laya-pi/`, default `overnight`) and `LAYA_PREFIX` (namespaces every
round/eval/checkpoint name, default `ov`) so a console-started test run never
collides with a concurrently running one in the shared `data/laya-pi` and
`data/laya-exploration/batches` directories. Both default to today's unnamed
behaviour.
