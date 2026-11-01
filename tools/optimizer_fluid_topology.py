# Path: tools/optimizer_fluid_topology.py | Purpose: Check a bound optimizer artifact's declared fluid topology.
"""Diagnostic composition; no result authorizes construction or authenticates a runtime."""

from core.fluid_topology import FluidPrototypeCatalog, validate_fluid_topology
from core.fluid_topology_intent import fluid_topology_intent
from core.optimizer_artifact_binding import BoundArtifactPlan, bind_artifact_to_construction_plan


def check_bound_fluid_topology(bound, *, catalog, snapshot, reference_tick, max_snapshot_age_ticks,
                               underground_mask_facts=None):
    """Rebind one artifact and compare its pinned intent with a supplied snapshot."""
    if not isinstance(bound, BoundArtifactPlan) or not isinstance(catalog, FluidPrototypeCatalog):
        raise ValueError("bound artifact and loaded fluid catalog are required")
    plan = bound.plan
    binding = plan.binding
    if (catalog.surface, catalog.force) != (binding.scope.surface, binding.scope.force):
        raise ValueError("fluid catalog scope differs from the bound artifact")
    artifact = bound.artifact
    rebound = bind_artifact_to_construction_plan(
        artifact, scope=binding.scope, project_id=binding.project_id, transaction_id=binding.transaction_id,
        throughput_output=plan.throughput_output, throughput_target=plan.throughput_target,
        throughput_unit=plan.throughput_unit, expected_artifact_sha256=bound.artifact_sha256,
        expected_plan_hash=bound.binding_sha256)
    if rebound.plan != plan or rebound.canonical_binding != bound.canonical_binding:
        raise ValueError("fluid artifact binding is inconsistent")
    context = artifact.get("planning_context")
    data = context.get("data") if isinstance(context, dict) else None
    intent = data.get("fluid_topology_intent") if isinstance(data, dict) else None
    if (not isinstance(intent, dict) or set(intent) != {"version", "segments", "permitted_attachments"}
            or intent.get("version") != "fluid-topology-intent-v1"):
        raise ValueError("bound artifact lacks explicit fluid topology intent")
    normalized = fluid_topology_intent(artifact["plan"], intent["segments"], intent["permitted_attachments"])
    if normalized != intent:
        raise ValueError("bound fluid topology intent is not canonical")
    scoped_plan = {**artifact["plan"], "surface": binding.scope.surface, "force": binding.scope.force}
    result = validate_fluid_topology(scoped_plan, catalog=catalog, declared_segments=intent["segments"],
                                     permitted_attachments=intent["permitted_attachments"], snapshot=snapshot,
                                     reference_tick=reference_tick, max_snapshot_age_ticks=max_snapshot_age_ticks,
                                     underground_mask_facts=underground_mask_facts)
    return {**result, "binding": binding.to_dict(), "artifact_sha256": bound.artifact_sha256,
            "catalog_sha256": catalog.sha256, "intent_trust": "unverified"}


def make_candidate_fluid_diagnostic(bound, *, episode_id, network_anchor, fluid_capture,
                                    underground_mask_capture=None):
    """Return an observation-only runner hook for one pinned candidate and catalog."""
    import hashlib
    from collections.abc import Mapping

    if (not isinstance(fluid_capture, dict) or set(fluid_capture) != {"sha256", "json"}
            or not isinstance(fluid_capture["sha256"], str) or not isinstance(fluid_capture["json"], str)):
        raise ValueError("pinned fluid catalog is invalid")
    raw = fluid_capture["json"].encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != fluid_capture["sha256"]:
        raise ValueError("fluid catalog hash does not match the pinned bytes")
    anchor = dict(network_anchor)
    mask_raw = None
    mask_digest = None
    if underground_mask_capture is not None:
        if (not isinstance(underground_mask_capture, dict)
                or set(underground_mask_capture) != {"sha256", "json"}
                or not isinstance(underground_mask_capture["sha256"], str)
                or not isinstance(underground_mask_capture["json"], str)):
            raise ValueError("pinned underground mask is invalid")
        mask_raw = underground_mask_capture["json"].encode("utf-8")
        mask_digest = hashlib.sha256(mask_raw).hexdigest()
        if mask_digest != underground_mask_capture["sha256"]:
            raise ValueError("underground mask hash does not match the pinned bytes")

    def diagnose(adapter, facts, candidates, response):
        from core.fluid_topology import load_fluid_prototype_catalog
        from core.underground_tile_blocking import load_underground_mask_facts
        from orchestrator.material_reservations import MaterialReservationError, MaterialReservationLedger
        from training.laya_saved_world import SavedWorldError
        from tools.optimizer_saved_world import (_annotate_candidate, _stock_observation,
                                                  optimizer_observation_candidate)

        values = response.get("validations")
        if not isinstance(values, Mapping):
            return values
        results = dict(values)
        for candidate in candidates:
            if not isinstance(candidate, Mapping) or candidate.get("optimizer_binding") != bound.plan.binding.to_dict():
                continue
            option_id = candidate.get("option_id")
            entry = results.get(option_id)
            if not isinstance(entry, Mapping):
                continue
            summary = {"status": "unavailable", "reason": "diagnostic_inputs_unavailable",
                       "catalog_sha256": digest, "dispatch_authorized": False}
            try:
                ledger = MaterialReservationLedger(adapter.root / "server/script-output", episode_id=episode_id,
                                                   surface=adapter.surface, force=adapter.force)
                expected = optimizer_observation_candidate(adapter, bound, ledger, episode_id=episode_id,
                                                            network_anchor=anchor)
                expected = _annotate_candidate(expected, _stock_observation(facts, bound, ledger, anchor), bound, ledger)
                if candidate != expected:
                    summary["reason"] = "candidate_or_funding_changed"
                elif not isinstance(facts, Mapping) or not isinstance(facts.get("mods"), Mapping):
                    summary["reason"] = "runtime_mods_unavailable"
                elif type(response.get("tick")) is not int or not isinstance(entry.get("fluid_topology_snapshot"), Mapping):
                    summary["reason"] = "snapshot_or_reference_tick_unavailable"
                else:
                    catalog = load_fluid_prototype_catalog(raw, expected_sha256=digest, surface=adapter.surface,
                                                          force=adapter.force, active_mods=facts["mods"])
                    snapshot = entry["fluid_topology_snapshot"]
                    # Factorio encodes an empty Lua table as {}, including an empty entity list.
                    normalized_empty = snapshot.get("entities") == {}
                    if normalized_empty:
                        snapshot = {**snapshot, "entities": []}
                    masks = (None if mask_raw is None else
                             load_underground_mask_facts(mask_raw, expected_sha256=mask_digest, active_mods=facts["mods"]))
                    report = check_bound_fluid_topology(bound, catalog=catalog,
                        snapshot=snapshot, reference_tick=response["tick"], max_snapshot_age_ticks=0,
                        underground_mask_facts=masks)
                    summary = {"status": report["status"], "catalog_sha256": digest,
                               "snapshot_tick": entry["fluid_topology_snapshot"].get("tick"),
                               "reference_tick": response["tick"], "normalized_empty_entities": normalized_empty,
                               "conflict_count": len(report["conflicts"]),
                               "conflicts": report["conflicts"][:16], "unverified_count": len(report["unverified"]),
                               "unverified": report["unverified"][:16], "network_count": len(report["networks"]),
                               "underground_pairs": report["underground_pairs"], "dispatch_authorized": False}
                    if "underground_tile_blocking" in report:
                        summary["underground_tile_blocking"] = report["underground_tile_blocking"]
            except (ValueError, TypeError, KeyError, OSError, OverflowError, MaterialReservationError, SavedWorldError):
                summary["reason"] = "diagnostic_inputs_invalid_or_stale"
            results[option_id] = {**entry, "fluid_topology_diagnostic": summary}
        return results

    return diagnose
