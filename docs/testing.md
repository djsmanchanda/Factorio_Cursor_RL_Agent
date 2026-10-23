<!-- Path: docs/testing.md | Purpose: Define truthful, tiered repository verification and its environment requirements. -->

# Test suites

Tests have one primary domain marker and may also carry cost or environment
markers. Marker assignment is centralized in `tests/conftest.py`; adding a new
test module requires placing it in the right domain or accepting the pure
contract fallback deliberately.

Default discovery is limited to `tests/`. Frozen campaign copies under `data/`
retain their historical tests as evidence; collecting them together imports
incompatible revisions and duplicates module names. To verify a frozen arm,
run pytest from that arm's code directory.

Primary domains:

- `contracts`: schemas, catalogs, pure planners, validators, and invariants;
- `deterministic`: mocked/replayed controller, lifecycle, and recovery behavior;
- `training`: disposable RL scenarios, rewards, policies, and evaluation;
- `ops`: dashboards, runners, shell managers, logging, and retention.

Orthogonal markers are `fast`, `slow`, `exhaustive`, `lua`, `loopback`,
`integration`, `source_tripwire`, and `live_regression`. Source tripwires prove
only that an implementation shape remains present. They are never evidence that
a lifecycle completed or that deployed/live behavior succeeded.

Default discovery is restricted to `tests/`. Frozen experiments and nested
worktrees under `data/` are evidence copies and must not be collected as the
current suite. Explicit paths still work for deliberate replay verification.

Use compact commands:

```bash
# Per-change safety gate used by pull requests and pushes.
python -m pytest -m fast

# One domain without its expensive or external-boundary cases.
python -m pytest -m "contracts and not slow and not integration"
python -m pytest -m "deterministic and not slow and not integration"
python -m pytest -m "training and not slow and not integration"
python -m pytest -m "ops and not slow and not integration"

# Environment-specific suites.
python -m pytest -m lua
python -m pytest -m loopback

# Truthful complete run, including breadth and integration.
python -m pytest
```

The fast gate is intentionally curated rather than defined as “everything not
currently slow.” This prevents a new expensive test from silently bloating every
change. The fast scaffolding contract uses a small real managed backbone; the
complete electronics bundle is still checked by the slow composition cases
using the same payload assertions. Scheduled and manually dispatched CI install
`lupa` and Lua 5.4 and run the complete suite. Loopback tests need permission to
bind `127.0.0.1`; Lua tests need `lua`/`luac` or their explicit `lupa` fallback.

All filesystem-writing tests use `tmp_path` or `tmp_path_factory`. Test files
rely on the repository `pythonpath` configured by `pytest.ini`; they must not
mutate `sys.path`. Shared fake clients and scenario builders belong under
`tests/support/` as they are introduced or consolidated.

## Tool-call efficiency

Run the narrow regression while iterating, then the fast gate once per verified
change. Run the complete suite when breadth is required; reuse its recorded
result for the same unchanged code and environment. Add `--durations=15` to a
slow run before changing test coverage or timeout behavior. Preserve pytest's
exit status when redirecting output: run it as its own command, or capture its
status before printing the log tail. A successful `tail` does not prove tests passed.

For a background suite, use the harness's completion notification or its returned
process/session identifier. Avoid `pgrep -f` against a command pattern embedded
in the polling shell itself: the poller can match itself and wait indefinitely.
Long campaign supervision belongs in T3's scheduler, with a bounded status read
on each wake, rather than a shell held open with `sleep` for hours. This frees the
agent between checks; it does not shorten the campaign itself.

The primary-domain router honors an explicitly declared primary marker and
rejects multiple distinct primary domains. Inferred markers are a fallback.
Research acceptance and coherent ops recovery have curated fast coverage;
real engine save/reload and funded lab/delivery wiring remain separate,
explicitly disposable integration evidence. A pre-supplied fixture proves
mechanics, not factory-earned research or frontier eligibility.
