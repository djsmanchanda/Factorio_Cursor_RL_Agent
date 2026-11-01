# Path: tests/test_optimizer_stock_refresh.py | Purpose: Verify paused optimizer stock refresh journaling and failure boundaries.
import copy
import json

import pytest

from orchestrator.material_reservations import MaterialReservationError
from tests.test_optimizer_saved_world import _network_facts, world
from tests.test_optimizer_demand_observation import inputs
from tools import optimizer_stock_refresh as refresh
from training.laya_saved_world import SavedWorldAdapter


@pytest.fixture
def paused(world, monkeypatch):
    adapter, bound, ledger = world
    adapter.lua_source = refresh._QUERY_SOURCE.read_text()
    snapshots = [_network_facts(bound), _network_facts(bound)]
    monkeypatch.setattr(SavedWorldAdapter, "query", lambda _self: copy.deepcopy(snapshots.pop(0)))
    return adapter, bound, ledger, snapshots


def run(paused):
    adapter, bound, ledger, _snapshots = paused
    return refresh.refresh_paused_optimizer_stock(adapter, bound, ledger, episode_id="episode-1",
                                                   network_anchor={"x": 3, "y": -1})


def test_applies_ghost_adjusted_stock_and_records_result(paused):
    adapter, bound, ledger, snapshots = paused
    item = next(iter(dict(bound.plan.required_materials)))
    for facts in snapshots:
        facts["networks"]["1"]["reserved"][item] = 1
    before = ledger.revision
    receipt = run(paused)
    assert receipt["status"] == "applied" and receipt["expected_revision"] == before
    assert receipt["resulting_revision"] == before + 1
    assert receipt["resulting_state"] == "supply_wait"
    assert receipt["resulting_reservations"].get(item, 0) == dict(bound.plan.required_materials)[item] - 1
    path = adapter.root / "evidence" / f"optimizer-stock-refresh-{receipt['refresh_id']}.json"
    assert json.loads(path.read_text()) == receipt


def test_unchanged_hold_receipt_keeps_revision(paused):
    _adapter, _bound, ledger, _ = paused
    before = ledger.path.read_bytes(), ledger.revision
    receipt = run(paused)
    assert receipt["status"] == "applied" and receipt["resulting_revision"] == before[1]
    assert (ledger.path.read_bytes(), ledger.revision) == before


@pytest.mark.parametrize("mutation", ["tick", "stock", "missing", "duplicate"])
def test_rejects_changed_or_ambiguous_observations_before_journaling(paused, mutation):
    adapter, bound, ledger, snapshots = paused
    if mutation == "tick": snapshots[1]["tick"] += 1
    elif mutation == "stock": snapshots[1]["networks"]["1"]["items"][next(iter(dict(bound.plan.required_materials)))] += 1
    elif mutation == "missing": snapshots[0]["ports"] = []
    else: snapshots[0]["ports"].append(copy.deepcopy(snapshots[0]["ports"][0]))
    before = ledger.path.read_bytes()
    with pytest.raises(refresh.StockRefreshError): run(paused)
    assert ledger.path.read_bytes() == before
    assert not list((adapter.root / "evidence").glob("optimizer-stock-refresh-*.json"))


def test_unmodified_paused_adapter_required(paused):
    adapter, _bound, ledger, _ = paused
    before = ledger.path.read_bytes()
    adapter.lua_source = adapter.lua_source.replace(refresh._PAUSE_ASSERTION, "")
    with pytest.raises(refresh.StockRefreshError, match="unmodified paused"): run(paused)
    assert ledger.path.read_bytes() == before


def test_prepared_receipt_after_ledger_write_blocks_retry(paused, monkeypatch):
    adapter, _bound, ledger, snapshots = paused
    item = next(iter(snapshots[0]["networks"]["1"]["items"]))
    for facts in snapshots: facts["networks"]["1"]["items"][item] = 0
    original = refresh.atomic_json
    def interrupted(path, value):
        if value["status"] == "applied": raise OSError("simulated crash")
        original(path, value)
    monkeypatch.setattr(refresh, "atomic_json", interrupted)
    before = ledger.revision
    with pytest.raises(OSError, match="simulated crash"): run(paused)
    assert ledger.revision == before + 1
    path, = (adapter.root / "evidence").glob("optimizer-stock-refresh-*.json")
    assert json.loads(path.read_text())["status"] == "prepared"
    with pytest.raises(refresh.StockRefreshError, match="unresolved stock refresh"): run(paused)


@pytest.mark.parametrize("kind", ["transaction", "symlink", "malformed"])
def test_existing_unsafe_evidence_blocks_refresh(paused, kind, tmp_path):
    adapter, _bound, ledger, _ = paused
    evidence = adapter.root / "evidence"
    evidence.mkdir()
    if kind == "transaction": (evidence / "transaction-pending.json").write_text('{"status":"submitted"}')
    elif kind == "symlink": (evidence / ("optimizer-stock-refresh-" + "a" * 32 + ".json")).symlink_to(tmp_path / "missing")
    else: (evidence / ("optimizer-stock-refresh-" + "a" * 32 + ".json")).write_text('{}')
    before = ledger.path.read_bytes()
    with pytest.raises(refresh.StockRefreshError): run(paused)
    assert ledger.path.read_bytes() == before


def test_other_active_project_fails_closed_with_prepared_receipt(paused):
    adapter, bound, ledger, _ = paused
    ledger.declare("other", {"extra": 1}, {**dict(bound.plan.required_materials), "extra": 1})
    before = ledger.path.read_bytes()
    with pytest.raises(MaterialReservationError, match="other active or constructing"): run(paused)
    assert ledger.path.read_bytes() == before
    path, = (adapter.root / "evidence").glob("optimizer-stock-refresh-*.json")
    assert json.loads(path.read_text())["status"] == "prepared"


def test_stale_ledger_fails_before_journal(paused):
    from orchestrator.material_reservations import MaterialReservationError, MaterialReservationLedger
    adapter, bound, ledger, _ = paused
    writer = MaterialReservationLedger(adapter.root / "server/script-output", episode_id="episode-1",
                                       surface="nauvis", force="player")
    writer.declare("other", {"extra": 1}, {**dict(bound.plan.required_materials), "extra": 1})
    before = writer.path.read_bytes()
    with pytest.raises(MaterialReservationError, match="changed"): run(paused)
    assert writer.path.read_bytes() == before
    assert not (adapter.root / "evidence").exists()


def test_pending_layout_stream_blocks_refresh(paused):
    adapter, _bound, ledger, _ = paused
    evidence = adapter.root / "evidence"
    evidence.mkdir()
    stream = {"version": 1, "stream_id": "a" * 16, "last_sequence": 0,
              "pending": {"sequence": 1, "request_id": "a" * 16 + "0000000000000001",
                          "authorization": {}, "build_plan": {}, "transaction": "transaction-" + "b" * 32 + ".json"}}
    (evidence / "layout-stream.json").write_text(json.dumps(stream))
    before = ledger.path.read_bytes()
    with pytest.raises(refresh.StockRefreshError, match="unresolved layout stream"): run(paused)
    assert ledger.path.read_bytes() == before


def test_symlinked_stream_lock_is_rejected(paused, tmp_path):
    from training.laya_layout_stream import LayoutStreamError
    adapter, _bound, ledger, _ = paused
    evidence = adapter.root / "evidence"
    evidence.mkdir()
    (evidence / ".layout-stream.lock").symlink_to(tmp_path / "target")
    before = ledger.path.read_bytes()
    with pytest.raises(LayoutStreamError, match="lock is unavailable"): run(paused)
    assert ledger.path.read_bytes() == before


def test_subclass_cannot_use_paused_refresh(paused):
    class OtherAdapter(SavedWorldAdapter):
        pass
    adapter, bound, ledger, _ = paused
    other = object.__new__(OtherAdapter)
    other.__dict__.update(adapter.__dict__)
    with pytest.raises(refresh.StockRefreshError, match="unmodified paused"):
        refresh.refresh_paused_optimizer_stock(other, bound, ledger, episode_id="episode-1",
                                              network_anchor={"x": 3, "y": -1})


def test_pinned_config_refresh_rebinds_and_updates_persisted_ledger(paused, tmp_path):
    from tools.optimizer_saved_world import optimizer_candidate_config
    adapter, bound, ledger, snapshots = paused
    config = optimizer_candidate_config(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(config))
    item = next(iter(dict(bound.plan.required_materials)))
    for facts in snapshots: facts["networks"]["1"]["items"][item] = 0
    receipt = refresh.refresh_from_config(adapter, path)
    reloaded = type(ledger)(adapter.root / "server/script-output", episode_id="episode-1",
                            surface="nauvis", force="player")
    assert receipt["status"] == "applied" and receipt["resulting_revision"] == reloaded.revision
    assert reloaded.projects["optimizer:iron-block"].state == "supply_wait"
    assert item not in reloaded.projects["optimizer:iron-block"].reserved
    assert receipt["binding"] == bound.plan.binding.to_dict()


@pytest.mark.parametrize("mutation", ["artifact_hash", "scope", "episode", "duplicate", "symlink"])
def test_pinned_config_refresh_rejects_tampering_before_query(paused, tmp_path, monkeypatch, mutation):
    from tools.optimizer_saved_world import optimizer_candidate_config
    adapter, bound, ledger, _ = paused
    config = optimizer_candidate_config(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1})
    if mutation == "artifact_hash": config["artifact_sha256"] = "0" * 64
    elif mutation == "scope": config["scope"]["manifest_digest"] = "0" * 64
    elif mutation == "episode": config["episode_id"] = "episode-2"
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(config))
    if mutation == "duplicate": path.write_text('{"version":"a", "version":"b"}')
    if mutation == "symlink":
        link = tmp_path / "link.json"
        link.symlink_to(path)
        path = link
    monkeypatch.setattr(SavedWorldAdapter, "query", lambda *_args: pytest.fail("invalid config reached query"))
    before = ledger.path.read_bytes()
    with pytest.raises((ValueError, refresh.StockRefreshError, MaterialReservationError)):
        refresh.refresh_from_config(adapter, path)
    assert ledger.path.read_bytes() == before
    assert not (adapter.root / "evidence").exists()


def test_command_dispatches_config_and_closes_adapter(tmp_path, monkeypatch, capsys):
    root, config = tmp_path / "world", tmp_path / "candidate.json"
    events = []
    class Adapter:
        def __init__(self, path): events.append(("open", path))
        def close(self): events.append(("close",))
    monkeypatch.setattr(refresh, "SavedWorldAdapter", Adapter)
    def refresh_config(adapter, path):
        events.append(("refresh", path))
        return {"status": "applied", "resulting_revision": 4}
    monkeypatch.setattr(refresh, "refresh_from_config", refresh_config)
    assert refresh.main(["--root", str(root), "--optimizer-candidate", str(config)]) == 0
    assert json.loads(capsys.readouterr().out)["resulting_revision"] == 4
    assert events == [("open", root), ("refresh", config), ("close",)]


def test_command_closes_adapter_if_refresh_fails(tmp_path, monkeypatch):
    events = []
    class Adapter:
        def __init__(self, _path): pass
        def close(self): events.append("closed")
    monkeypatch.setattr(refresh, "SavedWorldAdapter", Adapter)
    def fail(*_args): raise refresh.StockRefreshError("not paused")
    monkeypatch.setattr(refresh, "refresh_from_config", fail)
    with pytest.raises(refresh.StockRefreshError, match="not paused"):
        refresh.main(["--root", str(tmp_path), "--optimizer-candidate", str(tmp_path / "config")])
    assert events == ["closed"]


def test_candidate_config_with_fluid_catalog_also_supports_stock_refresh(paused, tmp_path):
    from tests.test_fluid_topology import catalog_bytes
    from tools.optimizer_saved_world import optimizer_candidate_config
    adapter, bound, _ledger, _ = paused
    config = optimizer_candidate_config(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1},
                                        fluid_catalog_bytes=catalog_bytes() + b" " * (2 * 1024 * 1024))
    path = tmp_path / "candidate-with-catalog.json"
    path.write_text(json.dumps(config))
    assert refresh.refresh_from_config(adapter, path)["status"] == "applied"


def test_candidate_with_demand_model_can_refresh_stock(paused, inputs, tmp_path):
    from tools.optimizer_saved_world import optimizer_candidate_config
    adapter, bound, _ledger, _ = paused
    _other_bound, model, _facts = inputs
    config = optimizer_candidate_config(bound, episode_id="episode-1", network_anchor={"x": 3, "y": -1},
                                        demand_model=model)
    path = tmp_path / "demand-candidate.json"
    path.write_text(json.dumps(config))
    assert refresh.refresh_from_config(adapter, path)["status"] == "applied"
