<!-- Path: docs/rl/laya_variants/README.md | Purpose: How Laya variants and experiments work, and the adoption rule. -->
# Laya variants

A variant is one small, recorded tweak to what Laya reads or how it runs,
tested against the current workflow (the baseline) on the same seeds.
Format and fields: `training/laya_variants.py`. Runner: `tools/laya_experiment.py`.

What a variant may change:

- **Docs and rules:** `catalog` (another catalog quoting the docs) or
  `catalog_patch` (per-rule overrides, usually `prefer` weights). Quotes are
  still verified against their source documents.
- **Context sent to Laya:** `settings.guideline_limit`, `per_need_checked`,
  `per_need_shown`, `stock_rate`, `rate_window_ticks`.
- **Policy:** `policy.epsilon` (exploration floor).
- **Runtime and start:** `settings.stall_decisions`, `runtime.speed`.

Windows:

- **Local:** `--until-phase C0` or `C1` with a tick cap; fast, many seeds.
- **Long:** a later phase and a larger cap; fewer seeds.

Adoption rule: a variant is folded into the default docs or settings only when
it reaches the long-window target more often than the baseline on the same
seeds, with no more failures. Record the experiment id, report and decision in
`CURRENT_STATUS.md`, then delete or archive the variant file. Generated
catalogs (`generated/`) are disposable.
