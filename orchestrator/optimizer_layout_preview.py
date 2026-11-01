# Path: orchestrator/optimizer_layout_preview.py | Purpose: Prepare a denied scoped layout request from a held optimizer bill.
"""Offline request preparation; never dispatches or grants execution authority."""

from __future__ import annotations

import re
from collections import Counter

from core.execution_authorizer import authorize_execution
from core.optimizer_artifact_binding import BoundArtifactPlan
from orchestrator.material_reservations import MaterialReservationError, MaterialReservationLedger
from orchestrator.optimizer_reservations import _verify_bound_ledger
from planners.sandbox_infrastructure import build_layout_authorization


def _evidence_tags(binding, episode_id):
    for name, value in (("episode_id", episode_id), ("project_id", binding.project_id)):
        if not value or len(value.encode("utf-8")) > 128:
            raise ValueError(f"{name} must be at most 128 UTF-8 bytes")
    if not re.fullmatch(r"[0-9a-f]{32}", binding.transaction_id):
        raise ValueError("tagged preview requires a 32-character lowercase hex transaction_id")
    return {"optimizer_episode": episode_id, "optimizer_project": binding.project_id,
            "optimizer_transaction": binding.transaction_id, "optimizer_plan_hash": binding.plan_hash}


def _deny_layout(layout, binding):
    layout.update(surface=binding.scope.surface, force=binding.scope.force, atomic=True)
    count = sum(len(phase["actions"]) for phase in layout["phases"])
    limits = {"surface": binding.scope.surface, "force": binding.scope.force, "max_count": count}
    proposed = build_layout_authorization([layout], scope_limits=limits)
    proposal = {"allowed_actions": proposed["approved_actions"], "blocked_actions": []}
    return authorize_execution(proposal, [], "policy", scope_limits=limits).to_dict()


def prepare_optimizer_layout_preview(bound: BoundArtifactPlan, ledger: MaterialReservationLedger, *,
                                     episode_id: str, include_evidence_tags: bool = False) -> dict:
    """Prepare exact scoped ghost actions and an authorization denying them all.

    Ledger funding, scope and identity are caller assertions and may be stale.
    The optional tagged variant changes each ghost to nonreconciling placement;
    its hash identifies the source artifact, not the transformed layout bytes.
    This creates no permit, dispatch, inventory hold, or runtime observation.
    """
    return _prepare_preview(bound, ledger, episode_id=episode_id, include_evidence_tags=include_evidence_tags,
                            observation_only=False)


def prepare_optimizer_observation_preview(bound: BoundArtifactPlan, ledger: MaterialReservationLedger, *,
                                          episode_id: str) -> dict:
    """Describe a ready or partially held project with all dispatch actions denied."""
    return _prepare_preview(bound, ledger, episode_id=episode_id, include_evidence_tags=False, observation_only=True)


def _prepare_preview(bound, ledger, *, episode_id, include_evidence_tags, observation_only):
    _verify_bound_ledger(bound, ledger, episode_id)
    if type(include_evidence_tags) is not bool:
        raise ValueError("include_evidence_tags must be a boolean")
    plan = bound.plan
    binding = plan.binding
    project = ledger.projects.get(f"optimizer:{binding.project_id}")
    if (project is None or project.construction_binding != binding
            or project.required != dict(plan.required_materials)
            or project.state not in ({"ready", "supply_wait"} if observation_only else {"ready"})
            or (project.state == "ready") != (project.reserved == project.required)
            or not project.hold_until_producing
            or project.target_item != plan.throughput_output):
        raise MaterialReservationError("bound project requires a consistent held reservation" if observation_only
                                       else "bound project requires a fully funded, held ready reservation")
    layout = bound.artifact["plan"]
    if include_evidence_tags:
        tags = _evidence_tags(binding, episode_id)
        for phase in layout["phases"]:
            for action in phase["actions"]:
                # The bound artifact contains only place_ghost actions; the tagged
                # variant avoids modifying existing entities or ghosts on retry.
                action["action_type"] = "place_ghost_no_reconcile"
                action["optimizer_ownership"] = dict(tags)
    denied = _deny_layout(layout, binding)
    funding = {"state": project.state, "reserved": dict(project.reserved),
               "shortfalls": {item: count - project.reserved.get(item, 0) for item, count in project.required.items()
                              if count > project.reserved.get(item, 0)}}
    return {"build_plan": layout, "authorization": denied, "binding": binding.to_dict(), "funding": funding,
            "ledger_episode_id": episode_id, "ledger_revision": ledger.revision,
            "execution_authorized": False,
            "non_claims": ["ledger stock and scope are caller assertions, not authenticated runtime evidence",
                           "all actions are denied; no dispatch or placement is authorized",
                           "atomic preflight does not prove runtime identity or transactional completion",
                           "tags are caller assertions; existing entities and ghosts do not produce new build events"]}


def prepare_optimizer_packet_preview(bound: BoundArtifactPlan, ledger: MaterialReservationLedger, *,
                                     episode_id: str, action_offset: int, max_actions: int) -> dict:
    """Select an ordered, affordable ghost prefix from an asserted partial hold.

    The cursor is caller-supplied, not proof of past submission or completion.
    This neither allocates nor consumes stock; overlapping previews may reuse
    the same hold. No packet may be dispatched based on this result.
    """
    _verify_bound_ledger(bound, ledger, episode_id)
    if type(action_offset) is not int or action_offset < 0:
        raise ValueError("action_offset must be a non-negative integer")
    if type(max_actions) is not int or max_actions <= 0:
        raise ValueError("max_actions must be a positive integer")
    plan, binding = bound.plan, bound.plan.binding
    project = ledger.projects.get(f"optimizer:{binding.project_id}")
    if (project is None or project.construction_binding != binding
            or project.required != dict(plan.required_materials) or not project.hold_until_producing
            or project.target_item != plan.throughput_output or project.state not in {"ready", "supply_wait"}):
        raise MaterialReservationError("bound project requires an active held reservation")
    tags = _evidence_tags(binding, episode_id)
    artifact = bound.artifact
    source = artifact["plan"]["phases"]
    total = sum(len(phase["actions"]) for phase in source)
    if action_offset > total:
        raise ValueError("action_offset exceeds the bound plan")
    item_mapping = artifact["item_mapping"]["entities"]
    used = Counter()
    phases = []
    index = selected = 0
    blocked_item = None
    stop = False
    for phase in source:
        actions = []
        for action in phase["actions"]:
            if index < action_offset:
                index += 1
                continue
            if selected >= max_actions:
                stop = True
                break
            item = item_mapping[action["entity"]]
            if used[item] >= project.reserved.get(item, 0):
                blocked_item = item
                stop = True
                break
            action["action_type"] = "place_ghost_no_reconcile"
            action["optimizer_ownership"] = dict(tags)
            actions.append(action)
            used[item] += 1
            selected += 1
            index += 1
        if actions:
            phases.append({"name": phase["name"], "actions": actions})
        if stop:
            break
    result = {"binding": binding.to_dict(), "ledger_episode_id": episode_id,
              "ledger_revision": ledger.revision, "action_offset": action_offset,
              "next_action_offset": action_offset + selected, "total_actions": total,
              "material_bill": dict(sorted(used.items())), "blocked_item": blocked_item,
              "execution_authorized": False,
              "non_claims": ["stock, scope and cursor are caller assertions, not authenticated observations",
                             "preview does not reserve or consume stock and may overlap other packets",
                             "partial geometry may split paired routes or be unsafe to operate; no dispatch is authorized"]}
    if phases:
        layout = {"phases": phases}
        result["authorization"] = _deny_layout(layout, binding)
        result["build_plan"] = layout
        result["status"] = "packet_preview"
    else:
        result["status"] = "end_of_plan" if action_offset == total else "supply_wait"
        result["authorization"] = None
        result["build_plan"] = None
    return result
