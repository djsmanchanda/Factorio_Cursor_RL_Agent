<!-- Path: docs/rl/Laya_Regression_Repair_2026-10-01.md | Purpose: Record overnight regression evidence, verified safeguards and remaining policy work. -->

# Overnight regression diagnosis and safeguards

The `overnight-20261001-health` run finished at 10:11:55 IST after rounds
36–40. No policy was promoted. Workers 01–13 stopped; demo worker 14 and
the operations console remained separate and running at the last status check.

## Measured regression

Development cohorts used seeds 4001–4032 repeatedly. These results are
diagnostics, not untouched promotion evidence.

| Policy | L1 completed | L2 completed | L3 completed |
| --- | ---: | ---: | ---: |
| Scripted | 32 | 28 | 32 |
| Final features r40 | 32 | 28 | 11 |
| Final Laya r40-final | 8 | 20 | 6 |

Features improved mean cost on L1 (6,867 → 6,083) and L2 (12,450 → 10,550).
It lost 21 scripted completions on L3. The driver admitted each global
features continuation using only that round's level, allowing this regression.

The old r26 reference, previously robust on L1, completed 0/32 with the new
health observations. A paired GPU replay used the first recorded decision on
eight L1 states, seeds 4001–4008. Only `observation_digest.factory_health`
was removed in the alternate diagnostic input; questions, options, time,
scope, objectives and other evidence stayed identical.

| Checkpoint | Original state | Same state without health |
| --- | --- | --- |
| r26-min19 | Wait on 8/8 | Build on 8/8 |
| r40-final | Wait on 8/8 | Wait on 8/8 |

This establishes r26's sensitivity to the added health facts on these starting
states. It does not establish restored episode completion. The newer model's
regression persists after removing those facts; input drift is not a complete
explanation of r40's failures.

r40's dataset has 1,719 retained training questions: 85 with core workflow
inputs and 1,634 with health. Calibration has 262: 11 core and 251 health.
The real tokenizer retained every question at 2,048 tokens with zero overflow.
Training targets include 1,001 wait labels among the 1,719 training questions;
all 48 L3 starting-state labels in the final dataset prefer a one-second wait.
The consequences of learning repeated waits from teacher continuations need
further investigation; these counts alone do not prove those labels are wrong.

The three batch results counted as errors were policy escalations at the
60-decision cap (r36 L2 seeds 4010/4018; r39 L3 seed 4013), not confirmed
token overflows or transport failures. They remain rejected by teacher admission.

Evidence: `data/laya-pi/overnight-20261001-health/`, the referenced batch
journals, and `data/laya-pi/regression-20261001/`. The latter retains paired
probabilities, the replay script and the global admission report.

## Implemented safeguards

- Global features continuation admission now evaluates every enabled level
  before changing the accepted pointer. Any completion loss, mean-cost
  regression at equal completion, missing cohort or safety/runtime failure
  prevents admission. The new gate rejects the actual r40 cohorts: L1/L2
  pass, L3 fails with 21 completion losses.
- Checkpoints record `input_profiles` from positively weighted, retained
  training questions. Every calibration profile requires training coverage.
  Serving checks the observed profile before model execution; the token
  sequence version alone cannot authorize a changed observation representation.
- Preserved checkpoints without this metadata reject health inputs by default,
  including the overnight adapters. Their weights and metadata are untouched.
  `evaluate_laya.py --allow-unrecorded-input-profile` permits explicit offline
  diagnosis and grants no disposable-shadow eligibility. New training writes
  the metadata to final and recovery checkpoints.

Verification: 52 focused tests plus the real frozen-driver harness passed;
shell syntax and scoped whitespace checks passed. No new live episode,
fine-tune, fleet deployment or long run was performed for these safeguards.

## Next bounded policy experiment

Audit the L3 starting-state wait preferences against their measured alternative
trajectories, then collect counterfactual corrections on the student's actual
wait-loop states under a verified continuation. Preserve health facts and
earlier-level coverage. Measure first-build latency, repeated waits and
per-level completions, in addition to offline accuracy. A short fine-tune must
pass fresh paired cohorts across all levels before another unattended run.

Lifecycle: start a new Python consumer/frozen driver to apply the safeguards.
No Lua deployment or Factorio restart is required. The new guard does not
repair existing model weights; reliability and real-base promotion remain open.
