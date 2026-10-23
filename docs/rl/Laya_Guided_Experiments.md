<!-- Path: docs/rl/Laya_Guided_Experiments.md | Purpose: Define opt-in guided-policy sampling experiments, binary history evidence, and recovery restrictions. -->

# Guided decision-policy experiments

`training/laya_guided_policy.py` supports three independent keyword options on
`GuidedPolicy(seed, epsilon=.15, preferences=..., ...)`. These are experimental
soft preferences over the supplied legal menu. They add no operations, change no
hard validators, and have no recipe, coordinate, recovery, or progress exceptions.
Defaults retain the original probability arithmetic, journal row shape and seeded
RNG choices for representable prior products. Nonfinite or wholly underflowed products use stable normalization instead of
producing an invalid distribution. Partial underflow retains legacy arithmetic
when its probabilities are valid.

| Option | Default | Effect |
| --- | --- | --- |
| `temperature` | `1.0` | Finite positive number. Raise the full product of matching visible-rule priors to `1 / temperature`. Values above one soften the prior; values below one strengthen it. |
| `exploration_mode` | `"options"` | `"options"` divides epsilon equally among legal options. `"families"` divides epsilon equally among the legal families present, then equally among each family's options. Wait is one family. |
| `novelty_strength` | `0.0` | Finite nonnegative number. Multiply an explicitly untried option's exploitation weight by `1 + novelty_strength`; tried and unknown options retain multiplier one. |

For option `i`, let `W_i` be the product of every matching preference of rules
actually present in `state["guidelines"]`. Binary novelty gives `N_i` as above.
The exploitation distribution is proportional to `W_i ** (1 / temperature) * N_i`.
Temperature applies to the complete prior product; novelty is a separate factor.
The final probability is `(1 - epsilon) * exploitation_i + epsilon * exploration_i`.
Epsilon must remain finite and in `(0, 1]`; matching prior factors must be finite
and nonnegative, with at least one option retaining positive prior weight. A zero
prior leaves only exploration support for that option. Sampling still uses the
policy's seeded `random.Random.choices`.
Family balance applies to the exploration component; strong priors can still give
different families different total final probabilities.

Normalization sums prior logs, subtracts their maximum before dividing by
temperature, adds log novelty, and exponentiates after another maximum shift.
This avoids overflowing the product or temperature-powered weights. At extreme
temperatures, exploitation may round to zero for weak options; epsilon preserves
support. A smallest-positive-float floor prevents that support from rounding to
zero at subnormal epsilon. All logged numbers remain finite.

## Binary novelty uses the actual ops observation

The ops runner passes `state["brief"]` and `state["observation_digest"]`, with no
structured history list. `compile_options` calls `history_features` and logs
`options[*]["features"]["tried"]`: whether the runner's observed results include
the same operation and recipe/item key. Current standard ops IDs encode that key,
for example `op.starter.iron-plate` and `op.produce.electronic-circuit`. One tried
starter does not mark another plate's starter tried. This fact is independent of
history-rule visibility and context limits, so novelty changes the ops distribution
even when no history preference rules fit in the context.

Only the exact boolean `False` earns novelty. `True` earns no bonus. Missing,
`None`, numeric, or string values are unknown and neutral. Wait options and other
options without this fact receive no bonus. Geometry options without `tried`
also stay neutral. The policy does not parse the prose brief, infer attempts from
failure streaks, infer success from `ok`, or create failure/progress facts.

This is **binary novelty over the history exposed by the runner**, not an attempt
count, recency decay, or lifetime exploration guarantee. One attempt and many
attempts have the same effect. The runner owns the history window and which keys
are distinguishable. New operation families with multiple exact options sharing
that key must expose a more precise fact before claiming exact-option novelty.
Exact counts or a bounded recent-choice window require an explicit runner
observation hook owned by the integrating parent; there are no hidden counters.
Calling `distribution` or `choose` does not update novelty.

## Evidence and integration

For opt-in modes (and numerical fallback), each distribution row records:

- `weight`: effective weight rescaled so the largest is one, identified by
  `weight_basis="max_scaled_effective"`; `prior_log_weight` retains the raw prior
  product in log space (`null` for a zero prior product).
- `factors`: all matched prior entries, temperature, exploration mode, novelty
  strength, the explicit boolean or unknown `tried`, its source, and applied
  novelty multiplier.
- `exploitation_probability`, `exploration_probability`, and the final mixture
  `probability`. The first two are normalized component distributions before
  applying epsilon.

Ordinary default rows retain their historical raw `weight`, `rules`, `features`,
and final `probability`, with no extra fields. Existing `choose` records carry the
full distribution. Unit checks in `tests/test_laya_guided_experiments.py` cover
default float bits and RNG choices, actual compiled ops facts, family mass, full
prior temperature, factor composition, extreme numbers, validation, legal menu
support, and reproducibility. These are unit results, not campaign or live evidence.

Example for a fresh disposable episode:

```python
policy = GuidedPolicy(seed, preferences=preferences, temperature=2.0)
```

Start with one changed factor per arm. A combined setting is a separate contrast.
The parent owns variant JSONs, CLI/settings integration, freezing, cohorts,
runtime actions and adoption. This source change starts no game or campaign.
For the user-authorized Starship window, cap each cohort at 60 total planned tests
across all arms, with a 12-hour deadline; keep every planned terminal status.
Use matched conditions and the existing [adoption rule](laya_variants/README.md).

**Do not use any nondefault mode in recovery/resume experiments.** Ops checkpoint
capture, runner recovery comparison and CLI reconstruction currently identify a
guided policy using only class, epsilon and preferences. They neither persist nor
compare these three new options, so a recovered policy could silently change its
distribution. Before recovery use, the parent must extend identity, serialization,
restoration and compatibility validation together, with seeded continuation tests.
The integrated CLI rejects checkpoint capture/resume when a variant requests
nondefault modes. Research-cohort capture uses default policy modes; future
automatic recovery experiments must preserve this restriction. No mod deployment or
Factorio restart is required for this Python-only change; the parent must launch
fresh runners from a verified frozen revision after integration.
