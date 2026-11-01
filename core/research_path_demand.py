# Path: core/research_path_demand.py | Purpose: Derive technology prerequisites and gross science demand from a pinned runtime catalog.
"""Read-only planning facts; neither research selection nor runtime identity is proved."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import re


class ResearchPathError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ResearchPathError("duplicate catalog field")
        result[key] = value
    return result


def research_path_demand(catalog_bytes: bytes, *, expected_sha256: str, surface: str, force: str,
                         active_mods: Mapping[str, str], targets: list[str]) -> dict:
    """Describe the unresearched prerequisite graph for explicit technology names.

    Bills are full unmodified research costs, not remaining consumption. A
    partial current research, lab productivity, inventories, trigger work and
    repeatable technologies are not inferred. The catalog's graph completeness
    and its origin are caller assertions, even when the digest matches.
    """
    if (not isinstance(catalog_bytes, bytes) or len(catalog_bytes) > 32 * 1024 * 1024
            or not isinstance(expected_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or hashlib.sha256(catalog_bytes).hexdigest() != expected_sha256):
        raise ResearchPathError("pinned catalog digest mismatch")
    if (not all(isinstance(v, str) and v for v in (surface, force))
            or not isinstance(active_mods, Mapping) or not active_mods
            or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                   for k, v in active_mods.items())
            or not isinstance(targets, list) or not targets
            or any(not isinstance(t, str) or not t for t in targets)
            or len(set(targets)) != len(targets)):
        raise ResearchPathError("explicit scope, mod set and unique targets are required")
    try:
        report = json.loads(catalog_bytes, object_pairs_hook=_object, parse_float=Decimal,
                            parse_constant=lambda _: (_ for _ in ()).throw(ResearchPathError("non-finite JSON")))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ResearchPathError("invalid catalog JSON") from error
    if not isinstance(report, dict):
        raise ResearchPathError("invalid runtime catalog")
    provenance = report.get("provenance")
    scope = provenance.get("scope") if isinstance(provenance, dict) else None
    state = report.get("force_state")
    tick = report.get("tick")
    if (report.get("schema_version") != "1.1.0" or report.get("kind") != "runtime_catalog"
            or report.get("ok") is not True or type(tick) is not int or tick < 0
            or not isinstance(scope, dict) or scope.get("technology_scope") != "all_force_technologies"
            or scope.get("surface_requested") is not True or scope.get("surface") != surface
            or scope.get("force") != force or provenance.get("export_tick") != tick
            or provenance.get("active_mods") != dict(active_mods)
            or type(provenance.get("active_mods_count")) is not int
            or provenance["active_mods_count"] != len(active_mods)
            or not isinstance(state, dict) or state.get("name") != force
            or type(state.get("research_enabled")) is not bool):
        raise ResearchPathError("catalog scope or provenance mismatch")
    raw = report.get("technologies")
    if not isinstance(raw, list) or not raw or len(raw) > 10000:
        raise ResearchPathError("technology list unavailable or too large")
    graph = report.get("technology_graph")
    graph_read_complete = (isinstance(graph, dict)
                           and set(graph) == {"version", "complete", "technology_count"}
                           and graph.get("version") == "runtime-technology-graph-v1"
                           and graph.get("complete") is True
                           and type(graph.get("technology_count")) is int
                           and graph["technology_count"] == len(raw))
    techs = {}
    for tech in raw:
        if (not isinstance(tech, dict) or not isinstance(tech.get("name"), str) or not tech["name"]
                or tech["name"] in techs or type(tech.get("researched")) is not bool
                or type(tech.get("enabled")) is not bool):
            raise ResearchPathError("invalid or duplicate technology")
        techs[tech["name"]] = tech

    # Iterative DFS prevents deep modded technology trees from exhausting Python's stack.
    color, ordered = {}, []
    for target in targets:
        stack = [(target, False)]
        while stack:
            name, exiting = stack.pop()
            if exiting:
                color[name] = 2
                ordered.append(name)
                continue
            if color.get(name) == 2:
                continue
            if color.get(name) == 1:
                raise ResearchPathError("technology prerequisite cycle")
            tech = techs.get(name)
            if tech is None:
                raise ResearchPathError(f"missing technology: {name}")
            # A researched prerequisite need not be traversed or billed again.
            deps = [] if tech["researched"] else tech.get("prerequisites")
            if (not isinstance(deps, list) or any(not isinstance(d, str) or not d for d in deps)
                    or len(set(deps)) != len(deps)):
                raise ResearchPathError(f"invalid prerequisites: {name}")
            color[name] = 1
            stack.append((name, True))
            stack.extend((dep, False) for dep in reversed(sorted(deps)))

    gross = defaultdict(Fraction)
    nodes, frontier = [], []
    for name in ordered:
        tech = techs[name]
        if tech["researched"]:
            continue
        deps = tech["prerequisites"]
        research = tech.get("research")
        unknowns = tech.get("unknowns")
        if not isinstance(research, dict) or not isinstance(unknowns, list) or any(not isinstance(x, str) for x in unknowns):
            raise ResearchPathError(f"invalid research facts: {name}")
        reasons = []
        if research.get("trigger") is not None:
            reasons.append("trigger_research")
        if tech.get("max_level") != 1 or tech.get("level") != 1 or research.get("unit_count_formula"):
            reasons.append("level_or_formula_unverified")
        count = research.get("unit_count")
        ingredients = research.get("ingredients")
        if type(count) is not int or count <= 0 or not isinstance(ingredients, list) or not ingredients:
            reasons.append("science_cost_unavailable")
        if any("research ingredient" in x or "technology prototype unavailable" in x for x in unknowns):
            reasons.append("science_cost_incomplete")
        cost = {}
        if isinstance(ingredients, list):
            for item in ingredients:
                if (not isinstance(item, dict) or item.get("kind") != "item"
                        or not isinstance(item.get("name"), str) or not item["name"]
                        or item["name"] in cost or type(item.get("amount")) not in (int, Decimal)):
                    raise ResearchPathError(f"invalid research ingredient: {name}")
                try:
                    amount = Fraction(str(item["amount"]))
                except (ValueError, ZeroDivisionError) as error:
                    raise ResearchPathError(f"invalid research ingredient: {name}") from error
                if amount <= 0:
                    raise ResearchPathError(f"invalid research ingredient: {name}")
                cost[item["name"]] = amount * count if type(count) is int and count > 0 else Fraction(0)
        if not reasons:
            for item, amount in cost.items():
                gross[item] += amount
        ready = tech["enabled"] and all(techs[dep]["researched"] for dep in deps)
        if ready:
            frontier.append(name)
        nodes.append({"name": name, "prerequisites": list(deps), "enabled": tech["enabled"],
                      "prerequisites_researched": all(techs[dep]["researched"] for dep in deps),
                      "science_bill": {k: str(v) for k, v in sorted(cost.items())} if not reasons else None,
                      "unknowns": reasons})
    return {"version": "research-path-demand-v1", "catalog_sha256": expected_sha256, "tick": tick,
            "surface": surface, "force": force, "targets": list(targets), "research_enabled": state["research_enabled"],
            "graph_read_complete": graph_read_complete,
            "technologies": nodes, "prerequisites_researched_frontier": sorted(frontier),
            "known_gross_science_bill": {k: str(v) for k, v in sorted(gross.items())},
            "bill_complete_for_exported_graph": all(not n["unknowns"] for n in nodes),
            "non_claims": ["The catalog and its prerequisite completeness are caller assertions, not authenticated runtime evidence.",
                           "Gross science omits current progress, productivity, inventories and all unknown costs.",
                           "Frontier facts do not establish live selection legality or authorize research."]}


def plan_research_science_supply(runtime_bytes: bytes, recipe_bytes: bytes, *, runtime_sha256: str,
                                 recipe_provenance: Mapping, surface: str, force: str,
                                 active_mods: Mapping[str, str], targets: list[str], horizon_ticks: int,
                                 boundaries=(), preferences=None) -> tuple[dict, object]:
    """Turn a fully costed exported path into explicit average science demand.

    The two captures must declare identical scope, mod set and tick. A result
    is a gross planning scenario, not a reservation, schedule, or selection.
    """
    from core.catalog_demand import plan_catalog_demand

    if type(horizon_ticks) is not int or horizon_ticks <= 0:
        raise ResearchPathError("a positive game-tick horizon is required")
    path = research_path_demand(runtime_bytes, expected_sha256=runtime_sha256, surface=surface,
                                force=force, active_mods=active_mods, targets=targets)
    if not path["graph_read_complete"]:
        raise ResearchPathError("technology graph completeness was not established by the exporter")
    if not path["bill_complete_for_exported_graph"] or not path["known_gross_science_bill"]:
        raise ResearchPathError("research path has unknown or empty science demand")
    if not isinstance(recipe_provenance, Mapping):
        raise ResearchPathError("recipe provenance is required")
    mods = recipe_provenance.get("mods")
    if (not isinstance(mods, (list, tuple)) or len(mods) != len(active_mods)
            or any(not isinstance(m, Mapping) or set(m) != {"name", "version"}
                   or not isinstance(m["name"], str) or not isinstance(m["version"], str)
                   or m["name"] not in active_mods or active_mods[m["name"]] != m["version"] for m in mods)
            or len({m["name"] for m in mods}) != len(mods)
            or recipe_provenance.get("surface") != surface or recipe_provenance.get("force") != force
            or type(recipe_provenance.get("export_tick")) is not int
            or recipe_provenance["export_tick"] != path["tick"]):
        raise ResearchPathError("recipe and technology catalog scope, mod set or tick mismatch")
    rates = {item: Fraction(amount) * 60 / horizon_ticks
             for item, amount in path["known_gross_science_bill"].items()}
    demand = plan_catalog_demand(recipe_bytes, provenance=recipe_provenance, demand=rates,
                                 boundaries=boundaries, preferences=preferences)
    return path, demand
