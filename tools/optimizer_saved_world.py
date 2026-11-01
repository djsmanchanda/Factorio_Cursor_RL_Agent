# Path: tools/optimizer_saved_world.py | Purpose: Offer and execute a bound optimizer block on a verified disposable saved world.
"""Composition layer for explicit selection; it makes no strategy or production claim."""

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from jsonschema import Draft7Validator

from core.optimizer_artifact_binding import BoundArtifactPlan, bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from orchestrator.material_reservations import MaterialReservationError, MaterialReservationLedger
from orchestrator.optimizer_layout_preview import prepare_optimizer_layout_preview, prepare_optimizer_observation_preview
from planners.plan_validation import actions
from training.laya_saved_world import (OPTIMIZER_REQUIRED_CHECKS, SAVED_WORLD_GHOST_FIELDS, SavedWorldAdapter,
                                       SavedWorldError, file_hash, owned_root, verify_instance)

_CONFIG_SCHEMA = Path(__file__).resolve().parents[1] / "schemas/optimizer_candidate_config.schema.json"


def _validate_candidate_config(config):
    schema = json.loads(_CONFIG_SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft7Validator(schema).iter_errors(config))
    if errors:
        raise ValueError(f"optimizer candidate configuration is invalid: {errors[0].message}")
    try:
        finite = all(math.isfinite(value) for value in config["network_anchor"].values())
    except (TypeError, OverflowError):
        finite = False
    if not finite:
        raise ValueError("optimizer candidate network anchor must be finite")
    if "fluid_catalog" in config:
        try:
            raw_catalog = config["fluid_catalog"]["json"].encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("fluid catalog must be UTF-8") from error
        if len(raw_catalog) > 16 * 1024 * 1024:
            raise ValueError("fluid catalog exceeds the byte limit")
        if hashlib.sha256(raw_catalog).hexdigest() != config["fluid_catalog"]["sha256"]:
            raise ValueError("fluid catalog hash does not match the pinned bytes")
    if "underground_mask" in config:
        try:
            raw_mask = config["underground_mask"]["json"].encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("underground mask must be UTF-8") from error
        if len(raw_mask) > 1024 * 1024:
            raise ValueError("underground mask exceeds the byte limit")
        if hashlib.sha256(raw_mask).hexdigest() != config["underground_mask"]["sha256"]:
            raise ValueError("underground mask hash does not match the pinned bytes")
    if "demand_model" in config:
        model = config["demand_model"]
        try:
            raw_demand = model["catalog_json"].encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("demand catalog must be UTF-8") from error
        if len(raw_demand) > 16 * 1024 * 1024:
            raise ValueError("demand catalog exceeds the byte limit")
        if hashlib.sha256(raw_demand).hexdigest() != model["provenance"].get("catalog_sha256"):
            raise ValueError("demand catalog hash does not match its provenance")


def optimizer_candidate_config(bound: BoundArtifactPlan, *, episode_id: str, network_anchor: dict,
                               fluid_catalog_bytes: bytes | None = None, demand_model: dict | None = None,
                               underground_mask_bytes: bytes | None = None) -> dict:
    """Create a self-contained pinned input for the observation-only runner hook."""
    if not isinstance(bound, BoundArtifactPlan):
        raise ValueError("bound optimizer plan is required")
    plan, binding = bound.plan, bound.plan.binding
    config = {"version": "optimizer-candidate-config-v1", "scope": binding.scope.to_dict(),
              "episode_id": episode_id, "project_id": binding.project_id,
              "transaction_id": binding.transaction_id, "artifact_sha256": bound.artifact_sha256,
              "plan_sha256": bound.binding_sha256,
              "throughput": {"output": plan.throughput_output, "target": str(plan.throughput_target),
                             "unit": plan.throughput_unit},
              "network_anchor": dict(network_anchor), "artifact": bound.artifact}
    if fluid_catalog_bytes is not None:
        if not isinstance(fluid_catalog_bytes, bytes):
            raise ValueError("fluid catalog must be UTF-8 bytes")
        try:
            catalog_text = fluid_catalog_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("fluid catalog must be UTF-8 bytes") from error
        config["fluid_catalog"] = {"sha256": hashlib.sha256(fluid_catalog_bytes).hexdigest(), "json": catalog_text}
    if underground_mask_bytes is not None:
        if not isinstance(underground_mask_bytes, bytes):
            raise ValueError("underground mask must be UTF-8 bytes")
        try:
            mask_text = underground_mask_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("underground mask must be UTF-8 bytes") from error
        config["underground_mask"] = {"sha256": hashlib.sha256(underground_mask_bytes).hexdigest(), "json": mask_text}
    if demand_model is not None:
        config["demand_model"] = json.loads(json.dumps(demand_model, allow_nan=False))
    _validate_candidate_config(config)
    return config


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate optimizer configuration field: {key}")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError(f"invalid optimizer configuration number: {value}")


def load_optimizer_candidate_config(path: Path, adapter: SavedWorldAdapter):
    """Rebind a pinned artifact and verify its active held ledger before use.

    Configuration and ledger are local assertions, not authenticated runtime
    identity, fluid validation, ownership, or permission to dispatch.
    """
    if not isinstance(adapter, SavedWorldAdapter):
        raise ValueError("saved-world adapter is required")
    raw = Path(path).read_bytes()
    try:
        config = json.loads(raw, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("optimizer candidate configuration is not valid JSON") from error
    _validate_candidate_config(config)
    root = owned_root(adapter.root)
    scope = ConstructionScope(**config["scope"])
    if ((root / "manifest.json").is_symlink() or scope.instance_id != str(root)
            or scope.surface != adapter.surface or scope.force != adapter.force
            or scope.manifest_digest != file_hash(root / "manifest.json")):
        raise ValueError("optimizer candidate configuration does not match this saved-world scope")
    throughput = config["throughput"]
    bound = bind_artifact_to_construction_plan(
        config["artifact"], scope=scope, project_id=config["project_id"], transaction_id=config["transaction_id"],
        throughput_output=throughput["output"], throughput_target=throughput["target"], throughput_unit=throughput["unit"],
        expected_artifact_sha256=config["artifact_sha256"], expected_plan_hash=config["plan_sha256"])
    ledger = MaterialReservationLedger(root / "server/script-output", episode_id=config["episode_id"],
                                       surface=adapter.surface, force=adapter.force)
    candidate = optimizer_observation_candidate(adapter, bound, ledger, episode_id=config["episode_id"],
                                                network_anchor=config["network_anchor"])
    fluid_capture = config.get("fluid_catalog")
    provider = optimizer_candidate_provider(bound, episode_id=config["episode_id"],
                                             network_anchor=config["network_anchor"], fluid_capture=fluid_capture,
                                             demand_model=config.get("demand_model"),
                                             underground_mask_capture=config.get("underground_mask"))
    metadata = {"config_path": str(Path(path).resolve()), "config_sha256": hashlib.sha256(raw).hexdigest(),
                "provider_code_sha256": file_hash(Path(__file__)), "config_schema_sha256": file_hash(_CONFIG_SCHEMA),
                "artifact_sha256": bound.artifact_sha256, "plan_sha256": bound.binding_sha256,
                "funding_context": candidate["funding_context"], "scope": scope.to_dict(),
                "dispatch_authorized": False}
    if fluid_capture is not None:
        metadata["fluid_catalog_sha256"] = fluid_capture["sha256"]
        root_code = Path(__file__).resolve().parents[1]
        metadata["fluid_diagnostic_code_sha256"] = {str(path.relative_to(root_code)): file_hash(path) for path in (
            root_code / "tools/optimizer_fluid_topology.py", root_code / "core/fluid_topology.py",
            root_code / "core/fluid_topology_intent.py")}
        data = bound.artifact.get("planning_context", {}).get("data", {})
        if isinstance(data, dict) and "power_attachment" in data:
            metadata["power_diagnostic_code_sha256"] = file_hash(root_code / "tools/optimizer_power_topology.py")
    if "underground_mask" in config:
        metadata["underground_mask_sha256"] = config["underground_mask"]["sha256"]
        metadata["underground_mask_code_sha256"] = file_hash(Path(__file__).resolve().parents[1] / "core/underground_tile_blocking.py")
    if "demand_model" in config:
        metadata["demand_catalog_sha256"] = config["demand_model"]["provenance"]["catalog_sha256"]
        root_code = Path(__file__).resolve().parents[1]
        metadata["demand_code_sha256"] = {str(path.relative_to(root_code)): file_hash(path) for path in (
            root_code / "tools/optimizer_demand_observation.py", root_code / "core/catalog_demand.py",
            root_code / "core/fixed_recipe_balance.py", root_code / "core/material_balance.py",
            root_code / "core/recipe_dag.py", root_code / "schemas/recipe_catalog.schema.json")}
    if "funding_status" in candidate:
        metadata["funding_status"] = candidate["funding_status"]
    return provider, metadata


def optimizer_candidate(adapter: SavedWorldAdapter, bound: BoundArtifactPlan, ledger: MaterialReservationLedger, *,
                        episode_id: str, network_anchor: dict) -> dict:
    """Offer the exact fully held block; live validation still happens at selection."""
    return _candidate(adapter, bound, ledger, episode_id=episode_id, network_anchor=network_anchor,
                      observation_only=False)


def optimizer_observation_candidate(adapter: SavedWorldAdapter, bound: BoundArtifactPlan,
                                    ledger: MaterialReservationLedger, *, episode_id: str,
                                    network_anchor: dict) -> dict:
    """Observe a fully or partially held project without authorizing dispatch."""
    return _candidate(adapter, bound, ledger, episode_id=episode_id, network_anchor=network_anchor,
                      observation_only=True)


def _stock_observation(facts, bound, ledger, anchor):
    """Compare a scoped snapshot with held bills without mutating allocations."""
    def unavailable(reason):
        return {"status": "unavailable", "reason": reason}

    def finite_number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False

    def item_counts(value):
        if not isinstance(value, Mapping):
            return None
        result = {}
        for item, count in value.items():
            if (not isinstance(item, str) or not item or not finite_number(count)
                    or count < 0 or int(count) != count):
                return None
            result[item] = int(count)
        return result

    scope = bound.plan.binding.scope
    if (not isinstance(facts, Mapping) or facts.get("version") != "saved-world-v1"
            or facts.get("surface") != scope.surface or facts.get("force") != scope.force
            or type(facts.get("tick")) is not int or facts["tick"] < 0
            or not isinstance(facts.get("ports"), list) or not isinstance(facts.get("networks"), Mapping)):
        return unavailable("invalid_or_missing_snapshot")
    for port in facts["ports"]:
        position = port.get("position") if isinstance(port, Mapping) else None
        if (not isinstance(position, Mapping) or set(position) != {"x", "y"}
                or not all(finite_number(value) for value in position.values())):
            return unavailable("invalid_port_position")
    ports = [port for port in facts["ports"] if port["position"] == anchor]
    if len(ports) != 1:
        return unavailable("anchor_missing_or_ambiguous")
    port = ports[0]
    energy = port.get("energy")
    if not finite_number(energy) or energy <= 0:
        return unavailable("anchor_unpowered_or_unknown")
    network_id = port.get("network")
    networks = facts["networks"]
    if not isinstance(network_id, str) or not network_id or not isinstance(networks.get(network_id), Mapping):
        return unavailable("network_unavailable")
    network = networks[network_id]
    inventory, ghosts = item_counts(network.get("items")), item_counts(network.get("reserved"))
    if inventory is None or ghosts is None:
        return unavailable("invalid_network_stock")
    project_id = f"optimizer:{bound.plan.binding.project_id}"
    project = ledger.projects.get(project_id)
    if project is None:
        return unavailable("held_project_unavailable")
    competing = Counter()
    for other in ledger.projects.values():
        if other.project_id != project_id and ledger.project_is_active(other.project_id):
            competing.update(other.reserved)
    result = {}
    for item, required in bound.plan.required_materials:
        stored, ghost_claims = inventory.get(item, 0), ghosts.get(item, 0)
        other_holds = competing.get(item, 0)
        available = max(0, stored - ghost_claims - other_holds)
        result[item] = {"required": required, "observed_inventory": stored, "ghost_claims": ghost_claims,
                        "competing_ledger_holds": other_holds, "project_ledger_hold": project.reserved.get(item, 0),
                        "available_after_claims": available, "shortfall_after_claims": max(0, required - available)}
    return {"status": "available", "tick": facts["tick"], "network": network_id,
            "ledger_revision": ledger.revision, "items": result}


def _validate_stock_observation(observation, bound, ledger):
    """Check internal arithmetic and ledger consistency, not observation origin."""
    if not isinstance(observation, dict):
        raise ValueError("optimizer stock observation is invalid")
    if observation.get("status") == "unavailable":
        reasons = {"invalid_or_missing_snapshot", "invalid_port_position", "anchor_missing_or_ambiguous",
                   "anchor_unpowered_or_unknown", "network_unavailable", "invalid_network_stock",
                   "held_project_unavailable"}
        if (set(observation) != {"status", "reason"} or not isinstance(observation.get("reason"), str)
                or observation["reason"] not in reasons):
            raise ValueError("optimizer stock observation is invalid")
        return
    if (set(observation) != {"status", "tick", "network", "ledger_revision", "items"}
            or observation.get("status") != "available" or type(observation.get("tick")) is not int
            or observation["tick"] < 0 or not isinstance(observation.get("network"), str)
            or not observation["network"] or type(observation.get("ledger_revision")) is not int
            or observation["ledger_revision"] != ledger.revision or not isinstance(observation.get("items"), dict)):
        raise ValueError("optimizer stock observation is invalid or stale")
    required = dict(bound.plan.required_materials)
    if set(observation["items"]) != set(required):
        raise ValueError("optimizer stock observation does not match the bill")
    project_id = f"optimizer:{bound.plan.binding.project_id}"
    project = ledger.projects.get(project_id)
    if project is None:
        raise ValueError("optimizer stock observation has no held project")
    competing = Counter()
    for other in ledger.projects.values():
        if other.project_id != project_id and ledger.project_is_active(other.project_id):
            competing.update(other.reserved)
    fields = {"required", "observed_inventory", "ghost_claims", "competing_ledger_holds",
              "project_ledger_hold", "available_after_claims", "shortfall_after_claims"}
    for item, count in required.items():
        row = observation["items"][item]
        if (not isinstance(row, dict) or set(row) != fields
                or any(type(value) is not int or value < 0 for value in row.values())
                or row["required"] != count or row["competing_ledger_holds"] != competing.get(item, 0)
                or row["project_ledger_hold"] != project.reserved.get(item, 0)):
            raise ValueError("optimizer stock observation conflicts with its bill or ledger")
        available = max(0, row["observed_inventory"] - row["ghost_claims"] - row["competing_ledger_holds"])
        if row["available_after_claims"] != available or row["shortfall_after_claims"] != max(0, count - available):
            raise ValueError("optimizer stock observation has inconsistent arithmetic")


def _annotate_candidate(candidate, observation, bound, ledger):
    """Bind a stock observation to a proposal ID; the observation remains untrusted."""
    _validate_stock_observation(observation, bound, ledger)
    payload = {"domain": "factorio.optimizer.candidate_observation.v1", "candidate_id": candidate["option_id"],
               "stock_observation": observation}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    return {**candidate, "option_id": "optimizer." + hashlib.sha256(encoded).hexdigest(),
            "stock_observation": json.loads(json.dumps(observation, allow_nan=False))}


def _candidate(adapter, bound, ledger, *, episode_id, network_anchor, observation_only):
    if not isinstance(adapter, SavedWorldAdapter) or not isinstance(bound, BoundArtifactPlan):
        raise ValueError("saved-world adapter and bound optimizer plan are required")
    root = owned_root(adapter.root)
    if (root / "manifest.json").is_symlink():
        raise ValueError("saved-world manifest may not be a symbolic link")
    verify_instance(root, runtime=adapter.runtime)
    scope = bound.plan.binding.scope
    if (scope.instance_id != str(root) or scope.manifest_digest != file_hash(root / "manifest.json")
            or scope.surface != adapter.surface or scope.force != adapter.force):
        raise ValueError("optimizer binding does not match this disposable saved world")
    if not isinstance(episode_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", episode_id):
        raise ValueError("episode_id must be a safe ledger identifier")
    if not isinstance(ledger, MaterialReservationLedger):
        raise ValueError("material reservation ledger is required")
    expected_path = root / "server/logs/deterministic-material-reservations" / f"{episode_id}.json"
    server = (root / "server").resolve()
    if (not server.is_relative_to(root) or expected_path.is_symlink()
            or not expected_path.resolve().is_relative_to(server)
            or ledger.path.resolve() != expected_path.resolve() or not expected_path.is_file()):
        raise MaterialReservationError("optimizer ledger is not the saved world's persisted ledger")
    fresh = MaterialReservationLedger(root / "server/script-output", episode_id=episode_id,
                                      surface=adapter.surface, force=adapter.force)
    if (fresh.revision != ledger.revision or fresh.version != ledger.version
            or fresh.projects != ledger.projects or fresh.bootstrap_supply != ledger.bootstrap_supply):
        raise MaterialReservationError("optimizer ledger changed since it was loaded")
    if (not isinstance(network_anchor, dict) or set(network_anchor) != {"x", "y"}
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   for v in network_anchor.values())):
        raise ValueError("network_anchor must contain finite numeric x and y")
    preview = (prepare_optimizer_observation_preview(bound, fresh, episode_id=episode_id) if observation_only
               else prepare_optimizer_layout_preview(bound, fresh, episode_id=episode_id))
    plan = preview["build_plan"]
    if any(action.get("action_type") != "place_ghost" or set(action) - SAVED_WORLD_GHOST_FIELDS
           for action in actions(plan)):
        raise ValueError("optimizer plan uses settings unsupported by the saved-world executor")
    funding = {"episode_id": preview["ledger_episode_id"], "ledger_revision": preview["ledger_revision"]}
    identity = {"binding": bound.plan.binding.to_dict(), "plan": plan, "network_anchor": network_anchor,
                "funding": funding}
    if preview["funding"]["state"] != "ready":
        identity["funding_status"] = preview["funding"]
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()
    candidate = {"option_id": f"optimizer.{digest}", "family": "optimizer-block",
            "summary": f"Build optimizer block for {bound.plan.throughput_output}",
            "product": bound.plan.throughput_output, "plan": plan, "network_anchor": dict(network_anchor),
            "bill": dict(bound.plan.required_materials), "optimizer_binding": bound.plan.binding.to_dict(),
            "optimizer_artifact_sha256": bound.artifact_sha256, "funding_context": funding,
            "required_checks": list(OPTIMIZER_REQUIRED_CHECKS)}
    if "funding_status" in identity:
        candidate["funding_status"] = identity["funding_status"]
    return candidate


def optimizer_candidate_provider(bound: BoundArtifactPlan, *, episode_id: str, network_anchor: dict,
                                 fluid_capture: dict | None = None, demand_model: dict | None = None,
                                 underground_mask_capture: dict | None = None):
    """Make an observation-only clean-runner provider for one pre-bound block.

    The runner keeps external candidates out of its selectable menu. Every
    observation reloads the held ledger, including partial holds; no reservation
    or submission occurs.
    """
    if not isinstance(bound, BoundArtifactPlan):
        raise ValueError("bound optimizer plan is required")
    if not isinstance(episode_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", episode_id):
        raise ValueError("episode_id must be a safe ledger identifier")
    if not isinstance(network_anchor, dict):
        raise ValueError("network_anchor must be a coordinate mapping")
    anchor = dict(network_anchor)
    if underground_mask_capture is not None and fluid_capture is None:
        raise ValueError("underground mask requires a pinned fluid catalog")

    def provide(adapter, facts, board):
        matching_needs = [need for need in board["needs"] if need.get("item") == bound.plan.throughput_output]
        if matching_needs and all(need.get("condition") == "satisfied" for need in matching_needs):
            return []
        if not isinstance(adapter, SavedWorldAdapter):
            raise ValueError("saved-world adapter is required")
        ledger = MaterialReservationLedger(adapter.root / "server/script-output", episode_id=episode_id,
                                           surface=adapter.surface, force=adapter.force)
        candidate = optimizer_observation_candidate(adapter, bound, ledger, episode_id=episode_id, network_anchor=anchor)
        observation = _stock_observation(facts, bound, ledger, anchor)
        return [_annotate_candidate(candidate, observation, bound, ledger)]

    # A pinned proposal may describe a fluid absent from the solid-only status
    # board. Expose it diagnostically without inventing a phase or stock need.
    provide.observe_unlinked = True
    if demand_model is not None:
        from tools.optimizer_demand_observation import make_phase_fluid_demand_observer
        demand_observer = make_phase_fluid_demand_observer(bound, demand_model)
        def describe(facts, board, candidates):
            return {candidate["option_id"]: demand_observer(facts, board) for candidate in candidates
                    if candidate.get("optimizer_binding") == bound.plan.binding.to_dict()
                    and candidate.get("product") == bound.plan.throughput_output}
        provide.describe = describe
    if fluid_capture is not None:
        from tools.optimizer_fluid_topology import make_candidate_fluid_diagnostic
        fluid_validate = make_candidate_fluid_diagnostic(bound, episode_id=episode_id, network_anchor=anchor,
                                                         fluid_capture=fluid_capture,
                                                         underground_mask_capture=underground_mask_capture)
        data = bound.artifact.get("planning_context", {}).get("data", {})
        has_power = isinstance(data, dict) and "power_attachment" in data
        if has_power:
            from tools.optimizer_power_topology import add_candidate_power_diagnostics
            def validate(adapter, facts, candidates, response):
                results = fluid_validate(adapter, facts, candidates, response)
                return add_candidate_power_diagnostics(bound, episode_id=episode_id, network_anchor=anchor,
                    fluid_capture=fluid_capture, adapter=adapter, facts=facts, candidates=candidates,
                    response=response, validations=results)
            provide.validate = validate
        else:
            provide.validate = fluid_validate
        if underground_mask_capture is not None or has_power:
            def prepare_validation(candidates):
                result = []
                for candidate in candidates:
                    if candidate.get("optimizer_binding") == bound.plan.binding.to_dict():
                        candidate = dict(candidate)
                        if underground_mask_capture is not None:
                            candidate["inspect_underground_tiles"] = True
                        if has_power:
                            candidate["inspect_power_topology"] = True
                    result.append(candidate)
                return result
            provide.prepare_validation = prepare_validation
    return provide


_LUA_FLUID_WORLD_REASONS = frozenset({"underground_world_competitor", "surface_pipe_world_contact",
                                      "underground_world_unverified", "surface_pipe_world_unverified"})
_ATTACHMENT_REASONS = {"anchor_not_observed": "attachment_anchor_missing",
                       "anchor_identity_mismatch": "attachment_anchor_mismatch",
                       "anchor_prototype_mismatch": "attachment_anchor_mismatch",
                       "anchor_quality_unverified": "attachment_anchor_mismatch",
                       "anchor_network_unavailable": "attachment_anchor_network_unavailable"}


class OptimizerCheckVerifier:
    """Inspect the current world for ``fluid_purity`` and ``safe_infrastructure_attachment``.

    Both checks read the SAME query response (the engine's fluid-prototype survey,
    fluid-topology snapshot, same-query pole snapshot and collision validation) for a
    candidate that is rebuilt from the bound artifact and persisted ledger, so a
    caller's claim about the plan or the checks is never consulted. Results carry
    ``facts`` so a later evaluation can detect change. Passing authorizes nothing on
    the real base: it only lets the disposable saved-world path submit this block.
    """

    def __init__(self, bound, *, episode_id, network_anchor, fluid_capture=None, underground_mask_capture=None):
        if not isinstance(bound, BoundArtifactPlan):
            raise ValueError("bound optimizer plan is required")
        self.bound, self.episode_id, self.anchor = bound, episode_id, dict(network_anchor)
        self.fluid_capture, self.mask_capture = fluid_capture, underground_mask_capture
        if fluid_capture is not None and hashlib.sha256(fluid_capture["json"].encode("utf-8")).hexdigest() != fluid_capture["sha256"]:
            raise ValueError("fluid catalog hash does not match the pinned bytes")
        if underground_mask_capture is not None and fluid_capture is None:
            raise ValueError("underground mask requires a pinned fluid catalog")

    def _is_bound(self, candidate):
        return isinstance(candidate, Mapping) and candidate.get("optimizer_binding") == self.bound.plan.binding.to_dict()

    def prepare(self, candidate):
        """Ask the engine query for the snapshots the checks need (never caller-asserted)."""
        if not self._is_bound(candidate):
            return candidate
        prepared = {**candidate, "inspect_power_topology": True}
        if self.mask_capture is not None:
            prepared["inspect_underground_tiles"] = True
        return prepared

    def __call__(self, adapter, candidate, entry, response):
        failed = lambda reason: {name: {"reason": reason, "facts": {}} for name in OPTIMIZER_REQUIRED_CHECKS}
        if not self._is_bound(candidate):
            return failed("candidate_not_bound")
        if type(response.get("tick")) is not int:
            return failed("query_tick_unavailable")
        try:
            ledger = MaterialReservationLedger(adapter.root / "server/script-output", episode_id=self.episode_id,
                                               surface=adapter.surface, force=adapter.force)
            expected = optimizer_candidate(adapter, self.bound, ledger, episode_id=self.episode_id,
                                           network_anchor=self.anchor)
            if "stock_observation" in candidate:
                expected = _annotate_candidate(expected, candidate["stock_observation"], self.bound, ledger)
        except (ValueError, TypeError, KeyError, OSError, MaterialReservationError, SavedWorldError):
            return failed("candidate_or_funding_changed")
        if {k: v for k, v in candidate.items() if k != "check_evidence"} != expected:
            return failed("candidate_or_funding_changed")
        return {"fluid_purity": self._fluid_purity(entry, response["tick"]),
                "safe_infrastructure_attachment": self._attachment(entry, response["tick"])}

    def _fluid_purity(self, entry, tick):
        def result(reason, **facts):
            return {"reason": reason, "facts": facts}
        if entry.get("reason") in _LUA_FLUID_WORLD_REASONS:
            return result(entry["reason"])
        plan_actions = list(actions(self.bound.artifact["plan"]))
        survey = entry.get("fluid_prototype_survey")
        keys = ("actions", "with_fluidboxes", "without_fluidboxes", "unknown")
        if (not isinstance(survey, dict) or any(type(survey.get(k)) is not int or survey[k] < 0 for k in keys)
                or survey["actions"] != len(plan_actions) or survey["unknown"] != 0
                or survey["with_fluidboxes"] + survey["without_fluidboxes"] != survey["actions"]):
            return result("fluid_survey_unverified")
        # The pinned catalog is a second, independent fluid-port source for the same plan entities.
        catalog_ports = 0
        catalog = None
        if self.fluid_capture is not None:
            mods = (entry.get("power_topology_snapshot") or {}).get("mods")
            try:
                from core.fluid_topology import load_fluid_prototype_catalog
                catalog = load_fluid_prototype_catalog(
                    self.fluid_capture["json"].encode("utf-8"), expected_sha256=self.fluid_capture["sha256"],
                    surface=self.bound.plan.binding.scope.surface, force=self.bound.plan.binding.scope.force,
                    active_mods=mods)
            except (ValueError, TypeError, KeyError, AttributeError):
                return result("fluid_catalog_unverified")
            catalog_ports = sum(1 for a in plan_actions
                                if a["entity"] in catalog.entities and catalog.entities[a["entity"]].fluidboxes)
        fluid_entities = max(survey["with_fluidboxes"], catalog_ports)
        if fluid_entities == 0:
            return result(None, fluid_entities=0, topology="not_applicable")
        if catalog is None:
            return result("fluid_topology_unpinned")
        snapshot = entry.get("fluid_topology_snapshot")
        if not isinstance(snapshot, Mapping) or snapshot.get("tick") != tick:
            return result("fluid_topology_unverified")
        normalized = {**snapshot, "entities": []} if snapshot.get("entities") == {} else snapshot
        try:
            masks = None
            if self.mask_capture is not None:
                from core.underground_tile_blocking import load_underground_mask_facts
                masks = load_underground_mask_facts(self.mask_capture["json"].encode("utf-8"),
                    expected_sha256=self.mask_capture["sha256"], active_mods=entry["power_topology_snapshot"]["mods"])
            from tools.optimizer_fluid_topology import check_bound_fluid_topology
            report = check_bound_fluid_topology(self.bound, catalog=catalog, snapshot=normalized, reference_tick=tick,
                                                max_snapshot_age_ticks=0, underground_mask_facts=masks)
        except (ValueError, TypeError, KeyError, OverflowError):
            return result("fluid_topology_unverified")
        if report["status"] != "consistent":
            return result("fluid_topology_conflict" if report["status"] == "rejected" else "fluid_topology_unverified")
        return result(None, fluid_entities=fluid_entities, topology="consistent",
                      network_count=len(report["networks"]), catalog_sha256=catalog.sha256)

    def _attachment(self, entry, tick):
        def result(reason, **facts):
            return {"reason": reason, "facts": facts}
        if entry.get("reason") == "collision":
            return result("attachment_collision")
        data = self.bound.artifact.get("planning_context", {}).get("data", {})
        if not isinstance(data, dict) or "power_attachment" not in data or self.fluid_capture is None:
            return result("attachment_unpinned")
        snapshot = entry.get("power_topology_snapshot")
        if not isinstance(snapshot, Mapping):
            return result("attachment_snapshot_unavailable")
        from tools.optimizer_power_topology import diagnose_bound_power
        try:
            report = diagnose_bound_power(self.bound, catalog_bytes=self.fluid_capture["json"].encode("utf-8"),
                                          catalog_sha256=self.fluid_capture["sha256"], snapshot=dict(snapshot),
                                          reference_tick=tick)
        except (ValueError, TypeError, KeyError, OverflowError):
            return result("attachment_unverified")
        if report["status"] == "potential_conflict":
            return result("attachment_grid_merge")
        if report["status"] != "consistent":
            first = report["reasons"][0] if report["reasons"] else "unknown"
            return result(_ATTACHMENT_REASONS.get(first, "attachment_unverified:" + first))
        anchor = data["power_attachment"]["anchor"]
        return result(None, anchor_grid=report["anchor_grid"], anchor_unit=anchor["unit_number"],
                      anchor_position=anchor["position"], planned_poles=report["planned_poles"])


def optimizer_check_verifier(bound, *, episode_id, network_anchor, fluid_capture=None, underground_mask_capture=None):
    """Public factory; pass the result to ``SavedWorldAdapter.options(check_verifier=...)``."""
    return OptimizerCheckVerifier(bound, episode_id=episode_id, network_anchor=network_anchor,
                                  fluid_capture=fluid_capture, underground_mask_capture=underground_mask_capture)


def _revalidate_stock(adapter, bound, ledger, anchor, candidate):
    """Re-observe stock on a fresh scoped snapshot and require the hold to still be payable.

    Narrowest correct check: the candidate was already rebuilt from the persisted
    ledger (so holds are unchanged), hence what can change is the world. The fresh
    comparison must be available, on the option-time network, with no per-item
    shortfall after ghost claims and other projects' holds. The adapter's Lua
    validation re-checks raw network stock again just before submission.
    """
    fresh = _stock_observation(adapter.query(), bound, ledger, anchor)
    _validate_stock_observation(fresh, bound, ledger)
    if fresh["status"] != "available":
        raise SavedWorldError("selected plan failed stock revalidation: stock_revalidation_unavailable:" + fresh["reason"])
    prior = candidate.get("stock_observation")
    if isinstance(prior, dict) and prior.get("status") == "available":
        if prior["network"] != fresh["network"]:
            raise SavedWorldError("selected plan failed stock revalidation: stock_network_changed")
        if fresh["tick"] < prior["tick"]:
            raise SavedWorldError("selected plan failed stock revalidation: stock_observation_regressed")
    short = sorted(item for item, row in fresh["items"].items() if row["shortfall_after_claims"] > 0)
    if short:
        raise SavedWorldError("selected plan failed stock revalidation: stock_shortfall_at_execution:" + short[0])


def execute_optimizer_candidate(adapter: SavedWorldAdapter, bound: BoundArtifactPlan,
                                ledger: MaterialReservationLedger, *, episode_id: str,
                                network_anchor: dict, candidate: dict, selected: str,
                                measure_ticks: int = 1800, fluid_capture: dict | None = None,
                                underground_mask_capture: dict | None = None) -> dict:
    """Execute only the unchanged, explicitly selected candidate through the disposable adapter.

    The required checks run again on fresh world facts inside ``adapter.execute``
    against the option-time evidence carried by the candidate; the verifier is
    constructed here from the bound artifact, never accepted from the caller.
    """
    evidence = None
    if isinstance(candidate, dict) and "check_evidence" in candidate:
        candidate = dict(candidate)
        evidence = candidate.pop("check_evidence")
    expected = optimizer_candidate(adapter, bound, ledger, episode_id=episode_id, network_anchor=network_anchor)
    if isinstance(candidate, dict) and "stock_observation" in candidate:
        expected = _annotate_candidate(expected, candidate["stock_observation"], bound, ledger)
    if candidate != expected or selected != expected["option_id"]:
        raise ValueError("optimizer selection differs from the bound candidate")
    for path in (adapter.root / "evidence").glob("transaction-*.json"):
        try:
            if path.is_symlink() or json.loads(path.read_text(encoding="utf-8")).get("status") not in {"observed", "rejected"}:
                raise ValueError("unresolved saved-world transaction prevents optimizer submission")
        except (OSError, json.JSONDecodeError, AttributeError) as error:
            raise ValueError("unreadable saved-world transaction prevents optimizer submission") from error
    verifier = OptimizerCheckVerifier(bound, episode_id=episode_id, network_anchor=network_anchor,
                                      fluid_capture=fluid_capture, underground_mask_capture=underground_mask_capture)
    _revalidate_stock(adapter, bound, ledger, network_anchor, candidate)
    presented = {**candidate, "check_evidence": evidence} if evidence is not None else candidate
    return adapter.execute(selected, [presented], measure_ticks, check_verifier=verifier)
