# Path: core/recipe_dag.py
# Purpose: Deterministically expand a live recipe catalog into an exact-rate symbolic dependency graph.

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
from typing import Mapping

from jsonschema import Draft7Validator

HEADROOM = Decimal("1.25")
_ROOT = Path(__file__).resolve().parents[1]


def _validate(payload: Mapping, schema_name: str) -> None:
    schema = json.loads((_ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
    errors = sorted(Draft7Validator(schema).iter_errors(dict(payload)), key=lambda e: list(e.path))
    if errors:
        raise ValueError("; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors))


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def compile_recipe_dag(goal: Mapping, catalog: Mapping) -> dict:
    """Compile exact rates. Unsupported, cyclic, missing, and ambiguous recipes fail closed."""
    _validate(goal, "goal.schema.json")
    _validate(catalog, "recipe_catalog.schema.json")
    raw_leaves = set(catalog["raw_resources"])
    producers: dict[str, list[dict]] = defaultdict(list)
    for recipe in catalog["recipes"]:
        if recipe["enabled"] and recipe["supported"]:
            for product in recipe["products"]:
                producers[product["name"]].append(recipe)
    preferences = (goal.get("preferences") or {}).get("recipes", {})
    nodes: dict[str, dict] = {}
    raw_rates: dict[str, Decimal] = defaultdict(Decimal)
    visiting: list[str] = []

    def expand(item: str, required_rate: Decimal) -> None:
        # Natural resources are extraction boundaries even if a recipe also produces them.
        if item in raw_leaves:
            raw_rates[item] += required_rate
            return
        candidates = sorted(producers.get(item, []), key=lambda recipe: recipe["name"])
        if not candidates:
            raise ValueError(f"Missing enabled deterministic recipe for {item}")
        selected = candidates[0]
        preference = preferences.get(item)
        if len(candidates) > 1:
            selected = next((r for r in candidates if r["name"] == preference), None)
            if selected is None:
                raise ValueError(f"Ambiguous recipes for {item}: {', '.join(r['name'] for r in candidates)}")
        if len(selected["products"]) != 1:
            raise ValueError(f"Shared coproduct recipe {selected['name']} is unsupported for exact expansion")
        name = selected["name"]
        if name in visiting:
            raise ValueError("Recipe cycle: " + " -> ".join(visiting + [name]))
        output_amount = sum((_decimal(p["amount"]) for p in selected["products"] if p["name"] == item), Decimal(0))
        if output_amount <= 0:
            raise ValueError(f"Recipe {name} has no deterministic amount for {item}")
        crafts = required_rate / output_amount
        craft_seconds = crafts * (_decimal(selected["energy_ticks"]) / Decimal(60))
        entry = nodes.setdefault(name, {"recipe": name, "category": selected["category"], "crafts": Decimal(0), "craft_seconds": Decimal(0), "products": {}, "ingredients": {}})
        entry["crafts"] += crafts
        entry["craft_seconds"] += craft_seconds
        for product in selected["products"]:
            rate = crafts * _decimal(product["amount"])
            entry["products"][product["name"]] = entry["products"].get(product["name"], Decimal(0)) + rate
        visiting.append(name)
        for ingredient in selected["ingredients"]:
            rate = crafts * _decimal(ingredient["amount"])
            entry["ingredients"][ingredient["name"]] = entry["ingredients"].get(ingredient["name"], Decimal(0)) + rate
            expand(ingredient["name"], rate)
        visiting.pop()

    expand(goal["target"], _decimal(goal["delta"]["rate"]))
    recipes = {r["name"]: r for r in catalog["recipes"]}
    dependencies = {name: sorted({p["name"] for i in recipes[name]["ingredients"] for p in producers.get(i["name"], []) if p["name"] in nodes}) for name in nodes}
    ordered: list[str] = []
    temporary: set[str] = set()
    permanent: set[str] = set()
    def visit(name: str) -> None:
        if name in permanent: return
        if name in temporary: raise ValueError(f"Recipe dependency cycle at {name}")
        temporary.add(name)
        for dependency in dependencies[name]: visit(dependency)
        temporary.remove(name); permanent.add(name); ordered.append(name)
    for name in sorted(nodes): visit(name)
    def number(value: Decimal) -> float: return float(value.quantize(Decimal("0.000000001")))
    serialized = []
    for name in ordered:
        node = nodes[name]
        serialized.append({"recipe": name, "category": node["category"], "crafts_per_second": number(node["crafts"]), "craft_seconds_per_second": number(node["craft_seconds"]), "provisioned_craft_seconds": number(node["craft_seconds"] * HEADROOM), "ingredients_per_second": {k: number(v) for k, v in sorted(node["ingredients"].items())}, "products_per_second": {k: number(v) for k, v in sorted(node["products"].items())}})
    return {"version": "1.0.0", "target": goal["target"], "target_rate_per_second": float(goal["delta"]["rate"]), "headroom": float(HEADROOM), "raw_leaves_per_second": {k: number(v) for k, v in sorted(raw_rates.items())}, "topological_order": ordered, "nodes": serialized}
