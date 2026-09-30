<!-- Path: docs/rl/Laya_Level3_Live_Verification_2026-09-30.md | Purpose: Record deployed L3 mechanics, paired capacity measurements, remaining limitations and next-run configuration. -->

# Level 3 live verification — 2026-09-30 → 2026-10-01

## Outcome

The revised connected-workflow lab is deployed to disposable workers 01–14
and the Linux GUI mod directory. Each worker reloaded Factorio 2.1.20 and
returned fresh `joint-sustain-v2` evidence. The scripted rollout completed
32/32 validation seeds (7001–7032), with at most 34 decisions per episode.
These are mechanics and scripted-baseline results; no new Laya checkpoint was
trained or promoted.

The first worker-05 smoke test exposed an unpowered circuit cable inserter.
Its physical position was `(-7.5, 12.5)` on seed 7001, with electrical network
ID zero; other consumers had a network. The circuit cell's western pole now
covers that inserter. A fresh electrical inspection found no unpowered
consumers, followed by 4/4 smoke completions and the full 32/32 cohort.
The new regression checks every consumer's pole coverage across the translated
L3 layouts. No existing factory was manually repaired.

## Paired measured capacity

Worker 05, seed 7001, exact paused-clock stepping at speed 32. Each case built
the same legal cells with fixed tier choices, warmed up for 6,000 ticks, then
measured both sinks for 90,000 ticks (25 minutes). Objective durations were
extended so the lab continued sampling throughout the experiment. Expansion
was the only build-presence difference within each pair.

| Machine / inserter | One furnace: belts/s | Two furnaces: belts/s | One furnace: circuits/s | Two furnaces: circuits/s |
|---|---:|---:|---:|---:|
| AM2 / fast | 0.2080 | 0.4160 | 0.3127 | 0.4160 |
| AM1 / fast | 0.2080 | 0.4173 | 0.3127 | 0.4167 |
| AM2 / basic | 0.2080 | 0.4160 | 0.3127 | 0.4173 |
| AM1 / basic | 0.2080 | 0.4160 | 0.3120 | 0.4167 |

All eight cases retained power, recorded zero entity/bounds/budget violations
and recycled successfully. Both product chains benefit from the shared plate
supply. In this fixture, assembler/inserter tiers did not materially change
steady average capacity; their costs and startup behavior remain decisions
for outcome learning.

Limits: this long measurement uses one seed and supplied ore/power. Its
averages do not guarantee every short window meets the target: some expanded
cases have 600-tick belt windows at 0.2/s despite an average near 0.416/s.
Production and inserter delivery are bursty. Buffer saturation, resource
depletion, finite construction costs and real-base resilience are not proved
by these experiments. The 32-seed cohort validates the present finite
completion objective, not unattended long-term service.

## Deployment and evidence

The lab Lua came from commit `9fd61c5`. Both project mods were synchronized
using the native worker manager. The already deployed `trash_requesters.lua`
was preserved in the deployment snapshot: its SHA-256 prefix is
`f4c2178ded05`, identical across the workers and GUI before deployment. This
avoided reverting an existing shared-mod fix or deploying unrelated dirty
worktree changes. The Python compiler includes the subsequently verified
circuit power correction.

Evidence under `data/laya-level3-validation/`:

- `power-diagnosis.json` and `power-check.json`: scoped electrical facts;
- `batches/l3-v2-powered-20260930.jsonl`: four initial successful seeds;
- `batches/l3-v2-cohort-20260930.jsonl`: all 32 validation results, with journals;
- `capacity-sweep.jsonl`: paired plans, execution receipts, scope, compiler
  hashes, samples, final reports and cleanup;
- `fleet-verification.json`: worker endpoints, versions and fresh-report checks;
- `context-fit.json`: all 26 decisions on the smoke trajectory fit the real
  tokenizer's 2,048-token limit, maximum 1,286, with no overflow;
- the `laya_*probe*.py`, `laya_capacity_sweep.py` and `laya_fleet_verify.py`
  programs preserve the experiment procedures alongside their artifacts.

The capacity probes are controlled interventions, not policy training labels
or primary-controller promotion evidence. Their raw outcomes and original
failed smoke episode remain preserved.

## Operating state and training adoption

The overnight driver had finished its work but left workers running. After
deployment and checks, workers 01–13 are returned to stopped status. Demo
worker 14 is running with updated mods. No new GPU job, unattended run or
monitoring schedule was started.

The Laya Operations Console is reloaded on `http://127.0.0.1:9138/`, managed
by user service `laya-operations-console-v4.service`. Its `l3-verification`
history entry displays the measured 32-seed scripted baseline, and future
training reports include level 3 and the revised reliability metrics. Refresh an already open console
page after the restart. The GUI mod files match the workers; restart/reopen
the Factorio GUI before connecting if its old session is still loaded.

The next driver launch rotates levels 2, 1, 3. `LAYA_LEVELS="2 1"` selects
the earlier rotation. Level 3 bootstraps labels with the scripted rollout
until its first accepted features round. Resume follows explicit consumed
seed blocks, including partial rounds, and retains the accepted continuation
rather than automatically choosing a later rejected model.

Current historical resume: round 33, next L1 seed 5677, L2 seed 6613, new L3
seed 8101. Separate unseen final-promotion scenarios and a matched deterministic
opening are still required before any real-base authority.
