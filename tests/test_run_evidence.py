# Path: tests/test_run_evidence.py
# Purpose: Verify bounded, run-scoped log and structured coordinate lookup.

import json
from tools.run_evidence import lookup


def test_lookup_scopes_log_and_finds_structured_action(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("RUN START: ts=2026-09-10T00:00:00Z\n+1s old fast belt\nRUN START: ts=2026-09-11T00:00:00Z\n+1s fast belt requested\n+2s placed\n")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"phases": [{"name": "collector", "actions": [{
        "entity": "fast-transport-belt", "position": {"x": 46.5, "y": -11.5}, "direction": "east",
    }]}]}))
    text = lookup(log, ["fast"], [plan], (46.5, -11.5))
    assert "old fast" not in text
    assert "run.log:4:" in text
    assert "plan.json#/phases/0/actions/0" in text
    assert '"direction": "east"' in text
    assert "verify their episode" in text


def test_lookup_bounded_and_preserves_first_and_last(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("RUN START: ts=2026-09-11T00:00:00Z\n" + "\n".join(f"+{i}s repeat {i}" for i in range(1000)))
    text = lookup(log, ["repeat"], [])
    assert len(text) <= 12000
    assert "+0s repeat 0" in text and "+999s repeat 999" in text
    assert "+500s repeat 500" not in text
