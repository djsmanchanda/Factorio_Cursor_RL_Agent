# Path: tests/test_research_path_demand.py | Purpose: Check scoped research dependency and science planning facts.

import hashlib
import json

import pytest

from core.research_path_demand import ResearchPathError, plan_research_science_supply, research_path_demand
from tests.test_catalog_demand import _bytes, _catalog, _provenance, _recipe


def technology(name, deps=(), *, researched=False, count=10, ingredients=None, **extra):
    return {"name": name, "researched": researched, "enabled": True, "level": 1, "max_level": 1,
            "prerequisites": list(deps), "unknowns": [],
            "research": {"unit_count": count, "ingredients": ingredients if ingredients is not None else
                         [{"name": "red", "kind": "item", "amount": 1}]}, **extra}


def catalog():
    return {"schema_version": "1.1.0", "kind": "runtime_catalog", "ok": True, "tick": 12,
            "provenance": {"export_tick": 12, "active_mods": {"base": "2.1.21"}, "active_mods_count": 1,
                           "scope": {"surface": "nauvis", "surface_requested": True, "force": "player",
                                     "technology_scope": "all_force_technologies"}},
            "force_state": {"name": "player", "research_enabled": True},
            "technology_graph": {"version": "runtime-technology-graph-v1", "complete": True,
                                 "technology_count": 4},
            "technologies": [technology("done", researched=True), technology("a", ["done"]),
                             technology("b", ["done"], count=5), technology("goal", ["a", "b"], count=20)]}


def project(document, targets=None, **changes):
    raw = json.dumps(document, separators=(",", ":")).encode()
    args = {"expected_sha256": hashlib.sha256(raw).hexdigest(), "surface": "nauvis", "force": "player",
            "active_mods": {"base": "2.1.21"}, "targets": targets or ["goal"]}
    return research_path_demand(raw, **(args | changes))


def test_shared_prerequisites_billed_once_and_frontier_uses_researched_state():
    data = catalog()
    data["technologies"].append(technology("second", ["a"], count=3))
    data["technology_graph"]["technology_count"] += 1
    result = project(data, ["goal", "second"])
    assert [row["name"] for row in result["technologies"]] == ["a", "b", "goal", "second"]
    assert result["prerequisites_researched_frontier"] == ["a", "b"]
    assert result["known_gross_science_bill"] == {"red": "38"}
    assert result["bill_complete_for_exported_graph"]


def test_unsupported_research_exposes_known_subtotal_without_inventing_costs():
    data = catalog()
    data["technologies"][1]["research"]["trigger"] = {"type": "craft-item", "details": {}}
    data["technologies"][2]["max_level"] = 100
    data["technologies"][3]["unknowns"] = ["research ingredient identity unavailable"]
    result = project(data)
    assert result["known_gross_science_bill"] == {}
    assert not result["bill_complete_for_exported_graph"]
    assert [r["unknowns"] for r in result["technologies"]] == [
        ["trigger_research"], ["level_or_formula_unverified"], ["science_cost_incomplete"]]


def test_exact_fraction_and_disabled_research():
    data = catalog()
    data["technologies"][1]["research"]["ingredients"][0]["amount"] = 0.25
    data["technologies"][1]["enabled"] = False
    data["force_state"]["research_enabled"] = False
    result = project(data)
    assert result["known_gross_science_bill"] == {"red": "55/2"}
    assert result["prerequisites_researched_frontier"] == ["b"]
    assert result["research_enabled"] is False


@pytest.mark.parametrize("corruption", ["missing", "cycle", "duplicate", "scope", "mods", "ingredient"])
def test_malformed_catalog_fails_closed(corruption):
    data = catalog()
    if corruption == "missing":
        data["technologies"][3]["prerequisites"].append("absent")
    elif corruption == "cycle":
        data["technologies"][1]["prerequisites"].append("goal")
    elif corruption == "duplicate":
        data["technologies"].append(data["technologies"][0])
    elif corruption == "scope":
        data["provenance"]["scope"]["surface_requested"] = False
    elif corruption == "mods":
        data["provenance"]["active_mods_count"] = 2
    else:
        data["technologies"][1]["research"]["ingredients"][0]["amount"] = 0
    with pytest.raises(ResearchPathError):
        project(data)


def test_digest_and_requested_identity_are_checked():
    with pytest.raises(ResearchPathError):
        project(catalog(), expected_sha256="0" * 64)
    with pytest.raises(ResearchPathError):
        project(catalog(), ["goal", "goal"])
    with pytest.raises(ResearchPathError):
        project(catalog(), ["goal"], force="enemy")


def test_research_bill_flows_into_shared_recipe_balance_with_exact_scope():
    runtime = json.dumps(catalog()).encode()
    recipes = _catalog(_recipe("red", [("ore", 2)], [("red", 1)]), raw=("ore",), tick=12)
    data = _bytes(recipes)
    provenance = _provenance(data, recipes, mods=[{"name": "base", "version": "2.1.21"}], surface="nauvis")
    args = dict(runtime_sha256=hashlib.sha256(runtime).hexdigest(), recipe_provenance=provenance,
                surface="nauvis", force="player", active_mods={"base": "2.1.21"}, targets=["goal"],
                horizon_ticks=2100, boundaries=[{"name": "ore", "cap": 3}])
    path, balance = plan_research_science_supply(runtime, data, **args)
    assert path["known_gross_science_bill"] == {"red": "35"}
    assert dict(balance.demand)["red"] == 1
    assert balance.balance.feasible
    assert dict(zip(balance.balance.source_names, balance.balance.source_rates))["ore"] == 2
    provenance["export_tick"] += 1
    with pytest.raises(ResearchPathError, match="tick mismatch"):
        plan_research_science_supply(runtime, data, **args)


def test_incomplete_research_cost_cannot_become_full_supply_plan():
    runtime_doc = catalog()
    runtime_doc["technologies"][1]["research"]["trigger"] = {"type": "craft-item"}
    raw = json.dumps(runtime_doc).encode()
    with pytest.raises(ResearchPathError, match="unknown or empty"):
        plan_research_science_supply(raw, b"{}", runtime_sha256=hashlib.sha256(raw).hexdigest(),
                                     recipe_provenance={}, surface="nauvis", force="player",
                                     active_mods={"base": "2.1.21"}, targets=["goal"], horizon_ticks=100)


@pytest.mark.parametrize("graph", [None, {"version": "runtime-technology-graph-v1", "complete": False,
                                         "technology_count": 4},
                                  {"version": "runtime-technology-graph-v1", "complete": True,
                                   "technology_count": 3}])
def test_missing_or_incomplete_graph_remains_diagnostic_only(graph):
    document = catalog()
    if graph is None:
        del document["technology_graph"]
    else:
        document["technology_graph"] = graph
    assert project(document)["graph_read_complete"] is False
    raw = json.dumps(document).encode()
    with pytest.raises(ResearchPathError, match="graph completeness"):
        plan_research_science_supply(raw, b"{}", runtime_sha256=hashlib.sha256(raw).hexdigest(),
                                     recipe_provenance={}, surface="nauvis", force="player",
                                     active_mods={"base": "2.1.21"}, targets=["goal"], horizon_ticks=100)
