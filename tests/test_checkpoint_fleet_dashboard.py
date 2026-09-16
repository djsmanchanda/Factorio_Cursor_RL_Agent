# Path: tests/test_checkpoint_fleet_dashboard.py
# Purpose: Protect the checkpoint fleet dashboard facade and bounded actions.

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.dashboard_runtime import CheckpointFleetStore, DashboardConfig, OperationError, OperationManager
from tools.dashboard_server import DashboardHandler


def test_fleet_default_enqueues_each_checkpoint_and_persists(tmp_path: Path) -> None:
    store = CheckpointFleetStore(tmp_path)
    state = store.mutate("run_default", {"commit": "abc1234"})

    assert [item["id"] for item in state["checkpoints"]][0] == "C0"
    assert {item["checkpoint"] for item in state["queue"]} == {
        item["id"] for item in state["checkpoints"]
    }
    assert sum(bool(item["helper"]) for item in state["queue"]) == 1
    assert state["queue"][-1]["helper"] is True
    assert store.view()["queue"][0]["commit"] == "abc1234"


def test_fleet_mutations_validate_unknown_ids_and_are_bounded(tmp_path: Path) -> None:
    store = CheckpointFleetStore(tmp_path)
    with pytest.raises(OperationError, match="Unknown checkpoint"):
        store.mutate("verify_checkpoint", {"checkpoint": "C999", "commit": "HEAD"})
    with pytest.raises(OperationError, match="at least one commit"):
        store.mutate("run_custom", {"commits": [], "checkpoints": ["C0"]})


def test_fleet_state_is_server_owned_not_shared_runtime_root(tmp_path: Path) -> None:
    config = DashboardConfig(
        server_data=tmp_path / "server-data",
        runtime_root=tmp_path / "shared-factorio-runtime",
        source_save=tmp_path / "source.zip",
        rcon_password="test",
    )
    manager = OperationManager(config)
    assert manager._fleet.path.parent == config.server_data / "checkpoint-fleet"
    assert manager._fleet.path.parent != config.runtime_root / "checkpoint-fleet"


def test_promotion_rejects_typed_save_without_catalog_candidate(tmp_path: Path) -> None:
    store = CheckpointFleetStore(tmp_path)
    with pytest.raises(OperationError, match="not listed"):
        store.mutate("promote_checkpoint", {"checkpoint": "C1", "save_id": "typed-save.zip"})


def test_fleet_get_endpoint_uses_manager_facade() -> None:
    handler = object.__new__(DashboardHandler)
    handler.path = "/api/checkpoint-fleet"
    expected = {"schema_version": "1.0.0", "queue": []}
    handler.manager = SimpleNamespace(checkpoint_fleet=lambda: expected)
    replies = []
    handler._json = lambda code, body: replies.append((code, body))

    handler.do_GET()

    assert replies == [(200, expected)]


def test_fleet_mutation_requires_action_token_and_dispatches_allowlisted_action() -> None:
    payload = json.dumps({"commit": "HEAD"}).encode()
    handler = object.__new__(DashboardHandler)
    handler.path = "/api/actions/fleet_run_default"
    handler.headers = {"X-Action-Token": "secret", "Content-Length": str(len(payload))}
    handler.action_token = "secret"
    handler.rfile = io.BytesIO(payload)
    calls = []
    handler.manager = SimpleNamespace(fleet_action=lambda action, body: calls.append((action, body)) or {"ok": True})
    replies = []
    handler._json = lambda code, body: replies.append((code, body))

    handler.do_POST()

    assert calls == [("run_default", {"commit": "HEAD"})]
    assert replies[0][0] == 202
    assert replies[0][1]["accepted"] is True


def test_dashboard_fleet_ui_covers_live_projection_and_custom_inputs() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "tools" / "dashboard.html").read_text(encoding="utf-8")
    javascript = (root / "tools" / "dashboard.js").read_text(encoding="utf-8")
    css = (root / "tools" / "dashboard.css").read_text(encoding="utf-8")

    for control in (
        "fleet-source-checkpoint", "fleet-generation", "fleet-save", "fleet-mission",
        "fleet-ranking-panel", "fleet-verify-candidate",
    ):
        assert f'id="{control}"' in html
    for projection in (
        "reached_checkpoints", "contiguous_reached_checkpoints", "slow_warning",
        "warnings", "log_tail", "ranked_commits", "fleetIsTerminalFailure",
    ):
        assert projection in javascript
    assert "payload.source_checkpoint" in javascript
    assert "payload.generation_id" in javascript
    assert "payload.save_id" in javascript
    assert "payload.mission_id" in javascript
    assert "candidate_id: document.querySelector('#fleet-verify-candidate')" in javascript
    assert "@media (max-width: 1200px)" in css
    assert css.rfind("@media (max-width: 1200px)") > css.rfind(
        ".evidence-panel { grid-column: 2;"
    )
