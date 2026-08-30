<!-- Path: docs/testing.md | Purpose: Define truthful, tiered repository verification and its environment requirements. -->

# Test suites

Tests have one primary domain marker and may also carry cost or environment
markers. Marker assignment is centralized in `tests/conftest.py`; adding a new
test module requires placing it in the right domain or accepting the pure
contract fallback deliberately.

Primary domains:

- `contracts`: schemas, catalogs, pure planners, validators, and invariants;
- `deterministic`: mocked/replayed controller, lifecycle, and recovery behavior;
- `training`: disposable RL scenarios, rewards, policies, and evaluation;
- `ops`: dashboards, runners, shell managers, logging, and retention.

Orthogonal markers are `fast`, `slow`, `exhaustive`, `lua`, `loopback`,
`integration`, `source_tripwire`, and `live_regression`. Source tripwires prove
only that an implementation shape remains present. They are never evidence that
a lifecycle completed or that deployed/live behavior succeeded.

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
change. Scheduled and manually dispatched CI install `lupa` and Lua 5.4 and run
the complete suite. Loopback tests need permission to bind `127.0.0.1`; Lua
tests need `lua`/`luac` or their explicit `lupa` fallback.

All filesystem-writing tests use `tmp_path` or `tmp_path_factory`. Test files
rely on the repository `pythonpath` configured by `pytest.ini`; they must not
mutate `sys.path`. Shared fake clients and scenario builders belong under
`tests/support/` as they are introduced or consolidated.
