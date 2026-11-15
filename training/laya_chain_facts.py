# Path: training/laya_chain_facts.py | Purpose: Conservative consumer continuity and joint construction-bill facts.

"""Observations only; these facts neither certify a milestone nor reserve stock."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping


class ConsumerObservation:
    """Compare consecutive surveys of identified machines on an unchanged catalog."""

    def __init__(self, ingredient: str):
        self.ingredient = ingredient
        self.previous = None

    def observe(self, facts: Mapping) -> dict:
        catalog = facts.get("recipe_observations")
        machines = facts.get("machines")
        tick = facts.get("tick")
        if (facts.get("surface") != "nauvis" or facts.get("force") != "player"
                or not isinstance(facts.get("mods"), Mapping) or not isinstance(catalog, Mapping)
                or not isinstance(machines, list) or type(tick) is not int or tick < 0):
            self.previous = None
            return {"status": "unknown", "reason": "incomplete_scope_or_catalog"}
        try:
            signature = hashlib.sha256(json.dumps([facts["mods"], catalog], sort_keys=True,
                                                   separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        except (TypeError, ValueError):
            self.previous = None
            return {"status": "unknown", "reason": "invalid_catalog"}
        consumers = set()
        for name, recipe in catalog.items():
            if not isinstance(recipe, Mapping) or not isinstance(recipe.get("ingredients"), list):
                self.previous = None
                return {"status": "unknown", "reason": "invalid_catalog"}
            if any(not isinstance(part, Mapping) or not isinstance(part.get("name"), str)
                   or type(part.get("amount")) not in (int, float) or not math.isfinite(part["amount"])
                   or part["amount"] <= 0 for part in recipe["ingredients"]):
                self.previous = None
                return {"status": "unknown", "reason": "invalid_catalog"}
            if any(isinstance(part, Mapping) and part.get("name") == self.ingredient
                   and part.get("type", "item") == "item" and isinstance(part.get("amount"), (int, float))
                   and not isinstance(part.get("amount"), bool) and part["amount"] > 0
                   for part in recipe["ingredients"]):
                consumers.add(name)
        current = {}
        invalid = False
        for machine in machines:
            if not isinstance(machine, Mapping):
                invalid = True
                continue
            recipe = machine.get("recipe")
            if recipe not in consumers:
                continue
            ident, count = machine.get("unit_number"), machine.get("products_finished")
            if type(ident) is not int or ident <= 0 or type(count) is not int or count < 0 or ident in current:
                invalid = True
                continue
            current[ident] = (recipe, count)
        previous = self.previous
        self.previous = None if invalid else (tick, signature, current)
        result = {"status": "unknown", "reason": "baseline_required", "consumer_recipes": sorted(consumers)}
        if invalid:
            return {**result, "reason": "consumer_identity_or_counter_missing"}
        if previous is None or previous[1] != signature:
            return result
        if tick <= previous[0]:
            self.previous = None
            return {**result, "reason": "non_increasing_tick"}
        progress = []
        reset = False
        for ident, (recipe, count) in current.items():
            old = previous[2].get(ident)
            if old and old[0] == recipe:
                if count < old[1]:
                    reset = True
                elif count > old[1]:
                    progress.append({"unit_number": ident, "recipe": recipe, "baseline": old[1],
                                     "observed": count, "from_tick": previous[0], "tick": tick})
        if reset:
            self.previous = None
            return {**result, "reason": "counter_reset"}
        if progress:
            return {**result, "status": "observed", "reason": "counter_progress", "progress": progress}
        return {**result, "reason": "no_continuous_progress"}


def consumer_choices(facts: Mapping, free_stock: Mapping | None, supported: Mapping) -> list[dict]:
    """Find catalog-backed solid consumers with one craft's inputs, not a funded construction plan."""
    catalog = facts.get("recipe_observations")
    if not isinstance(catalog, Mapping) or not isinstance(free_stock, Mapping):
        return []
    choices = []
    for name, recipe in sorted(catalog.items()):
        spec = supported.get(name)
        if not isinstance(recipe, Mapping) or recipe.get("enabled") is not True or not isinstance(spec, Mapping):
            continue
        ingredients, products = recipe.get("ingredients"), recipe.get("products")
        if (not isinstance(ingredients, list) or not ingredients or not isinstance(products, list)
                or len(products) != 1 or not isinstance(products[0], Mapping)
                or products[0].get("name") != name or products[0].get("type", "item") != "item"
                or not str(spec.get("machine", "")).startswith("assembling-machine-")
                or spec.get("fluid_ingredients")
                or not isinstance(recipe.get("machine_speeds"), Mapping)
                or not recipe["machine_speeds"].get(spec["machine"])):
            continue
        bill = {}
        for part in ingredients:
            if (not isinstance(part, Mapping) or part.get("type", "item") != "item"
                    or not isinstance(part.get("name"), str) or isinstance(part.get("amount"), bool)
                    or not isinstance(part.get("amount"), (int, float))
                    or not math.isfinite(part["amount"]) or part["amount"] <= 0):
                break
            bill[part["name"]] = bill.get(part["name"], 0) + math.ceil(part["amount"])
        else:
            expected = dict(zip(spec.get("ingredients", ()), spec.get("amounts", ())))
            if ("advanced-circuit" in bill and bill == expected
                    and all(type(free_stock.get(item)) is int and free_stock[item] >= count
                            for item, count in bill.items())):
                choices.append({"item": name, "input_bill": bill})
    return choices


def bill_facts(bills: Mapping | None, free_stock: Mapping | None, *, complete: bool) -> dict:
    """Compare complete, disjoint stage bills with one shared stock pool, without allocating it."""
    if not complete or not isinstance(bills, Mapping) or not bills or not isinstance(free_stock, Mapping):
        return {"status": "unknown", "jointly_funded": None, "stages": None}
    if any(not isinstance(bill, Mapping) for bill in bills.values()):
        return {"status": "unknown", "jointly_funded": None, "stages": None}
    if any(type(value) is not int or value < 0 for value in free_stock.values()):
        return {"status": "unknown", "jointly_funded": None, "stages": None}
    if any(not isinstance(item, str) or type(count) is not int or count < 0
           for bill in bills.values() for item, count in bill.items()):
        return {"status": "unknown", "jointly_funded": None, "stages": None}
    total = sum((Counter(bill) for bill in bills.values()), Counter())
    missing = {item: count - free_stock.get(item, 0) for item, count in total.items()
               if count > free_stock.get(item, 0)}
    return {"status": "known", "jointly_funded": not missing, "joint_shortfall": missing,
            "total": dict(total), "stages": {name: {"funded_individually": all(
                count <= free_stock.get(item, 0) for item, count in bill.items())} for name, bill in bills.items()}}


def committed_chain_facts(economics: Mapping) -> dict:
    """Expose complete recorded bills and ownership without treating WIP as free.

    These are current registered obligations, not a speculative geometry bill
    for a future line. Requester demand can keep changing; it is shown separately.
    """
    projects = economics.get("projects")
    stock = (economics.get("stock") or {}).get("network")
    unknown = {"version": "committed-chain-v1", "status": "unknown",
               "scope": "registered_material_projects", "future_chain_bill_complete": False,
               "jointly_funded": None, "owners": None}
    if not isinstance(projects, Mapping) or not isinstance(stock, Mapping):
        return unknown
    if any(type(value) is not int or value < 0 for value in stock.values()):
        return unknown
    required, reserved, owners = Counter(), Counter(), {}
    for owner, project in projects.items():
        if not isinstance(project, Mapping):
            return unknown
        bill, allocation = project.get("required"), project.get("reserved")
        if (not isinstance(bill, Mapping) or not isinstance(allocation, Mapping)
                or any(type(n) is not int or n < 0 for n in (*bill.values(), *allocation.values()))
                or any(n > bill.get(item, 0) for item, n in allocation.items())):
            return unknown
        # Construction already handed to bots can consume its bill between
        # surveys. Its original bill is not a new stock obligation.
        constructing = project.get("state") == "constructing"
        owners[owner] = {"target_item": project.get("target_item"),
                         "state": project.get("state"), "required": dict(bill),
                         "reserved": dict(allocation), "construction_in_flight": constructing,
                         "shortfall": None if constructing else {
                             item: n - allocation.get(item, 0) for item, n in bill.items()
                             if n > allocation.get(item, 0)}}
        if not constructing:
            required.update(bill)
            reserved.update(allocation)
    if any(n > stock.get(item, 0) for item, n in reserved.items()):
        return {**unknown, "reason": "allocation_exceeds_observed_transferable_stock", "owners": owners}
    unreserved = {item: max(0, n - reserved.get(item, 0)) for item, n in stock.items()}
    shortfall = {item: n - stock.get(item, 0) for item, n in required.items() if n > stock.get(item, 0)}
    for owner, row in owners.items():
        row["funded_with_own_allocation"] = (None if row["construction_in_flight"] else all(
            n <= row["reserved"].get(item, 0) + unreserved.get(item, 0)
            for item, n in row["required"].items()))
    loans = economics.get("loans")
    return {**unknown, "status": "known", "owners": owners, "jointly_funded": not shortfall,
            "joint_shortfall": shortfall, "total_unsubmitted_bill": dict(required),
            "reserved_by_owners": dict(reserved), "unreserved": unreserved,
            "requester_draws": economics.get("requester_draws"),
            "lender_costs": None if loans is None else [
                {"owner": loan.get("owner"), "displaced_recipe": loan.get("lender"),
                 "making": loan.get("making"), "remaining_to_target": loan.get("remaining_to_target"),
                 "completed_crafts": loan.get("step_completed_crafts"),
                 "lost_output_per_second": None} for loan in loans],
            "funding_is_snapshot": True}


def production_service_items(board: Mapping, facts: Mapping, supported: Mapping, chemical_specs: Mapping) -> list[str]:
    """Enabled recipes backed by the permanent deterministic operation.

    Plate and mine lifecycle operations remain explicit in their own menu.
    Fluid outputs are admitted only through a real chemical backend.
    """
    catalog = facts.get("recipe_observations")
    if not isinstance(catalog, Mapping):
        return []
    excluded = {"iron-plate", "copper-plate", "stone-brick", "steel-plate",
                "iron-ore", "copper-ore", "stone", "coal"}
    chemical = {"plastic-bar", "sulfur", "sulfuric-acid", "battery"}
    items = set()
    for need in board.get("needs", ()):
        item = need.get("item")
        recipe = catalog.get(item)
        if (item in excluded or need.get("condition") == "satisfied"
                or not isinstance(recipe, Mapping) or recipe.get("enabled") is not True):
            continue
        products = recipe.get("products") or []
        if len(products) != 1 or products[0].get("name") != item:
            continue
        if item in chemical:
            spec = chemical_specs.get(item)
            if not isinstance(spec, Mapping):
                continue
            expected = {("item", name): amount for name, amount in
                        zip(spec["item_ingredients"], spec["item_amounts"])}
            expected.update({("fluid", name): amount for name, amount in spec["fluid_ingredients"].items()})
            actual = Counter()
            for part in recipe.get("ingredients") or []:
                if (not isinstance(part, Mapping) or not isinstance(part.get("name"), str)
                        or type(part.get("amount")) not in (int, float)
                        or not math.isfinite(part["amount"]) or part["amount"] <= 0):
                    break
                actual[(part.get("type", "item"), part["name"])] += part["amount"]
            else:
                product = products[0]
                output = (spec["item_product_amounts"].get(item) if product.get("type", "item") == "item"
                          else spec["fluid_products"].get(item))
                if dict(actual) == expected and output is not None and product.get("amount") == output:
                    items.add(item)
            continue
        spec = supported.get(item)
        if not isinstance(spec, Mapping) or spec.get("fluid_ingredients"):
            continue
        ingredients = recipe.get("ingredients") or []
        if (not ingredients or any(part.get("type", "item") != "item" for part in ingredients)
                or products[0].get("type", "item") != "item"):
            continue
        bill = Counter()
        for part in ingredients:
            if type(part.get("amount")) not in (int, float) or not math.isfinite(part["amount"]) or part["amount"] <= 0:
                break
            bill[part["name"]] += part["amount"]
        else:
            if dict(bill) == dict(zip(spec.get("ingredients", ()), spec.get("amounts", ()))):
                items.add(item)
    return sorted(items)
