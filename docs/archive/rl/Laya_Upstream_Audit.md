# Path: docs/archive/rl/Laya_Upstream_Audit.md
# Purpose: Record upstream Laya changes checked against the Factorio classifier.

# Upstream Laya audit

Date: 2026-09-20

## Source checked

- Repository: `NandhaKishorM/laya`
- Current upstream: `d113dca` (`v0.3.4`, 2026-09-20)
- Local installed package: `laya 0.3.3`

The active Factorio training process was not changed. Upstream was cloned into
`/tmp` for read-only comparison.

## Recent upstream changes

1. **Route Mode is now the public Quickstart.** Upstream routes English,
   multilingual, and typed-decisions requests between three checkpoints.
2. **`Router.attach` avoids duplicate model residency.** An already-built agent
   can be registered without loading another copy of the weights.
3. **CPU-fallback diagnostics improved.** Upstream now reports the original
   device-placement error when an inference model falls back to CPU.
4. **Typed-decisions notebook fixes are already present upstream:**
   micro-batched temperature calibration, corrected calibration tensor handling,
   1024-token context, and ECE fixes. The notebook has no newer algorithmic
   change after those commits.
5. **Context documentation was clarified.** `head_max_len` is the option-prompt
   budget; the remaining `max_len` budget is state/document context.

## Applicability to Factorio

| Upstream change | Decision | Reason |
| --- | --- | --- |
| Route Mode / multilingual checkpoint | Do not adopt | Factorio has one typed decision task and our state is already a structured local contract; language routing would add latency and another checkpoint without evidence of benefit. |
| `Router.attach` | Defer | Our runtime loads one local checkpoint. Revisit only if a future Factorio process hosts multiple Laya policies. |
| CPU-fallback warning | Do not copy silently | Factorio runtime should fail closed when the requested GPU is unavailable, not quietly run a slow CPU policy. Our explicit preflight/authority gates are safer. |
| Notebook calibration fixes | Already covered | Local calibration is independent, micro-batched, and uses the larger seed-derived calibration split; evaluation computes NLL, Brier, and ECE. |
| Context-budget clarification | Adopt in docs | It confirms the current 1024/256 training profile and reinforces the separate state-truncation experiment. |

## Conclusion

No upstream code change should interrupt or be mixed into the active kernel-v2
training run. The selected local profile remains the measured winner:
micro-batch 2, accumulation 16, checkpointing off, compile + flex attention,
and fixed option width 6. Upstream changes should be reconsidered after the
held-out/test report, as separate runtime or multilingual work rather than as
another training-speed experiment.
