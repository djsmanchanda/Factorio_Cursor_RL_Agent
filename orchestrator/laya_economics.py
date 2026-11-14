# Path: orchestrator/laya_economics.py | Purpose: Project session economics and stock observations.
from collections import Counter
from collections.abc import Mapping


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

