<!-- Path: docs/rl/laya_variants/README.md | Purpose: How Laya variants and experiments work, and the adoption rule. -->
# Laya variants

A variant is one small, recorded tweak to what Laya reads or how it runs,
tested against the current workflow (the baseline) on the same seeds.
Format and fields: `training/laya_variants.py`. Runner: `tools/laya_experiment.py`.

Declare its action path (`geometry` or `deterministic`) and predicate/window.
Matching policy RNG seeds on one copied save is not unseen-terrain evaluation.
What a variant may change:

- **Docs and rules:** `catalog` (another catalog quoting the docs) or
  `catalog_patch` (per-rule overrides, usually `prefer` weights). Quotes are
  still verified against their source documents.
- **Context sent to Laya:** `settings.guideline_limit`, `per_need_checked`,
  `per_need_shown`, `stock_rate`, `rate_window_ticks`.
- **Policy:** `policy.epsilon` (exploration floor).
- **Runtime:** `runtime.speed`; geometry uses `settings.stall_decisions`.
  Ops has a separate 18,000-tick stall constant; that setting does not tune it.

Windows:

- **Local:** `--until-phase C0` or `C1` with a tick cap; fast, many seeds.
- **Long:** a later phase and a larger cap; fewer seeds.
- **Shared acceptance:** `--actions deterministic --until-accepted C2` with
  a larger tick cap continues through proxy C2 until the unchanged shared
  predicate passes or another bounded stop occurs. Keep these results
  separate from earlier proxy-terminated windows.

The harness supports up to 40 isolated worlds with explicit disjoint game/RCON
port ranges. Capacity is not proof of sustained throughput: check runtime
health before increasing a live fleet. Generated catalogs are atomically
replaced so simultaneous episode startup cannot read a partial file.

Before comparing, inspect effective option weights: every matching preference
multiplies, including overlapping entries in one rule. Change only the intended
factor, or declare all affected groups. Current other-slots-1 arms also reduce
gear/cable/circuit factors from 180 to 30; they do not isolate other slots.

Default adoption requires more long-window target completions on matched
conditions, no increase in safety/tool failures, and no earlier-service
regression. Preserve every planned job's terminal status; omitted crashes or
interrupted jobs cannot silently improve a denominator. The current harness
does not yet provide complete-cohort accounting, and its failure counter omits
typed blocks. Check raw outcomes alongside the report. Changing rate windows
changes acceptance conditions and is not a like-for-like promotion comparison.

An explicitly recorded early-milestone default is **provisional**, not evidence
that the long-window gate passed. Record the limited objective, full cohort,
known missing gates and required recheck. The October 7 mf30 adoption is such
an exception: Laya C1 improved, C2 remained zero. This cleanup preserves its
weights; it grants no additional variant adoption or production promotion.

Record experiment, report, decision and predicate version in `CURRENT_STATUS.md`,
then archive the variant. Generated catalogs (`generated/`) are disposable and
must be regenerated from synchronized sources for new runs.
