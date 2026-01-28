<!-- Path: policies/bot_policy.md -->
<!-- Purpose: Human-readable description of bot-related policy intent. -->

# Bot Policy (Read-Only Guidance)

This policy evaluates bot-related metrics and emits advisory signals.
It does not trigger actions or enforce upgrades.

## Signals

### bot_saturation
- Metric: bot density per tile
- Levels:
  - ok: below warning threshold
  - warn: above warning threshold
  - critical: above max threshold
- Recommended direction: reduce bot dependency when warn/critical

### construction_bot_load
- Metric: active construction bots
- Level: critical when over max threshold
- Recommended direction: reduce bot dependency when critical
