# Path: orchestrator/operation_observation.py | Purpose: Pure stock, reservation and item-service facts shared by operation consumers.
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import math


def item_service_observation(current: Mapping, previous: Mapping | None, item: str) -> dict:
    """Describe measured stock and flows; a stocked plateau never proves sustained service."""
    networks = current.get("networks")
    stock = None
    if isinstance(networks, Mapping) and all(
        isinstance(n, Mapping) and isinstance(n.get("items"), Mapping) for n in networks.values()
    ):
        values = [n["items"].get(item, 0) for n in networks.values()]
        if all(type(v) is int and v >= 0 for v in values):
            stock = sum(values)
    result = {"version": "item-service-observation-v1", "item": item,
              "surface": current.get("surface"), "force": current.get("force"),
              "tick": current.get("tick"), "network_stock_items": stock,
              "production_delta_items": None, "consumption_delta_items": None,
              "production_rate_per_second": None, "consumption_rate_per_second": None,
              "status": "unknown", "sustained_service": None}
    if previous is None:
        return result
    if (not current.get("surface") or not current.get("force") or not current.get("mods")
            or any(current.get(k) != previous.get(k) for k in ("surface", "force", "mods"))):
        return result
    now, before = current.get("tick"), previous.get("tick")
    if type(now) is not int or type(before) is not int or now <= before:
        return result
    result["window_ticks"] = now - before
    for field in ("production", "consumption"):
        current_counts, previous_counts = current.get(field), previous.get(field)
        if not isinstance(current_counts, Mapping) or not isinstance(previous_counts, Mapping):
            return result
        a, b = current_counts.get(item), previous_counts.get(item)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
               for v in (a, b)) or a < b:
            return result
        result[field + "_delta_items"] = a - b
        result[field + "_rate_per_second"] = (a - b) * 60 / (now - before)
    if result["production_delta_items"] > 0:
        result["status"] = "producing"
    elif stock is not None and stock > 0 and result["consumption_delta_items"] == 0:
        result["status"] = "stocked_plateau"
    else:
        result["status"] = "no_output_in_window"
    return result


def economics_observation(economics: Mapping | None, networks: Mapping | None) -> dict:
    """Project independent surveys without rebalancing or spending the material ledger.

    Network stock is the saved-world logistic-network survey, not requester WIP or
    transferable stock. Unreserved counts subtract the ledger's recorded owners;
    they do not establish a claim's affordability or a cell's borrowability.
    """
    observed = {"version": 1, "projects": None, "loans": None, "slots": None, **(economics or {})}
    stock = None
    if isinstance(networks, Mapping) and all(isinstance(n, Mapping) and isinstance(n.get("items"), Mapping)
                                             for n in networks.values()):
        stock = dict(sum((Counter(n["items"]) for n in networks.values()), Counter()))
    projects = observed.get("projects")
    reserved = None
    if projects is not None:
        reserved = dict(sum((Counter(p["reserved"]) for p in projects.values()), Counter()))
    observed["stock"] = {
        "scope": "logistic_networks", "network": stock,
        "reserved": reserved,
        "unreserved": ({i: max(0, n - reserved.get(i, 0)) for i, n in stock.items()}
                       if stock is not None and reserved is not None else None)}
    if observed.get("loans") is not None:
        observed["loans"] = [dict(loan, network_stock=None if stock is None else stock.get(loan["making"], 0),
                                   remaining_to_target=None if stock is None else
                                   max(0, loan["target"] - stock.get(loan["making"], 0)))
                             for loan in observed["loans"]]
    return observed


def assembler_utilization(facts: Mapping, economics: Mapping) -> dict:
    """Instantaneous survey status, not a measured working-time fraction or a reward."""
    from orchestrator.mall_reconciliation import ready_reason

    machines = facts.get("machines")
    loans = economics.get("loans")
    result = {"version": "assembler-utilization-sample-v1", "tick": facts.get("tick"),
              "scope": "survey_assemblers", "basis": "instantaneous_status_sample",
              "assemblers": None, "known_statuses": None, "status_counts": None,
              "working_sample_share": None,
              "ready_loans": None if loans is None else sum(bool(ready_reason(l)) for l in loans)}
    if not isinstance(machines, list):
        return result
    assemblers = [m for m in machines if m.get("name") in
                  {"assembling-machine-1", "assembling-machine-2", "assembling-machine-3"}]
    counts = Counter(m["status"] for m in assemblers if isinstance(m.get("status"), str))
    known = sum(counts.values())
    result.update(assemblers=len(assemblers), known_statuses=known, status_counts=dict(counts),
                  working_sample_share=counts["working"] / known if known else None)
    return result
