# Path: tools/optimizer_consumer_observation.py | Purpose: Observe an explicit consumer in a bound disposable world.
"""Read-only composition; correlation is not runtime authentication or commissioning."""

from core.optimizer_artifact_binding import BoundArtifactPlan, bind_artifact_to_construction_plan
from training.laya_saved_world import SavedWorldAdapter, file_hash, owned_root, verify_instance


def observe_bound_consumer(adapter, bound, *, entity, position, unit_number):
    """Read one explicit consumer from a verified disposable adapter.

    This does not establish a connection between the consumer and the optimizer
    block. Callers need separately authorized runtime access and must verify
    provenance and continuity before treating the result as evidence.
    """
    if not isinstance(adapter, SavedWorldAdapter) or not isinstance(bound, BoundArtifactPlan):
        raise ValueError("saved-world adapter and bound optimizer artifact are required")
    root = owned_root(adapter.root)
    if (root / "manifest.json").is_symlink():
        raise ValueError("saved-world manifest may not be a symbolic link")
    verify_instance(root, runtime=adapter.runtime)
    plan, binding = bound.plan, bound.plan.binding
    scope = binding.scope
    if (scope.instance_id != str(root) or scope.manifest_digest != file_hash(root / "manifest.json")
            or scope.surface != adapter.surface or scope.force != adapter.force):
        raise ValueError("consumer observation does not match the bound disposable scope")
    rebound = bind_artifact_to_construction_plan(
        bound.artifact, scope=scope, project_id=binding.project_id, transaction_id=binding.transaction_id,
        throughput_output=plan.throughput_output, throughput_target=plan.throughput_target,
        throughput_unit=plan.throughput_unit, expected_artifact_sha256=bound.artifact_sha256,
        expected_plan_hash=bound.binding_sha256)
    if rebound.plan != plan or rebound.canonical_binding != bound.canonical_binding:
        raise ValueError("optimizer artifact binding is inconsistent")
    report = adapter.bridge.optimizer_consumer_observation(
        surface=scope.surface, force=scope.force, entity=entity, position=position, unit_number=unit_number)
    return {"binding": binding.to_dict(), "artifact_sha256": bound.artifact_sha256, "report": report,
            "ownership_claimed": False, "commissioned": False, "dispatch_authorized": False}
