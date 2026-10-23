# Path: training/laya_ops_checkpoint.py | Purpose: Capture and restore coherent disposable ops generations with cumulative budgets and explicit state ownership.
from __future__ import annotations
import dataclasses
import importlib
import json
import math
import os
import shutil
import subprocess
import time
import uuid
from jsonschema import Draft7Validator
from pathlib import Path
from training.laya_saved_world import ROOT, file_hash, owned_root, verify_instance

VERSION = "laya-ops-checkpoint-v1"
VALIDATOR = Draft7Validator(json.loads((ROOT / "schemas/laya_ops_checkpoint.schema.json").read_text()))
# Persistent decisions/configuration must survive; read-only resource surveys can be re-observed.
RUNTIME = {
    "orchestrator.autonomous_builder": (
        "_STAGE_DELIVERY_PROVIDERS", "_STAGE_DELIVERY_ATTEMPTS", "_REFINERY_SITE_RESERVATIONS", "_TRANSFERABLE_WAITS",
        "_BLOCKING_MALL_ITEMS", "_DRAIN_WATCH_LAST_TRANSFERABLE", "_DRAIN_WATCH_PREVIOUS_TRANSFERABLE",
        "UNBACKED_DRAWS", "MANAGED_INTERMEDIATE_SOURCES", "_LOAN_GATE_REFRESH_ATTEMPTS", "_BOOTSTRAP_SHARED_PROVIDER_ITEMS",
        "_MALL_REFRESH_SIGNATURES", "_MALL_PROVIDER_CAPACITY_FLOORS", "_MALL_STOCK_GATE_FLOORS", "_RAISED_STOCK_GATE_FLOORS",
        "_MALL_ITEM_PROVIDER_LIMITS", "_CRAFT_PROOF_CONSUMED", "_BOOTSTRAP_LOAN_PROGRESS_REVISION",
        "_BOOTSTRAP_LOAN_CONFIGURATION_REVISION", "_STARTUP_METAL_STARTERS_OBSERVED", "_STARTUP_MALL_LIMITS_RELEASED",
        "_STARTUP_MALL_LIMITS_FALLBACK_PROBED", "_POST_STARTER_TRANSITION_ANNOUNCED", "_LADDER_CREDIT", "_LADDER_CREDIT_ERRORS"),
    "orchestrator.construction_coverage": ("_PENDING_POWER_BRIDGES", "_COVERAGE_PROGRESS"),
    "orchestrator.stage_chemical": ("_CRUDE_EXPANSION_FAILED_CELLS",),
}
CLASS_MODULES = ("orchestrator.checkpoint_milestones", "orchestrator.construction_coverage")


def encode(value):
    if dataclasses.is_dataclass(value):
        name = type(value).__module__ + "." + type(value).__name__
        if type(value).__module__ not in CLASS_MODULES:
            raise ValueError(f"unregistered checkpoint state type: {name}")
        return {"type": "dataclass", "class": name,
                "value": encode({f.name: getattr(value, f.name) for f in dataclasses.fields(value)})}
    if isinstance(value, dict):
        return {"type": "map", "value": [[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, (tuple, list, set, frozenset)):
        return {"type": type(value).__name__, "value": [encode(v) for v in value]}
    if isinstance(value, float) and not math.isfinite(value):
        return {"type": "float", "value": str(value)}
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError(f"unsupported checkpoint state: {type(value).__name__}")


def decode(value):
    if not isinstance(value, dict):
        return value
    kind, content = value["type"], value["value"]
    if kind == "map": return {decode(k): decode(v) for k, v in content}
    if kind in {"tuple", "list", "set", "frozenset"}:
        return {"tuple": tuple, "list": list, "set": set, "frozenset": frozenset}[kind](decode(v) for v in content)
    if kind == "float" and content in {"inf", "-inf"}: return float(content)
    if kind == "dataclass":
        classes = {m + "." + name: cls for m in CLASS_MODULES for name, cls in vars(importlib.import_module(m)).items()
                   if isinstance(cls, type) and dataclasses.is_dataclass(cls) and cls.__module__ == m}
        if value["class"] not in classes: raise ValueError("unregistered checkpoint class")
        return classes[value["class"]](**decode(content))
    raise ValueError("unsupported checkpoint encoding")


def source_hashes(root=ROOT):
    paths = []
    for folder in ("core", "orchestrator", "planners", "training", "tools", "schemas", "factorio_mod", "factorio_training_lab", "scripts"):
        paths.extend(p for p in (root / folder).rglob("*") if p.is_file() and p.suffix in {".py", ".lua", ".json", ".sh"})
    paths.extend((root / "docs/rl").glob("*guidelines.json"))
    for catalog in list((root / "docs/rl").glob("*guidelines.json")):
        for rule in json.loads(catalog.read_text()).get("rules", []):
            if rule.get("source"):
                source = (root / rule["source"]).resolve()
                if not source.is_relative_to(root.resolve()): raise ValueError("catalog source escapes repository")
                paths.append(source)
    return {str(p.relative_to(root)): file_hash(p) for p in sorted(paths)}


def runtime_state():
    state = {module: {name: getattr(importlib.import_module(module), name) for name in names}
             for module, names in RUNTIME.items()}
    # Host monotonic epochs do not survive reboot. Persist elapsed waits.
    pending = state["orchestrator.construction_coverage"]["_PENDING_POWER_BRIDGES"]
    state["orchestrator.construction_coverage"]["_PENDING_POWER_BRIDGES"] = {
        key: max(0, time.monotonic() - timestamp) for key, timestamp in pending.items()}
    return state


def catalog_hashes(settings):
    if not settings.get("catalog"):
        return {}
    result = {}
    for name in {settings["catalog"], "docs/rl/laya_ops_guidelines.json"}:
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT.resolve()):
            raise ValueError("checkpoint catalog must be repository-owned")
        result[str(path.relative_to(ROOT))] = file_hash(path)
        for rule in json.loads(path.read_text()).get("rules", []):
            if rule.get("source"):
                source = (ROOT / rule["source"]).resolve()
                if not source.is_relative_to(ROOT.resolve()): raise ValueError("catalog quote escapes repository")
                result[str(source.relative_to(ROOT))] = file_hash(source)
    return result


def restore_runtime(state):
    if set(state) != set(RUNTIME) or any(set(state[m]) != set(names) for m, names in RUNTIME.items()):
        raise ValueError("checkpoint runtime registry mismatch")
    for module, values in state.items():
        owner = importlib.import_module(module)
        for name, value in values.items():
            if module == "orchestrator.construction_coverage" and name == "_PENDING_POWER_BRIDGES":
                value = {key: time.monotonic() - age for key, age in value.items()}
            current = getattr(owner, name)
            if isinstance(current, dict): current.clear(); current.update(value)
            elif isinstance(current, set): current.clear(); current.update(value)
            elif isinstance(current, list): current[:] = value
            elif dataclasses.is_dataclass(current):
                if type(current) is not type(value): raise ValueError("checkpoint runtime type changed")
                for field in dataclasses.fields(current):
                    before, after = getattr(current, field.name), getattr(value, field.name)
                    if isinstance(before, dict): before.clear(); before.update(after)
                    elif isinstance(before, set): before.clear(); before.update(after)
                    else: setattr(current, field.name, after)
            else: setattr(owner, name, value)


def session_state(session):
    return {"episode_id": session.episode_id, "reference_point": session.reference_point,
            "runtime": runtime_state(),
            "evaluation": {name: getattr(session, name) for name in (
                "_baseline", "_history", "starter_facts", "foundation_facts", "acceptance_fresh", "acceptance_checks",
                "foundation_observations") if hasattr(session, name)},
            "accepted": set(session._evaluator.reached) if hasattr(session, "_evaluator") else set()}


def restore_session(session, state):
    from orchestrator.checkpoint_milestones import CheckpointEvaluator
    if session.episode_id != state["episode_id"] or session.reference_point != state["reference_point"]:
        raise ValueError("checkpoint logical session scope changed")
    restore_runtime(state["runtime"])
    for name, value in state["evaluation"].items(): setattr(session, name, value)
    if state["evaluation"]:
        session._evaluator = CheckpointEvaluator()
        session._evaluator.reached = set(state["accepted"])


def _sidecars(root):
    # Register authoritative state only, never credentials or stale report exports.
    for folder in ("server/logs/deterministic-material-reservations", "server/logs/deterministic-bootstrap-districts",
                   "server/logs/deterministic-bootstrap-work", "server/script-output/logs", "evidence"):
        for p in (root / folder).rglob("*"):
            if p.is_file() and p.suffix in {".json", ".jsonl"}:
                yield p


def _engine(root):
    pid = int((root / "server/factorio.pid").read_text())
    return file_hash(Path(f"/proc/{pid}/exe").resolve())


def load_bundle(path):
    path = Path(path).resolve()
    meta = json.loads((path / "checkpoint.json").read_text())
    VALIDATOR.validate(meta)
    if meta.get("version") != VERSION or meta.get("production_authority") is not False:
        raise ValueError("incompatible disposable ops checkpoint")
    if meta.get("source_hashes") != source_hashes():
        raise ValueError("checkpoint source/schema/catalog mismatch; an explicit continuation migration is required")
    if not {"source.zip", "controller.json", "engine-state.json"} <= set(meta["files"]):
        raise ValueError("incomplete ops checkpoint generation")
    for name, digest in meta["files"].items():
        source = (path / name).resolve()
        if not source.is_relative_to(path) or not source.is_file() or file_hash(source) != digest:
            raise ValueError("checkpoint hash or path mismatch")
    controller = decode(json.loads((path / "controller.json").read_text()))
    if meta.get("effective_catalog_hashes", {}) != catalog_hashes(controller["settings"]):
        raise ValueError("effective catalog/quotes changed since checkpoint")
    if controller["run_id"] != meta["logical_run_id"] or controller["session"]["episode_id"] != meta["logical_episode_id"]:
        raise ValueError("checkpoint logical identity mismatch")
    return meta, controller


def capture(adapter, policy, session, destination, controller):
    """Publish only after save, authoritative sidecars and controller agree at one paused boundary."""
    verify_instance(adapter.root)
    destination = Path(destination).resolve()
    if destination.exists() or not destination.is_relative_to((ROOT / "data").resolve()):
        raise ValueError("checkpoint requires a new repository data path")
    staging = destination.with_name("." + destination.name + ".pending-" + uuid.uuid4().hex)
    started = time.monotonic()
    adapter.bridge.command('/sc game.tick_paused=true;game.ticks_to_run=0;rcon.print("ops-checkpoint-paused")')
    try:
        saved = adapter.checkpoint()  # unique save name, completion barrier and post-save equality
        staging.mkdir(parents=True)
        shutil.copy2(saved["save"], staging / "source.zip")
        (staging / "engine-state.json").write_text(json.dumps(saved["facts"], sort_keys=True))
        controller = {**controller, "session": session_state(session), "rng": policy.rng.getstate(),
                      "policy": {"class": type(policy).__name__, "epsilon": getattr(policy, "epsilon", None),
                                 "preferences": getattr(policy, "preferences", None)}}
        for path in _sidecars(adapter.root):
            target = staging / "context" / path.relative_to(adapter.root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        (staging / "controller.json").write_text(json.dumps(encode(controller), allow_nan=False))
        facts = adapter.query()
        if facts != saved["facts"]:
            raise ValueError("world changed during coherent ops capture")
        meta = {"version": VERSION, "production_authority": False, "created_at": time.time(),
                "logical_run_id": controller["run_id"], "logical_episode_id": session.episode_id,
                "source_root": str(adapter.root), "tick": facts["tick"], "engine_sha256": _engine(adapter.root),
                "lineage": {"fixture_assisted": adapter.manifest.get("fixture_assisted", False),
                            "comparison_eligible": adapter.manifest.get("comparison_eligible", False)},
                "source_hashes": source_hashes(), "mod_hashes": adapter.manifest["mod_hashes"],
                "effective_catalog_hashes": catalog_hashes(controller["settings"]),
                "ancestor_source_sha256": saved["ancestor_source_sha256"],
                "capture_seconds": time.monotonic() - started,
                "files": {str(p.relative_to(staging)): file_hash(p) for p in staging.rglob("*") if p.is_file()}}
        meta["cumulative_active_wall_seconds"] = controller["elapsed_wall"] + meta["capture_seconds"]
        (staging / "checkpoint.json").write_text(json.dumps(meta, indent=2))
        staging.rename(destination)
        return meta
    finally:
        if hasattr(adapter, "go_live"): adapter.go_live()


def restore_context(bundle, root):
    meta, state = load_bundle(bundle)
    root = owned_root(root)
    verify_instance(root)
    if _engine(root) != meta["engine_sha256"]:
        raise ValueError("checkpoint engine mismatch")
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest["source_sha256"] != meta["files"]["source.zip"] or manifest["mod_hashes"] != meta["mod_hashes"]:
        raise ValueError("checkpoint save/mod provenance mismatch")
    # Newly prepared roots must be empty of controller context; never overlay a later generation.
    for name in meta["files"]:
        if name.startswith("context/"):
            target = root / name.removeprefix("context/")
            if target.exists(): raise ValueError("restore would replace existing controller context")
    for name in meta["files"]:
        if name.startswith("context/"):
            target = root / name.removeprefix("context/")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(bundle) / name, target)
    return meta, state


def verify_restored(adapter, bundle):
    meta, state = load_bundle(bundle)
    manifest = json.loads((adapter.root / "manifest.json").read_text())
    if (manifest.get("source_sha256") != meta["files"]["source.zip"] or manifest.get("mod_hashes") != meta["mod_hashes"]
            or manifest.get("ops_recovery", {}).get("bundle") != str(Path(bundle).resolve())):
        raise ValueError("restored root was not prepared from this exact ops generation")
    for name, digest in meta["files"].items():
        if name.startswith("context/") and file_hash(adapter.root / name.removeprefix("context/")) != digest:
            raise ValueError("restored controller sidecar changed before recovery")
    adapter.bridge.command('/sc game.tick_paused=true;game.ticks_to_run=0;rcon.print("verify-ops-restore")')
    expected = json.loads((Path(bundle) / "engine-state.json").read_text())
    adapter.extra_recipes = tuple(state.get("extra_recipes", ()))
    actual = adapter.query()
    if actual != expected or actual["tick"] != meta["tick"]:
        raise ValueError("restored world/tick differs from coherent checkpoint")
    if _engine(adapter.root) != meta["engine_sha256"]:
        raise ValueError("restored executable differs")
    return meta, state
