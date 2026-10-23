# Path: orchestrator/mall_reconciliation.py | Purpose: Expose measured, guarded advancement of an existing finite mall loan.
from __future__ import annotations


def identity(loan):
    return {"group": loan.live_group, "requester_position": list(loan.requester_position),
            "side": loan.side, "recipe": loan.current_recipe, "item": loan.target_item}


def ready_reason(fact):
    """A finished prerequisite is evidence of work to reconcile, not final delivery."""
    if fact.get("releasing") is True:
        return "release_pending"
    required, completed = fact.get("step_required_crafts"), fact.get("step_completed_crafts")
    if (fact.get("step_recipe") and fact["step_recipe"] != fact.get("making")
            and fact.get("current_recipe") == fact["step_recipe"]
            and type(required) is int and required > 0
            and type(completed) is int and completed >= required):
        return "prerequisite_finished"
    return None


def observe_loans(client, surface, force):
    from orchestrator import autonomous_builder as ab
    from orchestrator.mall_bootstrap import active_bootstrap_loans

    active = active_bootstrap_loans(client, surface, force)
    try:
        statuses, counters = ab.live_base.machine_health(client, surface,
                                                         [loan.machine_position for loan in active])
    except Exception:
        statuses, counters = {}, {}
    observed = []
    for loan in active:
        counter = counters.get(loan.machine_position)
        crafts = (int(counter // 1000) - loan.step_baseline_finished
                  if counter is not None and loan.step_baseline_finished is not None
                  and loan.current_recipe == loan.step_recipe else None)
        fact = {"lender": loan.original_recipe, "making": loan.target_item, "target": loan.target_count,
                "owner": ab._material_project_id(loan.target_item),
                "requester_position": list(loan.requester_position), "side": loan.side,
                "current_recipe": loan.current_recipe, "production_target": loan.production_target,
                "step_recipe": loan.step_recipe, "step_target": loan.step_target_count,
                "step_required_crafts": loan.step_required_crafts,
                "step_completed_crafts": crafts if crafts is not None and crafts >= 0 else None,
                "machine_status": statuses.get(loan.machine_position),
                "completed_step_targets": dict(loan.completed_step_targets),
                "identity": identity(loan), "releasing": loan.releasing}
        fact["ready_reason"] = ready_reason(fact)
        observed.append(fact)
    return observed


def options(economics):
    result = []
    for fact in (economics or {}).get("loans") or []:
        reason = ready_reason(fact)
        selected = fact.get("identity")
        if not reason or not selected:
            continue
        x, y = selected["requester_position"]
        result.append({"option_id": f"op.reconcile_mall_loan.{x}.{y}.{selected['side']}",
                       "family": "reconcile_mall_loan", "product": fact["making"],
                       "need_id": f"stock:{fact['making']}",
                       "summary": f"reconcile {fact['making']} loan: {reason}",
                       "params": {"item": fact["making"], "expected": selected},
                       "features": {"ready_loan": True, "ready_reason": reason,
                                    "idle_ready_loan": fact.get("machine_status") in
                                    {"disabled_by_control_behavior", "output_full"}},
                       "bill": {}})
    return result


def reconcile(session, *, item, expected):
    """Re-observe one selected identity, then use the shared loan transaction unchanged."""
    from orchestrator import autonomous_builder as ab
    from orchestrator.mall_bootstrap import active_bootstrap_loans

    surface, force = "nauvis", "player"
    evidence = {"attempted": False, "configuration_changed": False}
    try:
        facts = observe_loans(session.client, surface, force)
        fact = next((f for f in facts if f["making"] == item and f["identity"] == expected), None)
        if fact is None or not ready_reason(fact):
            return {"ok": False, "code": "loan_reconciliation_changed", "state": "planned", **evidence}
        loan = next((loan for loan in active_bootstrap_loans(session.client, surface, force)
                     if identity(loan) == expected), None)
        if loan is None:
            return {"ok": False, "code": "loan_reconciliation_changed", "state": "planned", **evidence}
    except Exception as error:
        return {"ok": False, "code": "loan_reconciliation_unknown", "state": "planned",
                **evidence, "error": str(error)[:200]}
    # Existing funding, chemical handoffs, WIP restoration, guarded executor and
    # anchor protections remain authoritative. A readback proves configuration,
    # never manufactured or delivered items.
    evidence["attempted"] = True
    evidence["configuration_changed"] = None
    from orchestrator.work_state import WorkStateSignal
    try:
        result = ab._submit_bootstrap_loan(session.client, session.bridge, surface, force, loan, session.emit,
                                         reference_point=session.reference_point)
    except WorkStateSignal as signal:
        return {"ok": False, "code": signal.code, "classification": signal.classification,
                "state": signal.state, "details": signal.details, "message": str(signal)[:500], **evidence}
    try:
        after = active_bootstrap_loans(session.client, surface, force)
        same_cell = [l for l in after if l.requester_position == loan.requester_position and l.side == loan.side]
        changed = not any(identity(l) == expected for l in same_cell)
        evidence.update(configuration_changed=changed, observed=[identity(l) for l in same_cell])
    except Exception as error:
        return {"ok": False, "code": "loan_reconciliation_unverified", "state": "unverified",
                **evidence, "error": str(error)[:200]}
    return {"ok": changed, "code": "loan_reconciled" if changed else "loan_reconciliation_pending",
            "state": "verified" if changed else "supply_wait", **evidence, "result": str(result)[:200]}
