<!-- Path: docs/archive/handoffs/36_overnight_runs_2026-09-05.md -->
<!-- Purpose: Summarize the Sep 4–5 deterministic campaign timeline and evidence across fifteen runs. -->

# Overnight runs — 2026-09-04 13:42 → 2026-09-05 15:00 IST

All runs: `research mining-productivity-4`, `surface=nauvis force=player`,
`bootstrap_profile=reduced-v1`, 60 UPS unless noted. Sources: archived
`autonomous-run-*.log` + live log + docs 34/35. Game-time (`+Ts`) is
authoritative; wall clocks jumped mid-session (NTP), so sequencing below
follows log order, not wall time.

## Summary table

| # | Run start | Length | Steel starter | Red science | Death (code) |
|---|---|---|---|---|---|
| 1 | Sep 4 13:42:41 | +3772s (~63m) | +2556s | — | `no_progress` bulk-inserter-0% |
| 2 | Sep 4 15:30:53 | +903s (~15m) | — | — | signal 15 (ops kill, not a defect) |
| 3 | Sep 4 15:46:00 | +2884s (~48m) | — | — | `no_progress` steel-plate-0% |
| 4 | Sep 4 16:50:49 | +2878s (~48m) | — | — | `no_progress` automation-science-pack |
| 5 | Sep 4 18:39:34 | +2993s (~50m) | +2303s | — | `unhandled_exception` ValueError (plan collision: substation vs pumpjack) |
| 6 | Sep 4 22:04:46 | +1724s (~29m) | — | — | signal 15 (ops kill, not a defect) |
| 7 | Sep 4 22:33:35 | +5177s (~86m, longest) | +3573s | — | `untyped_stuck` crude pipeline vs own power pole (-297.5,-57.5) |
| 8 | Sep 5 00:33:05 | +3971s (~66m) | +2349s | — | `untyped_stuck`, SAME pole tile |
| 9 | Sep 5 03:29:11 | +4338s (~72m) | +1666s | **+1820s GOAL MET** (first ever) | `unhandled_exception` BudgetExhausted (plan budget, FI/belt rotation) |
| 10 | Sep 5 06:13:22 | +977s (~16m) | — | — | `no_progress` splitter-0% (prep-latch + rotation gridlock) |
| 11 | Sep 5 06:42:09 | +137s | — | — | `no_progress` belt-32% (diagnostic restart on dead world) |
| 12 | Sep 5 14:20:12 | +412s | — | — | `no_progress` belt-32% (origin unattributed — not started by me) |
| 13 | Sep 5 14:32:23 | +389s | — | — | `no_progress` inserter-33% (diagnostic restart; belt advanced 48→64+) |
| 14 | Sep 5 14:47:07 | +404s | — | — | `no_progress` e-circuit-0% (diagnostic restart; e-circuit demanded+served) |
| 15 | Sep 5 15:00:03 | +2100s and live | pending | pending | — (healthy; yields firing repeatedly) |

## Per-run notes

### #1 — Sep 4 13:42:41, +3772s, `no_progress` bulk-inserter-0%
Pre-session baseline from the archive. Full-stack reserves era
(circuits 200 @1642s, splitter 50 @1928s). Steel +2556s, then stalled
~1200s on bulk-inserters. Typical early-September shape: slow reserves,
late steel, chemical-ladder stall.

### #2 — Sep 4 15:30:53, +903s, signal 15
Killed by operator action 15 min in. No milestones, no defect. Noted so
its archive isn't mistaken for a crash.

### #3 — Sep 4 15:46:00, +2884s, `no_progress` steel-plate-0%
Reserves done (EC @1520s, splitter @1843s) but no steel starter in 48
min — the pre-starter serial-steel stall that motivated the steel-starter
earmark work. No oil reached.

### #4 — Sep 4 16:50:49, +2878s, `no_progress` automation-science-pack
Same era: reserves done, no steel, science never started. Evidence for
the "steel before science" ordering push.

### #5 — Sep 4 18:39:34, +2993s, ValueError plan collision
Steel +2303s (progress vs #3/#4), then died on substation-vs-pumpjack
plan validation. Fixed same-session (multi-jack power filter); the fix
is what let later runs reach oil at all.

### #6 — Sep 4 22:04:46, +1724s, signal 15
Second ops kill (circuits reserve hit @1584s before the kill). Not a defect.

### #7 — Sep 4 22:33:35, +5177s, pole collision (longest run)
The run I was handed at session start. Splitter full-stack reserve held
the single rotating assembler +1538s→+1950s (~412s with its 200-belt
prerequisite ladder) before stone — pure overstock against measured
demand of 3/project. Steel +3573s, full oil district built, then the
crude pipeline died on a mid-pass power pole at (-297.5,-57.5).
Motivated three fixes: splitter trim 50→12 (`9d8b435`), corridor
reservation (`8fae36e`), pipe dives (`12816ef`).

### #8 — Sep 5 00:33:05, +3971s, SAME pole tile
First validation of the splitter trim (`splitter reached reserve (12)`
@1573s; circuits still 200). Steel +2349s (~1200s earlier than #7).
Died on the identical collision — proving it was deterministic, not luck.
Observed in doc 34 (R0–R5); pace analysis showed progress-slow, not
UPS-slow (60 UPS throughout, runner ~1.1% CPU).

### #9 — Sep 5 03:29:11, +4338s, BudgetExhausted (milestone run)
Fastest bootstrap yet: steel +1666s, reserves EC @1329s / splitter @1443s,
**first red science ever** (`GOAL MET` +1820s). Then ~2200s of
fast-inserter/belt rotation with zero net advancement: every borrow/
restore cost plan submissions against a 1-plan productive-pass refund
until `restore_bootstrap_loan_fast-inserter#retry0` tripped the budget.
Motivated the refund-to-mark fix (`d9c8b14`). Blocker target shows the
mission had already moved past red science toward chemical.

### #10 — Sep 5 06:13:22, +977s, splitter-0% (my fresh campaign)
Died 16 min in. Two stacked causes: (a) prep latched READY onto a
transient loan machine as the "standing" e-circuit cell (0 working,
`disabled_by_control_behavior`; same cell reborrowed for splitter +788s;
N=0 e-circuit assemblers post-mortem); (b) rotation gridlock — belt
waited on splitter's cell, splitter on circuits, circuits unproduced,
no free cell, no yield. Observed in doc 35 (R0–R8).

### #11 — Sep 5 06:42:09, +137s, belt-32% (diagnostic restart, PID 751218)
Restarted the runner on #10's dead world to watch the gridlock live.
Died in 137s on the same guard — confirming the freeze, not a fluke.

### #12 — Sep 5 14:20:12, +412s, belt-32% (unattributed start)
Appears in the archive but was not started by me this session (possibly
dashboard/user). Same gridlock shape, blocker-0002 (mission attempt 2).
Listed for completeness, not used as evidence for my fixes.

### #13 — Sep 5 14:32:23, +389s, inserter-33% (diagnostic restart, PID 4088143)
First run with the yield stale-credit fix live. Yield fired on pass 1
(`yielding its cell to transport-belt, which unblocks electronic-circuit`);
belt 48→64+; death moved one step downstream (belt→inserter). Blocker-0003.

### #14 — Sep 5 14:47:07, +404s, e-circuit-0% (diagnostic restart, PID 892)
With the handoff queue fallback live: e-circuit demanded (+35s) and
served (+137s, priority 100). Died one step further downstream —
e-circuit batch waited on belt's cell while belt sat `full_output` at
~64 (provider/output blockage, the current frontier). Blocker-0004.

### #15 — Sep 5 15:00:03, LIVE (fresh campaign, PID 94968)
All fixes live from a clean world. At +2100s: reserves done (EC @1338s,
splitter @1403s), yield lines firing repeatedly (`+754s, +1721s, +1770s,
+1940s, +2086s…`), no errors. Steel/science pending — the run that will
prove or break the current fix set end to end.

## Trends across the night
- **Steel keeps getting earlier**: +3573s (#7) → +2349s (#8) → +1666s (#9),
  tracking the splitter trim + parallel loans + reduced restore churn.
- **Deaths move downstream**: steel stall → plan collision → pipeline
  collision → budget → mall gridlock → provider/output. Each fix converts
  a death into progress until the next bottleneck.
- **Two deaths were ops kills** (#2, #6) — excluded from all fix reasoning.
- **Reserves converged**: circuits hold at 200 (kept), splitter at 12
  (trimmed, verified live twice).
- **Guard behavior is healthy throughout**: every `no_progress` death
  names the true stuck item; the one `BudgetExhausted` was a real
  accounting bug, now fixed. No death was a false positive.
- **Diagnostic restarts on dead worlds work**: #11 confirmed the freeze in
  137s; #13/#14 validated two fixes within minutes each. Same-mission
  blocker chains (0001→0004) track one episode across restarts.
