<!-- Path: docs/rl/Laya_C5_Startup_Contract_2026-10-25.md | Purpose: Define the user-directed plastic startup milestone and its retained telemetry. -->

# C5: plastic starts and reaches a stable production rate

User direction, 2026-10-25 IST: C5 is plastic getting started and reaching a
stable production rate in the first two game minutes. The final 30 seconds may
differ when output storage fills or petroleum is used elsewhere.

`c5-v2` measures new plastic output after the logical episode's baseline.
A game-clock sampler detects its first increase to one-game-second resolution
and retains cumulative output at startup and 30, 60, 90 and 120 game seconds.
Any adjacent pair of these four intervals proves stability if both rates are
positive and the smaller is at least 80% of the larger. An early stable pair
therefore passes even when the final interval slows or stops. No minimum
throughput target has been requested; the receipt records the actual rates.

The source is the force's cumulative **plastic item production** counter on the
explicit surface, never inventory growth. It measures aggregate plastic output,
not a fixed machine cohort or attribution to one recipe. Initial stock and historical production do not start the clock. The canonical
save has nonzero historical production. The session explicitly arms the sampler
before selecting any operations, baselining that old output. Observation and
resume only read an existing watch; a missing watch remains unknown rather
than starting a late window. The receipt records this episode-start boundary. Every watch is scoped by surface, force,
item and logical episode; a resumed episode retains its original evidence.
Sampling stops after the first 120 seconds and stores at most five samples.
It only starts for an explicitly observed episode and does not change factory
entities, recipes, inventories, stock gates or game speed.

Once proved, startup remains proved, including fresh final C5 evaluation.
Current production service, stock plateaus and supply interruptions remain
separate observations. They cannot establish stability that was never measured.
An incomplete window without a stable pair is unknown; a complete valid window
without one fails. Missing, malformed or legacy telemetry is unknown. A counter reset before
proof is unknown; an error after a valid stable pair retains the proof and is
recorded separately.
Delivery remains the separate C4 contract.

The previous `c5-v1` predicate is retained as `_c5_v1` for historical replay.
It examined every sample in the history supplied by its caller. Laya supplied
its most recent 20 acceptance observations, not the entire run. Neither that
history nor two endpoints can establish the new startup-rate contract. Keep
historical C5-v1 results labelled; do not silently rescore them as C5-v2.

The C32 arms share this contract and the structural census. They differ only
in the iron-extension correction. The census also supplies policy facts, so
it is not purely reporting and raw C2/C3 counts should not be compared directly
with pre-census cohorts. C31's output-backpressure treatment remains excluded
pending its own decision.

Validation: executable Lua sampling/resume/reset fixtures plus pure predicate,
live-adapter, journal and checkpoint regressions. Deployment is through future
frozen cohort mod copies and runners; existing freezes retain their versions.
