# Path: tests/test_oil_catalog_capture_pipeline.py | Purpose: Replay correlated catalog captures through an offline bound oil block.
import hashlib
import json
from types import SimpleNamespace

from core.optimizer_construction_state import ConstructionScope
from orchestrator.game_bridge import GameBridge, RECIPE_CATALOG_SUBDIR, RUNTIME_CATALOG_SUBDIR
from planners.oil_block_artifact import build_and_bind_oil_block
from tests.test_oil_block_artifact import oil_arguments, _placement_capture


def test_mock_captures_feed_scoped_bound_oil(oil_arguments, tmp_path, monkeypatch):
    identifiers = iter(("recipe-request", "prototype-request"))
    monkeypatch.setattr("orchestrator.game_bridge.uuid.uuid4", lambda: SimpleNamespace(hex=next(identifiers)))
    bridge = GameBridge.__new__(GameBridge)
    bridge.script_output = tmp_path
    bridge._poll_interval = 0
    capture = _placement_capture(oil_arguments)
    prototype_report = json.loads(capture["catalog_bytes"])
    prototype_report["request_id"] = "prototype-request"
    recipe_report = {**oil_arguments["catalog"], "version": "1.3.0", "force": "training-oil",
                     "active_mods": capture["active_mods"], "active_mods_count": 1,
                     "request_id": "recipe-request"}
    calls = []

    def command(text):
        calls.append(text.split(" ", 1)[0])
        if text.startswith("/export_recipe_catalog "):
            directory, name, report = RECIPE_CATALOG_SUBDIR, "recipe_catalog_recipe-request.json", recipe_report
        else:
            directory, name, report = RUNTIME_CATALOG_SUBDIR, "runtime_catalog_prototype-request.json", prototype_report
        target = tmp_path / directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report))
        return ""

    monkeypatch.setattr(bridge, "command", command)
    recipes = json.loads(bridge.export_recipe_catalog_capture("training-oil", timeout=1))
    prototype_bytes = bridge.export_runtime_catalog("training/oil", "training-oil", timeout=1)
    capture["catalog_bytes"] = prototype_bytes
    capture["expected_sha256"] = hashlib.sha256(prototype_bytes).hexdigest()
    result = build_and_bind_oil_block(
        **{**oil_arguments, "catalog": recipes, "item_mapping": None, "placement_capture": capture},
        scope=ConstructionScope("synthetic", "a" * 64, "training/oil", "training-oil"),
        project_id="oil", transaction_id="synthetic", throughput_output="petroleum-gas",
    )
    assert calls == ["/export_recipe_catalog", "/export_runtime_catalog"]
    assert result["bound"].artifact["declared_item_bill"]["captured-pipe"] > 0
    assert capture["expected_sha256"] in result["bound"].artifact["item_mapping"]["provenance"]
    assert result["bound"].execution_authorized is False
