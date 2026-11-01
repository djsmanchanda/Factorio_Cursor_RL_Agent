# Path: tools/optimizer_demand_observation.py | Purpose: Relate a candidate fluid output to explicit catalog-based phase demand.
"""A scoped planning calculation, not evidence of spare supply or execution authority."""

from collections.abc import Mapping
from fractions import Fraction
from functools import lru_cache
import copy
import hashlib
import json

from core.catalog_demand import CatalogDemandError, plan_catalog_demand


def make_phase_fluid_demand_observer(bound, demand_model):
    """Build a cached exact demand observer for one bound proposal and pinned catalog."""
    model = copy.deepcopy(demand_model)
    raw = model["catalog_json"].encode("utf-8")
    provenance = model["provenance"]
    digest = hashlib.sha256(raw).hexdigest()
    if digest != provenance.get("catalog_sha256"):
        raise ValueError("demand catalog hash does not match its provenance")
    boundaries = model["boundaries"]
    preferences = model["preferences"]
    output = bound.plan.throughput_output
    output_unit = bound.plan.throughput_unit
    scope = bound.plan.binding.scope

    @lru_cache(maxsize=8)
    def solve(targets):
        try:
            result = plan_catalog_demand(raw, provenance=provenance,
                                         demand={name: Fraction(rate) for name, rate in targets},
                                         boundaries=boundaries, preferences=preferences)
            return result, None
        except CatalogDemandError as error:
            return None, error.code

    def observe(facts, board):
        def unavailable(reason):
            return {"status": "unavailable", "reason": reason, "product": output,
                    "catalog_sha256": digest, "dispatch_authorized": False}

        if output_unit not in {"fluid/tick", "fluid/second"}:
            return unavailable("proposal_output_not_fluid")
        if not isinstance(facts, Mapping) or not isinstance(board, Mapping):
            return unavailable("observation_missing")
        if (facts.get("surface") != scope.surface or facts.get("force") != scope.force
                or provenance.get("surface") != scope.surface or provenance.get("force") != scope.force):
            return unavailable("scope_mismatch")
        mods = facts.get("mods")
        declared = provenance.get("mods")
        if (not isinstance(mods, Mapping) or not isinstance(declared, list)
                or any(not isinstance(row, Mapping) or set(row) != {"name", "version"}
                       or not isinstance(row.get("name"), str) or not isinstance(row.get("version"), str)
                       for row in declared)
                or len({row["name"] for row in declared}) != len(declared)
                or dict(mods) != {row["name"]: row["version"] for row in declared}):
            return unavailable("mod_set_mismatch")
        tick, exported = facts.get("tick"), provenance.get("export_tick")
        if type(tick) is not int or type(exported) is not int or tick < exported:
            return unavailable("catalog_tick_mismatch")
        enabled = facts.get("enabled_recipes")
        if not isinstance(enabled, Mapping):
            return unavailable("enabled_recipes_unavailable")
        targets = board.get("targets")
        if not isinstance(targets, Mapping) or not targets or len(targets) > 64:
            return unavailable("phase_targets_invalid")
        try:
            normalized = []
            for name, value in targets.items():
                if not isinstance(name, str) or not name or type(value) not in (int, float):
                    return unavailable("phase_targets_invalid")
                rate = Fraction(str(value))
                if rate <= 0:
                    return unavailable("phase_targets_invalid")
                normalized.append((name, str(rate)))
            model_result, solve_error = solve(tuple(sorted(normalized)))
        except (ValueError, TypeError, OverflowError, ZeroDivisionError):
            return unavailable("phase_targets_invalid")
        if solve_error is not None:
            return unavailable(solve_error)
        if any(enabled.get(recipe) is not True for recipe in model_result.selected_recipes):
            return unavailable("selected_recipe_not_observed_enabled")
        balance = model_result.balance
        if balance.status != "unique" or balance.rates is None:
            return unavailable("balance_" + balance.status)
        if any(rate < 0 for rate in balance.rates):
            return unavailable("balance_negative_rates")
        consumed = produced = Fraction(0)
        present_as_fluid = False
        for signature, rate in zip(balance.recipe_signatures, balance.rates):
            for part in signature.ingredients:
                if part.type == "fluid" and part.name == output:
                    consumed += rate * part.amount
                    present_as_fluid = True
            for part in signature.products:
                if part.type == "fluid" and part.name == output:
                    produced += rate * part.amount
                    present_as_fluid = True
        direct_target = dict(model_result.demand).get(output, Fraction(0)) if present_as_fluid else Fraction(0)
        required = max(Fraction(0), consumed + direct_target - produced)
        boundary = next((b for b in model_result.boundaries if b.name == output), None)
        return {"status": "required_by_selected_recipe_model" if required else "not_required_by_selected_recipe_model",
                "product": output, "catalog_sha256": digest, "catalog_tick": exported, "observation_tick": tick,
                "phase_targets_per_second": dict(sorted(normalized)), "selected_recipes": list(model_result.selected_recipes[:64]),
                "selected_recipes_omitted": max(0, len(model_result.selected_recipes) - 64),
                "gross_consumption_per_second": str(consumed), "internal_production_per_second": str(produced),
                "direct_target_per_second": str(direct_target), "net_external_input_per_second": str(required),
                "balance_feasible": balance.feasible, "boundary": None if boundary is None else {
                    "rate": None if boundary.rate is None else str(boundary.rate),
                    "cap": None if boundary.cap is None else str(boundary.cap)},
                "available_supply_per_second": None, "dispatch_authorized": False,
                "basis": "caller-pinned catalog, explicit boundaries and selected recipes; runtime identity and supply unverified"}
    return observe
