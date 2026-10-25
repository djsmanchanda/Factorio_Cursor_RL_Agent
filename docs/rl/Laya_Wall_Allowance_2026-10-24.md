<!-- Path: docs/rl/Laya_Wall_Allowance_2026-10-24.md | Purpose: Record the user-selected7200-second allowance, safe pre-admission amendments and live-budget exceptions. -->

# Laya trial wall allowance

On2026-10-24 the user selected **7,200 seconds for ordinary trials on both hosts**,
including current cohorts only when a safe transition exists. This replaces the
ordinary6,000-second preference for new comparisons. Explicit longer research
windows remain15,000 seconds. Game ticks, stall guards, predicates, source saves,
seed order, host limits and absolute campaign deadlines are separate settings.

New cohort generators write `trial_wall_seconds: 7200`. The shared constant is
`tools/laya_batch_defaults.py`; new batch supervisors also use it when the field
is omitted. Frontier sampling/capital/bottleneck generation uses that constant.
The local mall generator `/tmp/freeze_mall.py` now writes7200 explicitly instead
of inheriting C24's6000. Existing explicit budgets, historical reports and exact
recovery bundles retain their recorded values; a code change is not a live
budget extension. New frozen Python supervisors are needed to use the default.

## Safe current amendments

C26 and C27 each had60 queued jobs and zero admissions, reservations or worlds.
Their frozen admission controls were drained and acknowledged; only those two
waiting user units were stopped. Original batch, freeze, plan, wrapper, status
and unit-limit evidence is archived under the main repository's
`data/laya-orchestration/wall-allowance-7200-20261024/`.

While stopped, only `trial_wall_seconds` changed6000->7200 in each batch.
Freeze objective text and batch hashes were amended, retaining the original
freeze and original batch hashes in `amendments/wall7200-v2.json`. Harness
hashes, wrapper, source save and planned-job hashes were verified unchanged.
No arm code, mod, seed, predicate, tick window, port or dependency changed.
Both arms receive the same7200-second allowance before first admission.

The exact same frozen wrappers were relaunched with `--resume`; original
absolute user-unit limits were preserved by using remaining runtime rather
than restarting the100-hour clock. Admission was released only behind the
unchanged C25 dependency. Readback verified both units active,60 queued each,
zero reserved/admitted, manifest7200 and matching freeze hashes. No worlds
were reset. Cohort deadline remains2026-10-27 18:00 IST; latest admission is now
16:00 IST for a complete7200-second window. The previous6000-second latest
admission was16:20. This is a recorded pre-admission contract amendment.

## Current attempts retained safely

C25 already has completed and running attempts under6000 seconds. Its supervisor
caches the manifest and per-attempt end times. Changing only queued jobs would
mix conditions within the matched cohort; restart/recovery would interrupt or
recreate live worlds. No safe hot extension is supported, so the whole current
C25 comparison retains6000 seconds.

Starship freshness likewise has cached deadlines and no supported hot extension.
Its current6000-second comparison finishes intact; prior completed/cancelled
screens are historical evidence. The longer research pilot retains15000.
Starship's03:46:47 cohort end/03:51:47 hard end, current host target and all cuts
remain unchanged. Nothing here authorizes a new confirmation or extra cohort.

## Verification and lifecycle

The frontier source passed37 focused tests and the fast gate1358 passed/3 skipped.
The integrated Miniship source passed36 focused tests and the fast gate1326
passed/3 skipped. Coverage includes actual admission refusal without a complete
7200-second allowance, explicit legacy/longer budget preservation, generated
manifest budgets and coherent recovery. Logs and exit files are
`/tmp/laya-allowance7200-{focused,fast}-20261024.{log,exit}` and
`/tmp/laya-allowance7200-main-{focused,fast}-20261024.{log,exit}`.

Live evidence is the C26/C27 zero-admission supervisor transition and readback;
it does not establish runtime performance. Source/mod/save/plan hashes were
unchanged in those freezes. No Factorio restart or mod deployment is required.
Future runs must freeze the updated allowance contract. Existing live exceptions
need no lifecycle action. No controller-thread notification was sent.
