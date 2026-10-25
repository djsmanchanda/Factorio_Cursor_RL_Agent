<!-- Path: docs/rl/Laya_Ops_Outcome_Data.md | Purpose: Define outcome export, split isolation and the remaining policy-learning gate. -->
# Operations outcome data

`tools/export_laya_ops_data.py` exports measured logged-action outcomes from
frozen cohorts. It reads each cohort's `freeze.json`, `batch.json` and complete
`job-status.json`, then each completed world's `evidence/decision-states.jsonl`.
Every planned status remains in the manifest, including interrupted, failed,
unstarted and missing-evidence jobs. Incomplete trajectories are not relabelled
as successful examples. Invalid decision rows and duplicates are counted.

Run on Starship against a local evidence mirror:

```bash
.venv/bin/python -m tools.export_laya_ops_data /path/to/cohort \
  --world-root /path/to/world-mirror --output data/laya-ops-outcomes/export-001
```

The mirror layout is `<world-root>/<batch-name>/<job-id>/evidence/`. Without
`--world-root`, paths come from each job's recorded `world`. This command reads
local files; use SSH/Tailscale to copy selected Miniship evidence first. It never
connects to Factorio, changes campaign accounting or launches model work.

`schemas/laya_ops_outcome.schema.json` versions each row. State and legal options
are the exact pre-choice input. The logged selection, its distribution and actual
typed outcome remain separate from terminal trajectory achievements. No label is
invented for an unselected alternative. Terminal unknown final/C6 observations
stay unknown. This corpus supports outcome/value modelling; it is not a ranking
`laya_case` dataset ready for the existing lab fine-tuner. A future choice-policy
fit needs measured alternatives or a validated value estimator, a compatible
inference contract, and guided/uniform evaluation baselines. An imitation label
alone is not evidence of improvement.

Splits hash `source-save SHA256 + policy RNG seed`, keeping every arm, repeat
and decision for that key together. `--held-out-source-sha256` reserves an entire
source save for test. Different seeds on the same copied save do not establish
terrain generalization, and a different save hash alone does not prove different
terrain. The manifest preserves frozen revisions/diffs, engine/save/freeze/record
hashes, split counts, exclusions and output hashes. Existing export directories
cannot be overwritten. A usable export has a completed manifest; a command that
fails before writing it leaves an incomplete export that must not be fitted.

This change provides the evidence export boundary. No dataset fit, policy
evaluation, adoption or production promotion is claimed. The handler should
collect a frozen completed cohort first, inspect exclusions and split balance,
then schedule separate bounded training/evaluation work on Starship.
