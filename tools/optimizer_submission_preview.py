# Path: tools/optimizer_submission_preview.py | Purpose: Preview a bound optimizer's tagged ghosts and deferred settings.
"""Prepare a saved-world construction request without dispatch or runtime claims."""

from orchestrator.optimizer_layout_preview import prepare_optimizer_layout_preview
from planners.plan_validation import actions, validate_build_plan
from training.laya_saved_world import (CONFIGURATION_FIELDS, SAVED_WORLD_GHOST_FIELDS,
                                       configuration_actions)


def prepare_saved_world_optimizer_submission(bound, ledger, *, episode_id, compact_tags=False):
    """Split a fully held, tagged bound plan into ghost and configuration stages.

    This is an offline preview: its authorization denies every action. Compact
    tags require the correlated shared-tag protocol in the deployed executor.
    Tagged ghosts make later correlation possible but do not establish robot building,
    ownership, runtime identity, fluid safety, or permission to dispatch.
    """
    if type(compact_tags) is not bool:
        raise ValueError("compact_tags must be a boolean")
    preview = prepare_optimizer_layout_preview(bound, ledger, episode_id=episode_id,
                                               include_evidence_tags=True)
    plan = preview["build_plan"]
    original = bound.artifact["plan"]
    if any(action.get("action_type") != "place_ghost" or set(action) - SAVED_WORLD_GHOST_FIELDS
           for action in actions(original)):
        raise ValueError("optimizer plan uses settings unsupported by the saved-world executor")
    configurations = configuration_actions(original)
    shared_tags = None
    for action in actions(plan):
        for key in CONFIGURATION_FIELDS:
            action.pop(key, None)
        if compact_tags:
            tags = action.pop("optimizer_ownership", None)
            if not isinstance(tags, dict):
                raise ValueError("optimizer ghost lacks ownership tags")
            if shared_tags is None:
                shared_tags = tags
            elif tags != shared_tags:
                raise ValueError("optimizer ghost tags differ")
    if compact_tags:
        if shared_tags is None:
            raise ValueError("compact optimizer submission requires ghosts")
        plan["optimizer_ownership"] = shared_tags
    validate_build_plan(plan)
    if configurations:
        configured = {"surface": plan["surface"], "force": plan["force"], "atomic": True,
                      "phases": [{"name": "configure-observed-build", "actions": configurations}]}
        validate_build_plan(configured)
    else:
        configured = None
    return {**preview, "configuration_plan": configured,
            "non_claims": preview["non_claims"] + [
                "configuration requires separately correlated robot-build evidence before application",
                "complete tagging does not ensure that the runtime reports every entity or unit identity",
                "fluid purity, attachment, stock freshness, runtime identity and dispatch remain unverified"]}
