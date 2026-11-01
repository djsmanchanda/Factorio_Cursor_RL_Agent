# Path: tools/optimizer_stock_refresh.py | Purpose: Journal an isolated optimizer hold refresh from paused world observations.
"""Opt-in ledger maintenance; neither authenticates the runtime nor authorizes builds.

All writers and world commands must be quiescent except this operation. The layout
stream lock coordinates cooperating submissions, but not arbitrary RCON clients or
older runners. A prepared receipt is deliberately never replayed automatically.
"""

import argparse
import json
import os
import re
import stat
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jsonschema import Draft7Validator

from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from orchestrator.material_reservations import MaterialReservationLedger
from tools.optimizer_saved_world import (_stock_observation, _validate_candidate_config, _validate_stock_observation,
                                         optimizer_observation_candidate)
from training.laya_layout_stream import _load as load_stream, _locked as lock_stream, atomic_json
from training.laya_saved_world import SavedWorldAdapter

_SCHEMA = Path(__file__).resolve().parents[1] / "schemas/optimizer_stock_refresh.schema.json"
_QUERY_SOURCE = Path(__file__).resolve().parents[1] / "training/laya_saved_world.lua"
_PAUSE_ASSERTION = 'assert(game.tick_paused and game.ticks_to_run == 0, "saved-world clock must be paused")'
_MAX_RECEIPT = 2 * 1024 * 1024
_MAX_CONFIG = 64 * 1024 * 1024


class StockRefreshError(RuntimeError):
    pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _read(path, *, limit=_MAX_RECEIPT):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise StockRefreshError(f"unsafe evidence file: {path.name}")
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            raise StockRefreshError(f"oversized evidence file: {path.name}")
        def reject_constant(_value):
            raise ValueError("non-finite number")
        value = json.loads(raw, object_pairs_hook=_unique, parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (OSError, ValueError) as error:
        raise StockRefreshError(f"unreadable evidence file: {path.name}") from error


def _check_evidence(evidence):
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    for path in evidence.glob("optimizer-stock-refresh-*.json"):
        receipt = _read(path)
        if (not re.fullmatch(r"optimizer-stock-refresh-[0-9a-f]{32}\.json", path.name)
                or not validator.is_valid(receipt) or path.stem != "optimizer-stock-refresh-" + receipt["refresh_id"]):
            raise StockRefreshError(f"invalid stock refresh receipt: {path.name}")
        if receipt["status"] != "applied":
            raise StockRefreshError(f"unresolved stock refresh receipt: {path.name}")
    for path in evidence.glob("transaction-*.json"):
        if _read(path).get("status") not in {"observed", "rejected"}:
            raise StockRefreshError(f"unresolved layout transaction: {path.name}")


def refresh_paused_optimizer_stock(adapter, bound, ledger, *, episode_id, network_anchor):
    """Persist a prepared receipt, then refresh one bound project from two paused queries.

    Caller must stop other runners and external commands for the entire operation.
    Failure after preparation leaves an unresolved receipt for manual review.
    """
    if (type(adapter) is not SavedWorldAdapter or adapter.runtime != "disposable_saved_world"
            or not isinstance(getattr(adapter, "lua_source", None), str)
            or adapter.lua_source != _QUERY_SOURCE.read_text(encoding="utf-8")
            or _PAUSE_ASSERTION not in adapter.lua_source):
        raise StockRefreshError("the unmodified paused saved-world adapter is required")
    candidate = optimizer_observation_candidate(adapter, bound, ledger, episode_id=episode_id,
                                                network_anchor=network_anchor)
    with lock_stream(adapter.root) as stream_path:
        if load_stream(stream_path)["pending"] is not None:
            raise StockRefreshError("unresolved layout stream prevents stock refresh")
        evidence = stream_path.parent
        _check_evidence(evidence)
        observations = []
        for _ in range(2):
            facts = SavedWorldAdapter.query(adapter)
            observation = _stock_observation(facts, bound, ledger, network_anchor)
            _validate_stock_observation(observation, bound, ledger)
            if observation["status"] != "available":
                raise StockRefreshError(f"stock observation unavailable: {observation['reason']}")
            observations.append(observation)
        if observations[0] != observations[1]:
            raise StockRefreshError("paused stock observations differ")
        # Recheck the binding and persisted ledger after the queries.
        if optimizer_observation_candidate(adapter, bound, ledger, episode_id=episode_id,
                                           network_anchor=network_anchor) != candidate:
            raise StockRefreshError("optimizer candidate changed during observation")
        observation = observations[0]
        stock = {item: row["available_after_claims"] for item, row in observation["items"].items()}
        refresh_id = uuid.uuid4().hex
        receipt = {"version": "optimizer-stock-refresh-v1", "refresh_id": refresh_id, "status": "prepared",
                   "candidate_id": candidate["option_id"], "binding": candidate["optimizer_binding"],
                   "artifact_sha256": candidate["optimizer_artifact_sha256"], "episode_id": episode_id,
                   "network_anchor": dict(network_anchor), "observation": observation,
                   "expected_revision": ledger.revision, "available_stock": stock}
        path = evidence / f"optimizer-stock-refresh-{refresh_id}.json"
        validator = Draft7Validator(json.loads(_SCHEMA.read_text(encoding="utf-8")))
        validator.validate(receipt)
        if path.exists() or path.is_symlink():
            raise StockRefreshError("stock refresh receipt already exists")
        atomic_json(path, receipt)
        project = ledger.refresh_isolated_bound_project(
            f"optimizer:{bound.plan.binding.project_id}", stock, binding=bound.plan.binding,
            required=dict(bound.plan.required_materials), target_item=bound.plan.throughput_output,
            expected_revision=receipt["expected_revision"])
        receipt.update(status="applied", resulting_revision=ledger.revision,
                       resulting_state=project.state, resulting_reservations=dict(project.reserved))
        validator.validate(receipt)
        atomic_json(path, receipt)
        return receipt


def refresh_from_config(adapter, config_path):
    """Rebind a pinned candidate file and refresh its persisted held project.

    This performs read-only paused runtime queries and writes the owned ledger
    and evidence. The caller is responsible for exclusive operational control.
    """
    if type(adapter) is not SavedWorldAdapter:
        raise StockRefreshError("the unmodified paused saved-world adapter is required")
    config = _read(Path(config_path), limit=_MAX_CONFIG)
    _validate_candidate_config(config)
    try:
        scope = ConstructionScope(**config["scope"])
        throughput = config["throughput"]
        bound = bind_artifact_to_construction_plan(
            config["artifact"], scope=scope, project_id=config["project_id"],
            transaction_id=config["transaction_id"], throughput_output=throughput["output"],
            throughput_target=throughput["target"], throughput_unit=throughput["unit"],
            expected_artifact_sha256=config["artifact_sha256"], expected_plan_hash=config["plan_sha256"])
        ledger = MaterialReservationLedger(adapter.root / "server/script-output", episode_id=config["episode_id"],
                                           surface=adapter.surface, force=adapter.force)
    except (KeyError, TypeError) as error:
        raise StockRefreshError("invalid pinned optimizer configuration") from error
    return refresh_paused_optimizer_stock(adapter, bound, ledger, episode_id=config["episode_id"],
                                          network_anchor=config["network_anchor"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Existing owned disposable saved-world root")
    parser.add_argument("--optimizer-candidate", required=True, type=Path, help="Pinned candidate configuration")
    args = parser.parse_args(argv)
    adapter = SavedWorldAdapter(args.root)
    try:
        receipt = refresh_from_config(adapter, args.optimizer_candidate)
    finally:
        adapter.close()
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
