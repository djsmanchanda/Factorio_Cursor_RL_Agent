<!-- Path: docs/rl/laya_helper_runs/model-launch-20261026/c39-final-outcomes.md | Purpose: First final C39 paired outcome audit, read-only evidence. -->
# C39 final outcomes audit

Audited 2026-10-27 IST. Recommendation: integrated **does not satisfy default adoption**. Parent thread94dcfaf2 selects the base; this report makes no adoption or production promotion. Lifecycle: **nothing**. No source, campaign, service, world, ref, commit, push, SSH, account, or other-thread mutation. Only this report and its JSON were written.

## Cohort and provenance

Root: `data/laya-experiments/laya-c39-2123-miniship-20261026`. Frozen reference `9d9b2796a3ecb5bc1faed213ea825a6614e8eaff`; integrated `f098f9f2cda9e3803d8ed3569a37c7643d37368f`. Factorio 2.1.23 build 87750; frozen 2026-10-26 19:21:53 IST. Shared source SHA256 `347eba65065014f82261330d84d5ca2fa3e3427cad3ab18dcfdf496dd9c442b0`. Seeds 80240–80299, 60 matched pairs; caps 540,000 game ticks / 7,200 wall seconds. Whole-base comparison, not single-factor attribution.

Accounting: `{"planned": 120, "status_jobs": 120, "unique_status_ids": 120, "statuses": {"completed": 120}, "missing_planned": [], "extra_unplanned": [], "events": {"attempt_started": {"rows": 120, "unique_jobs": 120}, "attempt_terminal": {"rows": 120, "unique_jobs": 120}}, "terminal_returncodes": {"0": 120}}`. Each ledger event type has exactly one event per job; all 120 jobs completed, return code zero, no missing/unplanned jobs. Raw clean journals each contain one summary and one end acceptance; reconstructed fresh-ever and fresh-final sets match job-status in all 120 jobs (mismatches: 0). No denominator exclusions. User supplied inactive/dead, Result=success, MainPID=0 service state; no service query or lifecycle action performed.

## Strict endpoints

Counts are /60 in each arm, retaining all 120 planned jobs. P is two-sided exact paired binomial/McNemar: min(1, 2·sum[Binomial(n, 0.5) <= min(reference-only, integrated-only)]), n=discordances. These are descriptive multiple endpoints; no adjustment.

| Endpoint | Reference | Integrated | Both | Ref only | Int only | Neither | Exact paired P |
|---|---:|---:|---:|---:|---:|---:|---:|
| C6 ever | 13 | 0 | 0 | 13 | 0 | 47 | 0.000244140625 |
| C2 ever | 53 | 13 | 11 | 42 | 2 | 5 | 1.126636562e-10 |
| C3 ever | 50 | 7 | 5 | 45 | 2 | 8 | 1.604405497e-11 |
| C4 ever | 39 | 0 | 0 | 39 | 0 | 21 | 3.637978807e-12 |
| C5 ever (incompatible) | 39 | 0 | 0 | 39 | 0 | 21 | not valid |
| C6 final | 8 | 0 | 0 | 8 | 0 | 52 | 0.0078125 |
| C2 final | 53 | 13 | 11 | 42 | 2 | 5 | 1.126636562e-10 |
| C3 final | 50 | 7 | 5 | 45 | 2 | 8 | 1.604405497e-11 |
| C4 final | 39 | 0 | 0 | 39 | 0 | 21 | 3.637978807e-12 |
| C5 final (incompatible) | 7 | 0 | 0 | 7 | 0 | 53 | not valid |

Primary strict C6-ever: integrated 0/60 versus reference 13/60 (−21.67 percentage points); 13 discordances, all favor reference. Strict C6-final: 0/60 versus 8/60. This alone fails the required increase in long-window target completions; C2/C3/C4 also regress substantially. Ever means at least one fresh passed sample, not uninterrupted service. Final means the end fresh observation; it is not the latched `accepted` field and does not prove sustained service.

## Predicate compatibility

Frozen definitions are in each arm’s `code/orchestrator/checkpoint_milestones.py`, collection in `code/training/laya_ops_runner.py`, Laya receipt in `code/training/laya_phase_acceptance.py`. C2 is `c2-v1` (both metal foundations ready with delivered output and both starters absent); C3 `c3-v1` (stone foundation ready, starter absent); C4 `c4-v1` (new plastic production and delivery); strict C6 `c6-v1` (new advanced circuits plus output progression of an owned advanced-circuit consumer). Their labels and intended tests match across arms, but changed runner/observation/runtime behavior means this is a comparison of whole deployed bases, not a controlled policy-only experiment.

C5 is incompatible: frozen reference evaluates `c5-v1` (at least 120 seconds from positive plastic production/delivery, craft advances across sampled history, provider evidence); reference compact journals omit explicit predicate versions (0/60 known in batch report), though frozen source identifies v1. Integrated evaluates `c5-v2`, observed in 60/60: retained startup sampler `production-startup-v2`, first 120 seconds, 30-second rates, two adjacent positive rates within 20%, retained achievement despite later stalls. No valid primary C5 pair remains: 60/60 excluded. The raw 39/60 ever and 7/60 final versus 0/60 are descriptive different-predicate counts, not a C5 regression estimate. Integrated detail files also record the legacy c5-v1 shadow comparison in 60/60 jobs: 0 ever and 0 final passes (reference v1: 39 ever, 7 final). This supplies a same-predicate negative guard, while active C5 remains incompatible and collection differences remain. Integrated active C5 ends unknown in all 60 jobs; zero confirmed passes must not be called 60 measured C5 failures.

C1 changes from c1-v1 to c1-v2 (recent crafting window); C1 is therefore not an equivalent earlier-service endpoint. Integrated also changes termination to decision-idle-v3 versus the reference stall implementation. AST comparison confirms `_c2`, `_c3`, `_c4`, `_c6`, `_new_output`, and `_foundation_ready` bodies are identical across frozen arms; FactorySnapshot and collection differ. Equal maximum caps do not imply equal exposure: 60/60 integrated runs stop stalled; reference has 57 stalled and 3 window_timeout. Stopped jobs remain full-denominator outcomes, not censored successes. reference: observed decision-timing game ticks min/median/max 95956/391040/540673, wall elapsed seconds 845/3390/4693. integrated: observed decision-timing game ticks min/median/max 37966/44653/348365, wall elapsed seconds 335/399/3113. Timing and host scheduling are uncontrolled; copied-save policy seeds do not establish unseen-terrain generalization. Manifests say disposable_saved_world, production_authority=false, comparison_eligible=false; this is bounded experimental evidence, no production authority.

## Stops and faults

### reference

Stops: `{'stalled': 57, 'window_timeout': 3}`; termination causes: `{'stalled': 57, 'game_window': 3}`. Decisions 20995; caught-exception failures 0; maintenance failure events 5; evidence errors 0; malformed journal rows 0.

Runner log substring counts (events / affected jobs): `{"tool_error": {"events": 0, "jobs": 0}, "untyped_stuck": {"events": 37, "jobs": 21}, "infrastructure_collision": {"events": 1, "jobs": 1}, "power_route_unavailable": {"events": 35, "jobs": 21}, "safe_stop_after_repeated_failures": {"events": 0, "jobs": 0}}`. Final fresh statuses: `{"C2": {"passed": 53, "failed": 7}, "C3": {"passed": 50, "failed": 10}, "C4": {"passed": 39, "failed": 21}, "C5": {"passed": 7, "failed": 32, "unknown": 21}, "C6": {"failed": 52, "passed": 8}}`. Legacy C5 shadow: `{}`.

### integrated

Stops: `{'stalled': 60}`; termination causes: `{'stalled': 60}`. Decisions 5147; caught-exception failures 0; maintenance failure events 0; evidence errors 0; malformed journal rows 0.

Runner log substring counts (events / affected jobs): `{"tool_error": {"events": 0, "jobs": 0}, "untyped_stuck": {"events": 88, "jobs": 6}, "infrastructure_collision": {"events": 0, "jobs": 0}, "power_route_unavailable": {"events": 0, "jobs": 0}, "safe_stop_after_repeated_failures": {"events": 0, "jobs": 0}}`. Final fresh statuses: `{"C2": {"failed": 47, "passed": 13}, "C3": {"failed": 53, "passed": 7}, "C4": {"failed": 60}, "C5": {"unknown": 60}, "C6": {"failed": 60}}`. Legacy C5 shadow: `{'observed_jobs': 60, 'ever_pass': 0, 'final_pass': 0}`.

`failures=0` does not mean no tool/safety faults: typed refusals, untyped faults and maintenance failures are separate. Integrated untyped_stuck increases 37→88 events, while affected jobs decrease 21→6; infrastructure_collision decreases 1→0 and power_route_unavailable 35→0. Mixed safety/tool evidence does not rescue the failed target and earlier-service gates. Log counts are substring occurrences, not necessarily unique causally independent incidents.

Existing `takeover-20261021/stops.py` was executed read-only. Its grouping uses latched `accepted`, not fresh final: reference groups never C2=7, C2/no C3=3, C3/no C5=12, C5/no C6=25, C6=13; integrated 47,9,4,0,0. Thus those labels must not be mistaken for final strict retention; integrated C3-ever=7 includes runs outside C2. Tail-25 top blockers are descriptive, not proven causes. Reference C5/no C6 chiefly material_shortage (15 jobs); integrated C2/no C3 chiefly steel_seed_site_wait (7/9); integrated C3/no C5 steel_seed_site_wait (4/4); integrated never C2 chiefly prerequisite_advanced (26/47).

## Proxy and option evidence

Runner proxy phases are separate: reference C1/C2/C3/C4 = 60/53/53/39, integrated 22/13/4/0. Laya explicit-choice/output receipt `laya-c6-choice-output-v2` is also separate from strict deterministic C6: reference 36/60, integrated 0/60. It proves an explicit mall/produce choice followed by recipe output, not strict downstream advanced-circuit consumption. The two arms’ receipt implementation files are identical.

Executed `data/laya-orchestration/c39-20261026/chest_exposure.py` read-only: integrated passive-provider chest mall_slot/produce offered and selected in 14 jobs (9 C2, 5 never C2), no successful chest receipt; no requester option exposure. Reference C6 group offered passive-provider in 13, selected 13, outcome ok in 7; requester offered 5, selected 4. Journals distinguish offering, selection and success; these exposures do not prove the cause of outcome regression.

## Reproduction and decision

Inputs: `batch.json`, `freeze.json`, `planned-jobs.json`, `batch-events.jsonl`, `job-status.json`, `report.json` under the root above; every world path and exact run ID, paired seed cells, raw end statuses, and input SHA256 are in `c39-final-outcomes.json`. For each job read `world/evidence/clean-decisions.jsonl`: union phases with fresh==passed across acceptance events for ever; fresh==passed at at=end for final; cross-check against job-status accepted_ever/accepted_final. Read the exact world/logs/laya-runner.log for fault strings. No absent/unknown acceptance was converted to a pass.

Useful read-only commands (from repository root):

```sh
python data/laya-orchestration/takeover-20261021/stops.py data/laya-experiments/laya-c39-2123-miniship-20261026
python data/laya-orchestration/takeover-20261021/failures.py data/laya-experiments/laya-c39-2123-miniship-20261026
python data/laya-orchestration/c39-20261026/chest_exposure.py data/laya-experiments/laya-c39-2123-miniship-20261026
```

`takeover-20261021/time_to.py` was inspected, not used to determine primary binary outcomes: it reads fresh acceptance, infers missing ticks, and caps unreached timing at 150 game minutes. Early stop implementations differ, so such capped timing is not equal actual exposure.

Adoption rule (`docs/rl/laya_variants/README.md`): more long-window matched target completions, no increase in safety/tool failures, no earlier-service regression. Integrated fails the first and third gates clearly; fault dimensions are mixed, with increased untyped event volume. Recommend **do not default-adopt integrated on C39**. Parent owns base selection; no status/handoff/source changes or adoption action performed. Highest evidence: complete frozen matched 60-pair disposable-world cohort, with the compatibility limits above. Lifecycle: **nothing**.
