# Path: orchestrator/optimizer_reservations.py | Purpose: Bind optimizer construction bills to held ledger reservations.
"""Caller-scoped accounting only; no runtime verification or execution authority."""

from __future__ import annotations

import json
from collections.abc import Mapping

from core.optimizer_artifact_binding import BoundArtifactPlan, bind_artifact_to_construction_plan
from orchestrator.material_reservations import MaterialProject, MaterialReservationError, MaterialReservationLedger


def _verify_bound_ledger(bound: BoundArtifactPlan, ledger: MaterialReservationLedger, episode_id: str) -> None:
    if not isinstance(bound, BoundArtifactPlan) or not isinstance(ledger, MaterialReservationLedger):
        raise MaterialReservationError("bound artifact and material ledger are required")
    if not isinstance(episode_id, str) or not episode_id or ledger.episode_id != episode_id:
        raise MaterialReservationError("explicit episode does not match ledger")
    plan = bound.plan
    binding = plan.binding
    if binding.scope.surface != ledger.surface or binding.scope.force != ledger.force:
        raise MaterialReservationError("construction binding scope does not match ledger")
    try:
        artifact = json.loads(bound.canonical_artifact)
        verified = bind_artifact_to_construction_plan(
            artifact, scope=binding.scope, project_id=binding.project_id, transaction_id=binding.transaction_id,
            throughput_output=plan.throughput_output, throughput_target=plan.throughput_target,
            throughput_unit=plan.throughput_unit, expected_artifact_sha256=bound.artifact_sha256,
            expected_plan_hash=bound.binding_sha256,
        )
    except (ValueError, TypeError, UnicodeDecodeError) as error:
        raise MaterialReservationError("bound artifact cannot be independently revalidated") from error
    if verified != bound:
        raise MaterialReservationError("bound artifact or construction plan was altered")


def reserve_optimizer_artifact(
    bound: BoundArtifactPlan, ledger: MaterialReservationLedger, *, episode_id: str,
    stock: Mapping[str, int], priority: int = 50,
) -> MaterialProject:
    """Hold the exact bound bill in a caller-selected single-writer ledger.

    Scope and stock are assertions. No inventory, runtime, authorization, bot,
    placement or throughput is observed, and this function never dispatches.
    The ledger must not be concurrently used by another writer.
    """
    _verify_bound_ledger(bound, ledger, episode_id)
    plan = bound.plan
    binding = plan.binding
    return ledger.declare(
        f"optimizer:{binding.project_id}", dict(plan.required_materials), stock,
        target_item=plan.throughput_output, priority=priority, hold_until_producing=True,
        construction_binding=binding,
    )
