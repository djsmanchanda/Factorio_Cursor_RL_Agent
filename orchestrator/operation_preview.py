# Path: orchestrator/operation_preview.py | Purpose: Describe observed operation obligations without changing the world.
from __future__ import annotations

import copy
import math
from typing import Mapping, TypedDict


class OperationPreview(TypedDict):
    version: int
    requested_postcondition: dict
    immediate_obligation: dict | None
    step_proposal: dict | None
    ingredient_bill: dict | None
    reservation_owner: str | None
    active_loan: dict | None
    displaced_production: str | None
    dependency_edges: list[dict]
    cycle: str
    unknown: dict[str, str]


def observe_step_proposal(client, surface, force, loan):
    """Sample the inputs used by execution, then run its pure step calculation.

    The observation window is explicit: this is a proposal before admission,
    never an allocation or a promise that the live state will remain unchanged.
    """
    from orchestrator import autonomous_builder as ab
    from planners.recipe_data import LINE_RECIPES

    ledger = ab._MATERIAL_RESERVATION_LEDGER
    revision = None if ledger is None else ledger.revision
    before = ab.live_base.game_tick(client)
    actual = ab.live_base.available_items(client, surface, force)
    usable = (ledger.preview_allocatable_stock(actual, claimant=ab._material_project_id(loan.target_item))
              if ledger is not None else dict(actual))
    spendable = ab.live_base.transferable_items(client, surface, force)
    products_finished = (ab._bootstrap_loan_products_finished(client, surface, loan)
                         if loan.step_recipe is not None else None)
    if any(not isinstance(stock, Mapping) or any(type(n) is not int or n < 0 for n in stock.values())
           for stock in (actual, usable, spendable)):
        raise ValueError('loan stock observation unavailable')
    if loan.step_recipe is not None and (type(products_finished) is not int
            or loan.step_baseline_finished is None or products_finished < loan.step_baseline_finished):
        raise ValueError('loan progress observation unavailable')
    actual = {**actual, loan.target_item: spendable.get(loan.target_item, 0)}
    _, minimum, completed, _, step = ab._bootstrap_loan_step_decision(loan, actual, usable, products_finished)
    after = ab.live_base.game_tick(client)
    if (type(before) is not int or type(after) is not int or after < before
            or ledger is not ab._MATERIAL_RESERVATION_LEDGER
            or (ledger is not None and ledger.revision != revision)):
        raise ValueError('loan planning observation changed')
    if loan.releasing or step is None:
        return None  # Step exhaustion is not proof that release is safe.
    spec = LINE_RECIPES[step.recipe]
    bill = {item: math.ceil(float(amount) * step.crafts)
            for item, amount in zip(spec['ingredients'], spec['amounts'], strict=True)}
    return {'identity': {'group': loan.live_group, 'requester_position': list(loan.requester_position),
                         'side': loan.side, 'recipe': loan.current_recipe, 'item': loan.target_item},
            'observation_window': {'start_tick': before, 'end_tick': after, 'ledger_revision': revision},
            'step': {'recipe': step.recipe, 'target_count': step.target_count, 'crafts': step.crafts},
            'ingredient_bill': {'kind': 'gross_step_recipe_inputs', 'items': bill,
                                'includes_request_headroom': False, 'allocates_material': False},
            'minimum_fulfilled': minimum, 'completed_prerequisite': completed}


def preview_operation(family: str, params: Mapping, economics: Mapping | None) -> OperationPreview | None:
    """Project only observed loan facts; observations may be stale by execution time."""
    if family not in {"produce", "production_service"} or not params.get("item"):
        return None
    item = params["item"]
    result: OperationPreview = {
        "version": 1, "requested_postcondition": {"item": item, "stock_target": params.get("stock_target", 1 if family == "produce" else 10)},
        "immediate_obligation": None, "step_proposal": None, "ingredient_bill": None,
        "reservation_owner": None, "active_loan": None,
        "displaced_production": None, "dependency_edges": [], "cycle": "unknown",
        "unknown": {"immediate_obligation": "operation may choose a prerequisite or another path",
                    "bill": "current operation bill is not observed", "reservation_owner": "operation owner is not established",
                    "displaced_production": "lender selection is not predicted", "release": "ownership and progress guards have not been evaluated",
                    "dependency_edges": "unobserved dependencies and independent suppliers are unknown",
                    "freshness": "observation can change before execution"},
    }
    loans = (economics or {}).get("loans")
    if loans is None:
        result["unknown"]["active_loan"] = "loan observation unavailable"
        return result
    matches = [loan for loan in loans if loan.get("making") == item]
    if len(matches) != 1:
        if len(matches) > 1:
            result["unknown"]["active_loan"] = "multiple matching loans"
        return result
    loan = matches[0]
    required, completed = loan.get("step_required_crafts"), loan.get("step_completed_crafts")
    remaining = max(0, required - completed) if type(required) is int and required > 0 and type(completed) is int and completed >= 0 else None
    result["active_loan"] = {key: copy.deepcopy(loan.get(key)) for key in ("identity", "making", "target", "step_recipe", "step_target", "step_required_crafts", "step_completed_crafts", "machine_status", "releasing")}
    result["active_loan"]["remaining_crafts"] = remaining
    owner = loan.get('owner')
    projects = (economics or {}).get('projects')
    project = projects.get(owner) if isinstance(projects, Mapping) else None
    if isinstance(project, Mapping) and project.get('active') is True:
        result["reservation_owner"] = owner
    result["displaced_production"] = loan.get("lender")
    if result["reservation_owner"] is not None:
        result["unknown"].pop("reservation_owner")
    if result["displaced_production"] is not None:
        result["unknown"].pop("displaced_production")
    if loan.get("step_recipe") and loan["step_recipe"] != item:
        result["dependency_edges"].append({"from": item, "to": loan["step_recipe"], "evidence": "observed loan step"})
    proposal = loan.get('step_proposal')
    if (isinstance(proposal, Mapping) and isinstance(proposal.get('identity'), Mapping)
            and proposal['identity'] == loan.get('identity')
            and isinstance(proposal.get('step'), Mapping)
            and isinstance(proposal.get('ingredient_bill'), Mapping)):
        result['step_proposal'] = {**copy.deepcopy(proposal), 'stage': 'before_admission',
                                   'conditions': ['unchanged loan and stock', 'live admission, preemption and release guards']}
        result['ingredient_bill'] = copy.deepcopy(proposal['ingredient_bill'])
        result['unknown']['bill'] = 'construction cost, local inputs and additional reservation are not established by gross recipe demand'
    return result
