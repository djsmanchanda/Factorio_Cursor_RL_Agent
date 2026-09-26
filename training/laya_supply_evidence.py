# Path: training/laya_supply_evidence.py
# Purpose: Project measured supply, committed construction demand, and provenance for Laya.

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping, Sequence


class LayaSupplyEvidenceError(ValueError):
    """Raised when explicit supply evidence is malformed or ambiguous."""


def _quantity(value: Any, label: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LayaSupplyEvidenceError(f"{label} must be a non-negative finite number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise LayaSupplyEvidenceError(f"{label} must be a non-negative finite number")
    return number


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LayaSupplyEvidenceError(f"{label} must be a non-empty string")
    return value.strip()


def _provenance(value: Any, label: str) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        raise LayaSupplyEvidenceError(f"{label} must contain only finite numbers")
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _provenance(part, f"{label}.{key}") for key, part in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_provenance(part, label) for part in value]
    raise LayaSupplyEvidenceError(f"{label} must be JSON-serializable provenance")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LayaSupplyEvidenceError(f"{label} must be an object")
    return value


def project_supply_evidence(
    items: Mapping[str, Mapping[str, Any]],
    committed_bills: Sequence[Mapping[str, Any]] = (),
    reservations: Sequence[Mapping[str, Any]] = (),
    recipes: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a factual, JSON-safe supply projection without choosing an action.

    Item records accept measured `stock`, `production_rate`, `draw_rate`, and
    `capacity_rate` values (each may be null when unknown), plus optional
    `provenance` and `bottleneck` evidence. Bills have a unique `bill_id`, a
    `requirements` item-to-quantity map, and optional provenance. Reservations
    identify `bill_id`, `item`, and `amount`; they reduce outstanding bill
    demand and count as allocated stock. Recipe facts are optional and have
    `output_item`, `output_amount`, `ingredients`, and optional provenance.
    """
    _mapping(items, "items")
    normalized_items: dict[str, dict[str, Any]] = {}
    for raw_item, raw_facts in items.items():
        item = _name(raw_item, "item name")
        facts = _mapping(raw_facts, f"items.{item}")
        normalized_items[item] = {
            "stock": _quantity(facts.get("stock"), f"items.{item}.stock", allow_none=True),
            "production_rate": _quantity(facts.get("production_rate"), f"items.{item}.production_rate", allow_none=True),
            "draw_rate": _quantity(facts.get("draw_rate"), f"items.{item}.draw_rate", allow_none=True),
            "capacity_rate": _quantity(facts.get("capacity_rate"), f"items.{item}.capacity_rate", allow_none=True),
            "provenance": _provenance(facts.get("provenance"), f"items.{item}.provenance"),
            "bottleneck": _provenance(facts.get("bottleneck"), f"items.{item}.bottleneck"),
        }

    bills_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_bill in enumerate(committed_bills):
        bill = _mapping(raw_bill, f"committed_bills[{index}]")
        bill_id = _name(bill.get("bill_id"), f"committed_bills[{index}].bill_id")
        if bill_id in bills_by_id:
            raise LayaSupplyEvidenceError(f"duplicate committed bill id: {bill_id}")
        requirements = _mapping(bill.get("requirements"), f"bill {bill_id}.requirements")
        normalized: dict[str, float] = {}
        for raw_item, amount in requirements.items():
            item = _name(raw_item, f"bill {bill_id} item")
            normalized[item] = _quantity(amount, f"bill {bill_id}.{item}")  # type: ignore[assignment]
            if item not in normalized_items:
                normalized_items[item] = {
                    "stock": None, "production_rate": None, "draw_rate": None,
                    "capacity_rate": None, "provenance": None, "bottleneck": None,
                }
        bills_by_id[bill_id] = {
            "requirements": normalized,
            "provenance": _provenance(bill.get("provenance"), f"bill {bill_id}.provenance"),
        }

    allocations: dict[tuple[str, str], float] = defaultdict(float)
    item_allocated: dict[str, float] = defaultdict(float)
    reservation_provenance: dict[str, list[Any]] = defaultdict(list)
    for index, raw_reservation in enumerate(reservations):
        reservation = _mapping(raw_reservation, f"reservations[{index}]")
        bill_id = _name(reservation.get("bill_id"), f"reservations[{index}].bill_id")
        item = _name(reservation.get("item"), f"reservations[{index}].item")
        amount = _quantity(reservation.get("amount"), f"reservations[{index}].amount")
        assert amount is not None
        if bill_id not in bills_by_id:
            raise LayaSupplyEvidenceError(f"reservation references unknown bill: {bill_id}")
        if item not in bills_by_id[bill_id]["requirements"]:
            raise LayaSupplyEvidenceError(f"reservation item {item} is not required by bill {bill_id}")
        allocations[(bill_id, item)] += amount
        if allocations[(bill_id, item)] > bills_by_id[bill_id]["requirements"][item]:
            raise LayaSupplyEvidenceError(f"reservation exceeds bill requirement: {bill_id}.{item}")
        item_allocated[item] += amount
        provenance = _provenance(reservation.get("provenance"), f"reservation {bill_id}.{item}.provenance")
        if provenance is not None:
            reservation_provenance[item].append(provenance)

    demand_gross: dict[str, float] = defaultdict(float)
    demand_reserved: dict[str, float] = defaultdict(float)
    bill_sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bill_id, bill in bills_by_id.items():
        for item, amount in bill["requirements"].items():
            reserved = allocations[(bill_id, item)]
            demand_gross[item] += amount
            demand_reserved[item] += reserved
            bill_sources[item].append({
                "bill_id": bill_id,
                "required": amount,
                "reserved": reserved,
                "provenance": bill["provenance"],
            })

    future_demand = {
        item: sum(source["required"] - source["reserved"] for source in sources)
        for item, sources in bill_sources.items()
    }
    recipe_projection = _project_recipe_demand(future_demand, bill_sources, recipes)
    recipe_amounts = (
        recipe_projection["ingredients"] if recipe_projection is not None else {}
    )
    recipe_sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if recipe_projection is not None:
        for use in recipe_projection["uses"]:
            recipe_sources[use["item"]].append({
                "bill_ids": use["bill_ids"],
                "recipe_id": use["recipe_id"],
                "from_item": use["from_item"],
                "provenance": use["provenance"],
            })
        for item in recipe_amounts:
            normalized_items.setdefault(item, {
                "stock": None, "production_rate": None, "draw_rate": None,
                "capacity_rate": None, "provenance": None, "bottleneck": None,
            })

    projected_items: dict[str, Any] = {}
    for item, facts in normalized_items.items():
        stock = facts["stock"]
        allocated = item_allocated[item]
        accessible = None if stock is None else max(0.0, stock - allocated)
        capacity = facts["capacity_rate"]
        draw = facts["draw_rate"]
        observed = facts["production_rate"]
        headroom = None if capacity is None or observed is None else max(0.0, capacity - observed)
        demand_margin = None if capacity is None or draw is None else capacity - draw
        projected_items[item] = {
            "stock": {
                "measured": stock,
                "allocated": allocated,
                "accessible": accessible,
                "overallocated": None if stock is None else max(0.0, allocated - stock),
                "reservation_provenance": reservation_provenance[item],
            },
            "demand": {
                "current_rate": draw,
                "recipe_projected": recipe_amounts.get(item) if recipe_projection is not None else None,
                "recipe_sources": recipe_sources[item],
                "committed": {
                    "gross": demand_gross[item],
                    "reserved": demand_reserved[item],
                    "remaining": demand_gross[item] - demand_reserved[item],
                    "bills": bill_sources[item],
                },
            },
            "production": {
                "observed_rate": observed,
                "capacity_rate": capacity,
                "headroom_rate": headroom,
                "headroom_status": "known" if headroom is not None else "unknown",
                "capacity_margin_over_current_demand": demand_margin,
            },
            "bottleneck": {
                "evidence": facts["bottleneck"],
                "provenance": facts["provenance"],
            },
            "provenance": facts["provenance"],
        }

    return {"version": "1.0.0", "items": projected_items, "recipe_projection": recipe_projection}


def _project_recipe_demand(
    direct_demand: Mapping[str, float],
    demand_sources: Mapping[str, Sequence[Mapping[str, Any]]],
    recipes: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    if recipes is None:
        return None
    _mapping(recipes, "recipes")
    by_output: dict[str, tuple[str, float, Mapping[str, Any], Any]] = {}
    for raw_recipe_id, raw_recipe in recipes.items():
        recipe_id = _name(raw_recipe_id, "recipe id")
        recipe = _mapping(raw_recipe, f"recipes.{recipe_id}")
        output_item = _name(recipe.get("output_item"), f"recipes.{recipe_id}.output_item")
        output_amount = _quantity(recipe.get("output_amount"), f"recipes.{recipe_id}.output_amount")
        assert output_amount is not None
        if output_amount == 0:
            raise LayaSupplyEvidenceError(f"recipe {recipe_id} output_amount must be positive")
        ingredients_raw = _mapping(recipe.get("ingredients"), f"recipes.{recipe_id}.ingredients")
        ingredients: dict[str, float] = {}
        for raw_item, amount in ingredients_raw.items():
            ingredient = _name(raw_item, f"recipe {recipe_id} ingredient")
            ingredients[ingredient] = _quantity(amount, f"recipe {recipe_id}.{ingredient}")  # type: ignore[assignment]
        if output_item in by_output:
            raise LayaSupplyEvidenceError(f"multiple live recipes produce {output_item}; select one explicitly")
        by_output[output_item] = (
            recipe_id, output_amount, ingredients,
            _provenance(recipe.get("provenance"), f"recipe {recipe_id}.provenance"),
        )

    ingredient_totals: dict[str, float] = defaultdict(float)
    uses: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []

    def expand(item: str, amount: float, path: tuple[str, ...], bill_ids: tuple[str, ...]) -> None:
        recipe_info = by_output.get(item)
        if recipe_info is None:
            return
        recipe_id, output_amount, ingredients, recipe_source = recipe_info
        if item in path:
            cycles.append({"cycle": list(path + (item,)), "blocked_recipe": recipe_id})
            return
        scale = amount / output_amount
        for ingredient, per_craft in ingredients.items():
            required = per_craft * scale
            ingredient_totals[ingredient] += required
            uses.append({
                "item": ingredient,
                "amount": required,
                "from_item": item,
                "recipe_id": recipe_id,
                "bill_ids": list(bill_ids),
                "provenance": recipe_source,
            })
            expand(ingredient, required, path + (item,), bill_ids)

    for item, amount in direct_demand.items():
        sources = demand_sources.get(item, ())
        if sources:
            for source in sources:
                remaining = source["required"] - source["reserved"]
                if remaining:
                    expand(item, remaining, (), (source["bill_id"],))
        elif amount:
            expand(item, amount, (), ())
    return {
        "ingredients": dict(sorted(ingredient_totals.items())),
        "uses": uses,
        "cycles": cycles,
        "provenance": {value[0]: value[3] for value in by_output.values()},
    }


__all__ = ["LayaSupplyEvidenceError", "project_supply_evidence"]
