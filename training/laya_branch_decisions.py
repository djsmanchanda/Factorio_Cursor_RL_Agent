# Path: training/laya_branch_decisions.py | Purpose: Present measured disposable work branches as bounded Laya goal choices.

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from orchestrator.laya_operations import sha256_hash
from orchestrator.laya_state import validate_state
from training.laya_work_graph import WorkGraph, project_work_graph


class BranchDecisionError(ValueError):
    """The branch choice cannot be presented or reconciled safely."""


_BRANCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,112}$")


def _evidence(graph: WorkGraph, supply: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if supply.get("version") != "1.0.0" or not isinstance(supply.get("items"), Mapping):
        raise BranchDecisionError("supply evidence must be a versioned item projection")
    projection = project_work_graph(graph)
    return projection, sha256_hash({"work_graph": projection, "supply": dict(supply)})


def compact_workflow_digest(projection: Mapping[str, Any], supply: Mapping[str, Any]) -> dict[str, Any]:
    """The bounded branch and per-item supply view the model reads."""
    items = supply["items"]
    compact_items = {}
    for item in sorted(items):
        facts = items[item]
        compact_items[item] = {
            "stock": facts["stock"]["accessible"],
            "committed": facts["demand"]["committed"]["remaining"],
            "future_ingredients": facts["demand"].get("recipe_projected"),
            "draw_rate": facts["demand"]["current_rate"],
            "output_rate": facts["production"]["observed_rate"],
            "capacity_rate": facts["production"]["capacity_rate"],
        }
    compact_branches = {
        "ready": [branch["branch_id"] for branch in projection["runnable"]],
        "blocked": {
            branch["branch_id"]: {
                "needs": branch["unmet_prerequisites"],
                "why": None if branch["blocker"] is None else branch["blocker"]["reason"],
            }
            for branch in projection["blocked"]
        },
        "closed": [branch["branch_id"] for branch in projection["closed"]],
    }
    return {"work_branches": compact_branches, "item_supply": compact_items}


def prepare_serve_goal(
    state: Mapping[str, Any], graph: WorkGraph, supply: Mapping[str, Any],
    *, max_options: int = 12, max_items: int = 8,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], str]:
    """Expose runnable peers without sorting by value or granting execution authority.

    The complete evidence stays outside the model request under its hash. The
    request receives only a bounded projection; overflow is explicit rather
    than silently hiding a branch or item. This seam is disposable-only until
    a compatible director checkpoint and operation handlers are evaluated.
    """
    if (state.get("run") or {}).get("runtime") != "disposable_training":
        raise BranchDecisionError("branch preview requires a disposable training runtime")
    if not 1 <= max_options <= 20 or max_items < 1:
        raise BranchDecisionError("invalid branch projection bounds")
    projection, evidence_hash = _evidence(graph, supply)
    runnable = projection["runnable"]
    items = supply["items"]
    if not runnable:
        raise BranchDecisionError("no runnable branch; observe, wait, or close the goal")
    if len(runnable) > max_options or len(items) > max_items:
        raise BranchDecisionError("work projection exceeds the bounded model request")

    run = state["run"]
    for item, facts in items.items():
        source = facts.get("provenance")
        if isinstance(source, Mapping) and (
            ("surface" in source and source["surface"] != run["surface"])
            or ("force" in source and source["force"] != run["force"])
        ):
            raise BranchDecisionError(f"supply evidence for {item} belongs to another training scope")
    options: list[dict[str, Any]] = []
    for branch in runnable:
        branch_id = branch["branch_id"]
        if not _BRANCH_ID.fullmatch(branch_id):
            raise BranchDecisionError(f"invalid branch ID for model choice: {branch_id!r}")
        target = branch["completion"]
        option_id = f"serve.{branch_id}"
        options.append({
            "option_id": option_id,
            "operation_family": "observe",
            "tool_id": "training.serve_goal_preview",
            "signature": sha256_hash({"evidence": evidence_hash, "branch_id": branch_id}),
            "plan_hash": None,
            "summary": f"{branch_id}: {target['metric']} >= {target['minimum']:g}",
            "features": {
                "target_minimum": float(target["minimum"]),
                "required_sustained_ticks": float(target["required_sustained_ticks"]),
                "reserved_item_types": float(len(branch["reserved_materials"])),
            },
            "scope": {"surface": run["surface"], "force": run["force"], "bounds": None},
            "risk_tier": "read_only",
        })

    prepared = copy.deepcopy(dict(state))
    digest = dict(prepared.get("observation_digest") or {})
    digest.update(compact_workflow_digest(projection, supply))
    prepared["observation_digest"] = digest
    prepared["options"] = options
    prepared["provenance"]["renderer_version"] = "branch-goal-preview-v1"
    prepared["provenance"]["state_snapshot_hash"] = sha256_hash({
        "prior_state": state, "evidence_hash": evidence_hash,
    })
    validate_state(prepared)
    return prepared, tuple(options), evidence_hash


def resolve_serve_goal(
    graph: WorkGraph, supply: Mapping[str, Any], *,
    evidence_hash: str, option_id: str,
) -> str:
    """Resolve only a freshly presented runnable branch; perform no action."""
    projection, fresh_hash = _evidence(graph, supply)
    if fresh_hash != evidence_hash:
        raise BranchDecisionError("branch or supply evidence changed before selection")
    eligible = {f"serve.{branch['branch_id']}": branch["branch_id"] for branch in projection["runnable"]}
    if option_id not in eligible:
        raise BranchDecisionError("selected branch is not runnable in the current projection")
    return eligible[option_id]


__all__ = ["BranchDecisionError", "prepare_serve_goal", "resolve_serve_goal"]
