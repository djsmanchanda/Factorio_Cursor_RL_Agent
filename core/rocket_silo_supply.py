# Path: core/rocket_silo_supply.py
# Purpose: Convert a scoped silo's remaining rocket-part recipe into explicit finite-horizon catalog demand.

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
from pathlib import Path
from collections.abc import Mapping

from jsonschema import Draft7Validator

from core.catalog_demand import CatalogDemandResult, plan_catalog_demand
from core.recipe_dag import validate_recipe_catalog


NON_CLAIMS = (
    "The silo and catalog are caller-supplied observations, not verified runtime or save identity.",
    "The bill uses unmodified base recipe amounts; productivity, quality and runtime effects are not modeled.",
    "Requested average rates are a planning target, not a delivery schedule, inventory reservation or throughput guarantee.",
    "Existing silo input inventories, other consumers, construction, power and transport are not accounted for.",
    "Rocket parts and readiness do not prove or authorize launch, delivery or victory.",
)


@dataclass(frozen=True)
class SiloSupplyPlan:
    surface: str
    force: str
    unit_number: int
    observation_tick: int
    catalog_tick: int
    horizon_ticks: int
    parts_observed: int
    parts_required: int
    remaining_parts: int
    input_bill: tuple[tuple[str, int], ...]
    input_rates_per_second: tuple[tuple[str, Fraction], ...]
    demand: CatalogDemandResult
    notes: tuple[str, ...] = NON_CLAIMS


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field in recipe catalog")
        result[key] = value
    return result


def _invalid_number(_value):
    raise ValueError("non-finite JSON number in recipe catalog")


def plan_silo_supply(
    catalog_bytes: bytes, *, provenance: Mapping, observation: Mapping,
    unit_number: int, horizon_ticks: int, boundaries: object = (),
    preferences: object = None,
) -> SiloSupplyPlan:
    """Plan base recipe inputs for one observed, already-built rocket silo.

    The requested horizon is a game-tick budget, not wall time. Inputs already
    in the silo are deliberately not subtracted. Catalog and observation
    declarations are checked for consistency but are not authenticated.
    """
    if type(unit_number) is not int or unit_number < 0:
        raise ValueError("unit_number must be a non-negative integer")
    if type(horizon_ticks) is not int or horizon_ticks <= 0:
        raise ValueError("horizon_ticks must be a positive integer")
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "rocket_silo_observation.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(observation, Mapping) or not Draft7Validator(schema).is_valid(observation):
        raise ValueError("silo observation does not match its schema")
    if observation["ok"] is not True:
        raise ValueError("silo observation failed")
    requested = observation["requested"]["unit_number"]
    if (requested.get("state") != "available" or requested.get("value") != unit_number
            or observation["silo_count"] != 1 or len(observation["silos"]) != 1):
        raise ValueError("silo observation must be filtered to exactly the requested silo")
    silo = observation["silos"][0]
    if silo["unit_number"].get("state") != "available" or silo["unit_number"].get("value") != unit_number:
        raise ValueError("silo identity does not match the requested unit")
    recipe_fact = silo["recipe"]
    if recipe_fact.get("state") != "available" or recipe_fact.get("value") != "rocket-part":
        raise ValueError("silo must report the active rocket-part recipe")
    actual, required = silo["parts"]["actual"], silo["parts"]["required"]
    if actual.get("state") != "available" or required.get("state") != "available" or required["value"] <= 0:
        raise ValueError("silo part counts must be available and required count positive")
    remaining = required["value"] - actual["value"]
    if remaining <= 0:
        raise ValueError("silo has no remaining rocket parts to plan")
    if not isinstance(provenance, Mapping):
        raise ValueError("catalog provenance must be a mapping")
    if (provenance.get("surface") != observation["surface"]
            or provenance.get("force") != observation["force"]):
        raise ValueError("catalog and silo scope mismatch")
    if (type(provenance.get("export_tick")) is not int
            or provenance["export_tick"] > observation["tick"]):
        raise ValueError("catalog tick must not follow silo observation")
    if not isinstance(catalog_bytes, bytes):
        raise ValueError("catalog must be exact bytes")
    try:
        catalog = json.loads(catalog_bytes, object_pairs_hook=_unique_object, parse_constant=_invalid_number)
        validate_recipe_catalog(catalog)
    except (ValueError, TypeError, UnicodeDecodeError) as error:
        raise ValueError("invalid recipe catalog") from error
    recipes = [recipe for recipe in catalog["recipes"] if recipe["name"] == "rocket-part"]
    if len(recipes) != 1:
        raise ValueError("catalog must contain one rocket-part recipe")
    recipe = recipes[0]
    if (recipe["enabled"] is not True or recipe["supported"] is not True
            or recipe["products"] != [{"name": "rocket-part", "type": "item", "amount": 1}]):
        raise ValueError("rocket-part recipe must be enabled, supported and produce one part")
    bill = {}
    for part in recipe["ingredients"]:
        amount = part["amount"]
        if (part["type"] != "item" or type(amount) is not int or amount <= 0
                or part["name"] in bill):
            raise ValueError("rocket-part ingredients must be unique positive whole items")
        bill[part["name"]] = amount * remaining
    if not bill:
        raise ValueError("rocket-part recipe must have explicit ingredients")
    rates = {name: Fraction(amount * 60, horizon_ticks) for name, amount in bill.items()}
    demand = plan_catalog_demand(
        catalog_bytes, provenance=provenance, demand=rates,
        boundaries=boundaries, preferences=preferences,
    )
    return SiloSupplyPlan(
        surface=observation["surface"], force=observation["force"], unit_number=unit_number,
        observation_tick=observation["tick"], catalog_tick=provenance["export_tick"],
        horizon_ticks=horizon_ticks, parts_observed=actual["value"], parts_required=required["value"],
        remaining_parts=remaining, input_bill=tuple(sorted(bill.items())),
        input_rates_per_second=tuple(sorted(rates.items())), demand=demand,
    )
