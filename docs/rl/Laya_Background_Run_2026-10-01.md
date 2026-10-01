<!-- Path: docs/rl/Laya_Background_Run_2026-10-01.md | Purpose: Record the authorized five-hour corrected-sampler training run and shutdown boundary. -->

# Corrected-sampler background run

Started **21:54 IST, October 1** under
`laya-corrected-20261001-5h.service`, with a five-hour hard runtime limit.
Stop begins by **02:54 IST, October 2**; shutdown/checkpoint cleanup has up to
two minutes. No restart or successor run is scheduled.

The driver is frozen at `25f1a36`. Its data is under
`data/laya-pi/background-20261001-corrected/`, prefix `rc01`.
The immutable checkout runs independently of subsequent repository edits.

## Configuration

- Workers 01–04 and 06–13: twelve disposable instances, one stepped slot each,
  speed 32, three labellers. Workers 05 and 14 remain stopped.
- Fresh collection rotates levels 2, 1 and 3, with 24 seeds per round.
  Initial banks: L1 5707, L2 6667, L3 8200. The selected L3 bank includes
  calibration seeds; the unused gap after the previous run is not overwritten.
- Only `rc01` cases with `episode-relative-v1` sampling evidence enter new
  training. No old global-clock cases or old adapters are substituted.
- Scripted continuation initially; features candidates require matched
  all-level admission and a new-seed teacher preflight before reuse.
- Collection reserves 4,500 seconds for one final two-epoch Laya fine-tune and
  evaluation. Fine-tuning timeout is 90 minutes, micro-batch 1, accumulation 16,
  checkpointing enabled, recovery snapshots every 25 optimizer steps.
- Student data uses the latest eight rounds. Train/calibration splits remain
  seed-derived. Missing valid coverage blocks training.
- Matched development evaluations use seeds **45101–45132**, fresh to the
  recorded workflow evidence at launch. Repeated admission makes these
  development seeds; they do not grant production promotion authority.

The service's control group owns its worker processes. `ExecStopPost` stops
only the twelve allocated workers on completion, failure or deadline. A sleep
inhibitor exists only while the bounded driver runs. The separate console
service, `laya-operations-console-v6.service`, remains available until stopped
or reboot: http://127.0.0.1:9138/.

## Verified launch evidence

- All twelve deployed lab copies and GUI match the committed sampler. The
  installed shared mod was preserved, including trash hash `f4c2178ded05`.
- GPU preflight passed with no competing CUDA training job.
- Fresh scripted cohorts: L1 **32/32**, L2 **26/32**, L3 **32/32**, zero runtime
  errors and safety violations. L2's six safe non-completions remain failures,
  not positive cheap-wait labels.
- The first collection audit found 295 replay journals and 6,593 decisions,
  all using `episode-relative-v1`; 266 completed and 20 timed out at that
  snapshot, with remaining episodes active. Three labellers subsequently
  recorded 96 corrected labels. This proves collection, not trained-policy
  improvement.
- Console HTTP and active-run detection passed. The five-hour runtime limit,
  two-minute stop grace, cleanup script and task sleep inhibitor were checked.

Manifests: `data/laya-background-preflight-20261001/` and the run's
`experiment.json`, `startup-verification.json`, frozen `code/`, logs, datasets
and eventual adapter/evaluation files. Run controls passed ten focused tests.

Lifecycle: fleet and GUI files have been deployed, workers reloaded at launch,
and new Python consumers are active. Reopen a GUI session that predates this
deployment before joining. The real deterministic server/save is untouched.
No policy is promoted automatically; learned results must be inspected after
completion and later pass separate held-out promotion gates.
