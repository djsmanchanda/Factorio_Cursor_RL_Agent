# Path: core/bounded_fluid_machines.py
# Purpose: Convert one independently re-verified optimal bounded recipe balance into exact fluid-machine and fluid-source facts.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from math import isfinite

from core.bounded_linear import MAX_CANDIDATE_BUDGET
from core.bounded_recipe_balance import (
    BoundedRecipeBalanceError,
    BoundedRecipeBalanceResult,
    MaterialDecision,
    MaterialDemand,
    MaterialIdentity,
    RecipeDecision,
    optimize_bounded_recipe_balance,
)
from core.fixed_recipe_balance import RecipePartSignature, RecipeSignature
from core.recipe_categories import recipe_categories
from planners.fluid_layouts import (
    FLUID_MACHINE_SPEEDS,
    FLUID_RECIPES,
    MACHINE_FOOTPRINTS,
    VERIFIED_PIPE_TILES,
)

_RECIPE_FIELDS = frozenset(
    {
        "name",
        "enabled",
        "category",
        "categories",
        "energy_ticks",
        "ingredients",
        "products",
        "supported",
        "unsupported_reason",
    }
)
_MACHINE_FIELDS = frozenset(
    {"name", "type", "ingredient_count", "crafting_speed", "categories"}
)
_MACHINE_TYPES = frozenset({"assembling-machine", "furnace"})

# These fields would change the speed or yield computation, and this module
# models only catalog ticks, explicit machine speed, and fluid recipe parts.
# Fail closed rather than silently ignoring a claimed effect.
_UNSUPPORTED_EFFECT_FIELDS = frozenset(
    {
        "beacon",
        "beacons",
        "crafting_speed_bonus",
        "effect",
        "effects",
        "energy_usage",
        "fuel",
        "fuel_effect",
        "module",
        "modules",
        "pollution",
        "productivity",
        "productivity_bonus",
        "quality",
        "quality_bonus",
        "speed",
        "speed_bonus",
    }
)

NON_CLAIMS = (
    "Machine counts and nominal rates are exact arithmetic from a bounded recipe "
    "balance whose optimal objective this module re-derives with the same exact "
    "solver under an explicit candidate budget; they are not runtime throughput "
    "measurements.",
    "Fluid consumption, internal production, and external source rates are exact "
    "gross rates from the selected recipe rates; an external source may serve final "
    "demand as well as machine inputs, so internal production is not simply the "
    "remainder of machine consumption.",
    "Fluid source demands and caps are material-boundary facts only. This module "
    "does not select or claim a source site, pumpjack, offshore pump, pipe "
    "capacity, pump, power supply, beacon, module, inserter, belt, or "
    "construction plan.",
    "This module adds no recipe, source, disposal, site, power, pipeline, or "
    "runtime decision and does not execute a plan.",
)


class BoundedFluidMachineError(ValueError):
    """The balance, catalog, or explicit machine speed cannot support exact fluid-machine facts."""


@dataclass(frozen=True)
class BoundedFluidMachineFact:
    """Exact machine arithmetic for one re-verified optimal fluid recipe rate.

    ``required_crafts_per_second`` is the exact recipe rate from the bounded
    balance. ``craft_seconds`` is the hard-coded FLUID_RECIPES craft time that
    must equal the catalog ``energy_ticks / 60``. ``exact_machines`` divides the
    required craft work by the explicit machine speed, and ``machine_count`` is
    its ceiling. ``nominal_crafts_per_second`` is the rounded-up count's nominal
    capacity. ``capacity_shortfall_crafts_per_second`` is zero when that
    capacity covers the requirement; it is a fact about the rounded count, not
    a runtime promise.
    """

    recipe: str
    machine: str
    required_crafts_per_second: Fraction
    craft_seconds: Fraction
    machine_speed: Fraction
    exact_machines: Fraction
    machine_count: int
    nominal_crafts_per_second: Fraction
    spare_crafts_per_second: Fraction
    capacity_shortfall_crafts_per_second: Fraction


@dataclass(frozen=True)
class FluidSourceDemand:
    """Per-fluid input/output and explicit external source boundary facts.

    ``consumption_rate`` is the gross fluid consumed by every selected recipe.
    ``internally_produced_rate`` is the gross fluid produced by every selected
    recipe. ``external_source_rate`` is the balance's solved source rate for that
    fluid. ``final_demand_rate`` is the fixed net output demand for that fluid.
    The exact balance requires::

        internally_produced_rate + external_source_rate
            = consumption_rate + final_demand_rate

    (nonzero disposal is rejected by this adapter). An external source may
    therefore feed final demand directly rather than a machine input, and
    ``internally_produced_rate`` is gross production, not the remainder of
    consumption. ``cap`` is the caller's explicit source cap, or ``None`` when the
    fluid has no external source boundary. ``shortfall_rate`` is zero when the
    external source rate is within its cap; it is an explicit check, not a
    fabricated source recommendation.
    """

    fluid: str
    consumption_rate: Fraction
    external_source_rate: Fraction
    internally_produced_rate: Fraction
    final_demand_rate: Fraction
    cap: Fraction | None
    shortfall_rate: Fraction


@dataclass(frozen=True)
class BoundedFluidMachineResult:
    """Exact fluid-machine facts in deterministic recipe-name order."""

    facts: tuple[BoundedFluidMachineFact, ...]
    total_machines: int
    fluid_sources: tuple[FluidSourceDemand, ...]
    non_claims: tuple[str, ...] = NON_CLAIMS


@dataclass(frozen=True)
class _FixedRecipe:
    name: str
    machine: str
    craft_seconds: Fraction
    ingredients: tuple[RecipePartSignature, ...]
    products: tuple[RecipePartSignature, ...]


@dataclass(frozen=True)
class _Machine:
    name: str
    type: str
    ingredient_count: int
    categories: frozenset[str]
    crafting_speed: Fraction


def _exact_number(value: object, label: str) -> Fraction:
    if isinstance(value, bool):
        raise BoundedFluidMachineError(f"{label} must be a finite number, not a boolean")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if not isfinite(value):
            raise BoundedFluidMachineError(f"{label} must be finite")
        return Fraction(Decimal(str(value)))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise BoundedFluidMachineError(f"{label} must be finite")
        return Fraction(value)
    raise BoundedFluidMachineError(f"{label} must be an int, Fraction, float, or Decimal")


def _positive_number(value: object, label: str) -> Fraction:
    number = _exact_number(value, label)
    if number <= 0:
        raise BoundedFluidMachineError(f"{label} must be positive")
    return number


def _nonnegative_number(value: object, label: str) -> Fraction:
    number = _exact_number(value, label)
    if number < 0:
        raise BoundedFluidMachineError(f"{label} must not be negative")
    return number


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BoundedFluidMachineError(f"{label} must be a nonempty string")
    return value


def _ceil_fraction(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


def _recipe_index(catalog: object) -> dict[str, Mapping]:
    if not isinstance(catalog, Mapping):
        raise BoundedFluidMachineError("catalog must be a mapping")
    recipes = catalog.get("recipes")
    if not isinstance(recipes, list):
        raise BoundedFluidMachineError("catalog must contain a recipes list")
    index: dict[str, Mapping] = {}
    for position, recipe in enumerate(recipes):
        if not isinstance(recipe, Mapping):
            raise BoundedFluidMachineError(
                f"catalog recipe at index {position} must be a mapping"
            )
        name = _name(recipe.get("name"), f"catalog recipe at index {position} name")
        if name in index:
            raise BoundedFluidMachineError(
                f"catalog defines ambiguous duplicate recipe {name!r}"
            )
        index[name] = recipe
    return index


def _machine_index(catalog: Mapping) -> dict[str, _Machine]:
    raw_machines = catalog.get("machines")
    if not isinstance(raw_machines, list) or not raw_machines:
        raise BoundedFluidMachineError(
            "catalog must contain a nonempty machines list so explicit machine speeds "
            "can be checked against the same catalog"
        )
    index: dict[str, _Machine] = {}
    for position, machine in enumerate(raw_machines):
        if not isinstance(machine, Mapping):
            raise BoundedFluidMachineError(
                f"catalog machine at index {position} must be a mapping"
            )
        name = _name(machine.get("name"), f"catalog machine at index {position} name")
        if name in index:
            raise BoundedFluidMachineError(
                f"catalog defines ambiguous duplicate machine {name!r}"
            )
        effect_fields = sorted(set(machine) & _UNSUPPORTED_EFFECT_FIELDS)
        if effect_fields:
            fields = ", ".join(map(repr, effect_fields))
            raise BoundedFluidMachineError(
                f"catalog machine {name!r} claims unsupported effects ({fields})"
            )
        extra = sorted(set(machine) - _MACHINE_FIELDS)
        if extra:
            fields = ", ".join(map(repr, extra))
            raise BoundedFluidMachineError(
                f"catalog machine {name!r} has unsupported fields ({fields})"
            )
        kind = machine.get("type")
        if kind not in _MACHINE_TYPES:
            raise BoundedFluidMachineError(
                f"catalog machine {name!r} has unsupported type {kind!r}"
            )
        slots = machine.get("ingredient_count")
        if type(slots) is not int or slots < 0:
            raise BoundedFluidMachineError(
                f"catalog machine {name!r} ingredient_count must be a nonnegative integer"
            )
        raw_categories = machine.get("categories")
        if not isinstance(raw_categories, list):
            raise BoundedFluidMachineError(f"catalog machine {name!r} categories must be a list")
        categories = frozenset(
            _name(category, f"catalog machine {name!r} categories[{index}]")
            for index, category in enumerate(raw_categories)
        )
        speed = _positive_number(
            machine.get("crafting_speed"), f"catalog machine {name!r} crafting_speed"
        )
        index[name] = _Machine(name=name, type=kind, ingredient_count=slots,
                               categories=categories, crafting_speed=speed)
    return index


def _catalog_parts(
    recipe_name: str,
    recipe: Mapping,
    side: str,
) -> tuple[RecipePartSignature, ...]:
    parts = recipe.get(side)
    if isinstance(parts, (str, bytes)) or not isinstance(parts, Sequence):
        raise BoundedFluidMachineError(f"catalog recipe {recipe_name!r} {side} must be a sequence")
    parsed: list[RecipePartSignature] = []
    seen: set[str] = set()
    for index, part in enumerate(parts):
        if not isinstance(part, Mapping):
            raise BoundedFluidMachineError(
                f"catalog recipe {recipe_name!r} {side}[{index}] must be a mapping"
            )
        extra = set(part) - {"name", "type", "amount"}
        if extra:
            fields = ", ".join(sorted(map(repr, extra)))
            raise BoundedFluidMachineError(
                f"catalog recipe {recipe_name!r} {side}[{index}] has unsupported fields ({fields})"
            )
        name = _name(part.get("name"), f"catalog recipe {recipe_name!r} {side}[{index}] name")
        kind = part.get("type")
        if kind not in ("item", "fluid"):
            raise BoundedFluidMachineError(
                f"catalog recipe {recipe_name!r} {side}[{index}] has unsupported type {kind!r}"
            )
        amount = _positive_number(
            part.get("amount"), f"catalog recipe {recipe_name!r} {side}[{index}] amount"
        )
        if name in seen:
            raise BoundedFluidMachineError(
                f"catalog recipe {recipe_name!r} repeats {name!r} in {side}"
            )
        seen.add(name)
        parsed.append(RecipePartSignature(name=name, type=kind, amount=amount))
    return tuple(parsed)


def _catalog_signature(recipe_name: str, recipe: Mapping) -> RecipeSignature:
    effect_fields = sorted(set(recipe) & _UNSUPPORTED_EFFECT_FIELDS)
    if effect_fields:
        fields = ", ".join(map(repr, effect_fields))
        raise BoundedFluidMachineError(
            f"catalog recipe {recipe_name!r} claims unsupported effects ({fields})"
        )
    extra = sorted(set(recipe) - _RECIPE_FIELDS)
    if extra:
        fields = ", ".join(map(repr, extra))
        raise BoundedFluidMachineError(
            f"catalog recipe {recipe_name!r} has unsupported fields ({fields})"
        )
    ingredients = _catalog_parts(recipe_name, recipe, "ingredients")
    products = _catalog_parts(recipe_name, recipe, "products")
    if not products:
        raise BoundedFluidMachineError(
            f"catalog recipe {recipe_name!r} has no deterministic products"
        )
    return RecipeSignature.from_parts(
        recipe_name,
        [(part.name, part.type, part.amount) for part in ingredients],
        [(part.name, part.type, part.amount) for part in products],
    )


def _fixed_parts(
    recipe_name: str,
    parts: Mapping[str, object],
    side: str,
) -> tuple[RecipePartSignature, ...]:
    parsed: list[RecipePartSignature] = []
    seen: set[str] = set()
    for raw_name, raw_amount in parts.items():
        name = _name(raw_name, f"FLUID_RECIPES {recipe_name!r} {side} name")
        amount = _positive_number(
            raw_amount, f"FLUID_RECIPES {recipe_name!r} {side} amount for {name!r}"
        )
        if name in seen:
            raise BoundedFluidMachineError(
                f"hard-coded FLUID_RECIPES recipe {recipe_name!r} repeats {name!r} in {side}"
            )
        seen.add(name)
        parsed.append(RecipePartSignature(name=name, type="fluid", amount=amount))
    return tuple(parsed)


def _fixed_recipe(recipe_name: str) -> _FixedRecipe:
    spec = FLUID_RECIPES.get(recipe_name)
    if not isinstance(spec, Mapping):
        raise BoundedFluidMachineError(
            f"recipe {recipe_name!r} is not a fully supported FLUID_RECIPES recipe"
        )
    machine = spec.get("machine")
    if not isinstance(machine, str) or not machine:
        raise BoundedFluidMachineError(
            f"hard-coded FLUID_RECIPES recipe {recipe_name!r} has no machine"
        )
    if machine not in FLUID_MACHINE_SPEEDS:
        raise BoundedFluidMachineError(
            f"hard-coded FLUID_RECIPES machine {machine!r} has no explicit machine speed"
        )
    if machine not in MACHINE_FOOTPRINTS:
        raise BoundedFluidMachineError(
            f"hard-coded FLUID_RECIPES machine {machine!r} has no verified footprint"
        )
    if recipe_name not in VERIFIED_PIPE_TILES:
        raise BoundedFluidMachineError(
            f"recipe {recipe_name!r} has no verified FLUID_RECIPES pipe tiles"
        )
    craft_seconds = _positive_number(
        spec.get("craft_time"), f"FLUID_RECIPES {recipe_name!r} craft_time"
    )

    ingredient_parts: list[RecipePartSignature] = []
    product_parts: list[RecipePartSignature] = []

    item_ingredients = spec.get("item_ingredients")
    item_amounts = spec.get("item_amounts")
    if not isinstance(item_ingredients, list) or not isinstance(item_amounts, list):
        raise BoundedFluidMachineError(
            f"hard-coded FLUID_RECIPES recipe {recipe_name!r} item ingredient facts are malformed"
        )
    if len(item_ingredients) != len(item_amounts):
        raise BoundedFluidMachineError(
            f"hard-coded FLUID_RECIPES recipe {recipe_name!r} item ingredient names and "
            "amounts disagree in length"
        )
    seen_ingredients: set[str] = set()
    for item, amount in zip(item_ingredients, item_amounts):
        name = _name(item, f"FLUID_RECIPES {recipe_name!r} item ingredient")
        value = _positive_number(
            amount, f"FLUID_RECIPES {recipe_name!r} item ingredient amount for {name!r}"
        )
        if name in seen_ingredients:
            raise BoundedFluidMachineError(
                f"hard-coded FLUID_RECIPES recipe {recipe_name!r} repeats ingredient {name!r}"
            )
        seen_ingredients.add(name)
        ingredient_parts.append(RecipePartSignature(name=name, type="item", amount=value))

    fluid_ingredients = spec.get("fluid_ingredients")
    if not isinstance(fluid_ingredients, Mapping):
        raise BoundedFluidMachineError(
            f"hard-coded FLUID_RECIPES recipe {recipe_name!r} fluid ingredients must be a mapping"
        )
    for part in _fixed_parts(recipe_name, fluid_ingredients, "fluid_ingredients"):
        if part.name in seen_ingredients:
            raise BoundedFluidMachineError(
                f"hard-coded FLUID_RECIPES recipe {recipe_name!r} repeats ingredient {part.name!r}"
            )
        seen_ingredients.add(part.name)
        ingredient_parts.append(part)

    item_products = spec.get("item_products")
    item_product_amounts = spec.get("item_product_amounts")
    if not isinstance(item_products, list) or not isinstance(item_product_amounts, Mapping):
        raise BoundedFluidMachineError(
            f"hard-coded FLUID_RECIPES recipe {recipe_name!r} item product facts are malformed"
        )
    extra_product_amounts = set(item_product_amounts) - set(item_products)
    if extra_product_amounts:
        names = ", ".join(sorted(map(repr, extra_product_amounts)))
        raise BoundedFluidMachineError(
            f"hard-coded FLUID_RECIPES recipe {recipe_name!r} has item product amounts "
            f"without products ({names})"
        )
    seen_products: set[str] = set()
    for item in item_products:
        name = _name(item, f"FLUID_RECIPES {recipe_name!r} item product")
        if name not in item_product_amounts:
            raise BoundedFluidMachineError(
                f"hard-coded FLUID_RECIPES recipe {recipe_name!r} has no amount for product {name!r}"
            )
        value = _positive_number(
            item_product_amounts[name],
            f"FLUID_RECIPES {recipe_name!r} item product amount for {name!r}",
        )
        if name in seen_products:
            raise BoundedFluidMachineError(
                f"hard-coded FLUID_RECIPES recipe {recipe_name!r} repeats product {name!r}"
            )
        seen_products.add(name)
        product_parts.append(RecipePartSignature(name=name, type="item", amount=value))

    fluid_products = spec.get("fluid_products")
    if not isinstance(fluid_products, Mapping):
        raise BoundedFluidMachineError(
            f"hard-coded FLUID_RECIPES recipe {recipe_name!r} fluid products must be a mapping"
        )
    for part in _fixed_parts(recipe_name, fluid_products, "fluid_products"):
        if part.name in seen_products:
            raise BoundedFluidMachineError(
                f"hard-coded FLUID_RECIPES recipe {recipe_name!r} repeats product {part.name!r}"
            )
        seen_products.add(part.name)
        product_parts.append(part)

    ingredients = tuple(sorted(ingredient_parts, key=lambda part: (part.name, part.type, part.amount)))
    products = tuple(sorted(product_parts, key=lambda part: (part.name, part.type, part.amount)))
    return _FixedRecipe(
        name=recipe_name,
        machine=machine,
        craft_seconds=craft_seconds,
        ingredients=ingredients,
        products=products,
    )


def _decision_rates(
    decisions: object,
    rates: object,
    decision_type: type,
    label: str,
) -> dict[str, Fraction]:
    if isinstance(decisions, (str, bytes)) or not isinstance(decisions, Sequence):
        raise BoundedFluidMachineError(f"balance {label} decisions must be a sequence")
    if isinstance(rates, (str, bytes)) or not isinstance(rates, Sequence):
        raise BoundedFluidMachineError(f"balance {label} rates must be a sequence")
    if len(decisions) != len(rates):
        raise BoundedFluidMachineError(
            f"balance {label} decisions and rates must have the same length"
        )
    values: dict[str, Fraction] = {}
    for position, (decision, raw_rate) in enumerate(zip(decisions, rates)):
        if not isinstance(decision, decision_type):
            raise BoundedFluidMachineError(
                f"balance {label}[{position}] must be a {decision_type.__name__}"
            )
        name = _name(decision.name if label == "recipes" else decision.material.name, f"balance {label}[{position}] name")
        if name in values:
            raise BoundedFluidMachineError(f"balance {label} repeats {name!r}")
        rate = _nonnegative_number(raw_rate, f"balance {label} rate for {name!r}")
        cap = _nonnegative_number(decision.cap, f"balance {label} cap for {name!r}")
        if rate > cap:
            raise BoundedFluidMachineError(
                f"balance {label} rate for {name!r} exceeds its explicit cap"
            )
        values[name] = rate
    return values


def _validate_balance(balance: object) -> BoundedRecipeBalanceResult:
    """Check only the caller's shape and asserted status; optimality is re-derived later."""
    if not isinstance(balance, BoundedRecipeBalanceResult):
        raise BoundedFluidMachineError("balance must be a BoundedRecipeBalanceResult")
    if balance.status != "optimal" or balance.optimal is not True:
        raise BoundedFluidMachineError(
            "balance must assert a completed optimal solve; "
            f"got status {balance.status!r} and optimal={balance.optimal!r}"
        )
    for field in (
        "objective_value",
        "recipe_rates",
        "source_rates",
        "disposal_rates",
        "material_net_rates",
    ):
        if getattr(balance, field) is None:
            raise BoundedFluidMachineError(
                f"optimal balance is missing {field}; refusing to size fluid machines"
            )
    return balance


def _reverify_optimality(
    catalog: object,
    balance: BoundedRecipeBalanceResult,
    *,
    max_candidate_solves: int,
) -> None:
    """Re-solve the same bounded model and prove the asserted objective is the optimum.

    The caller's ``status == "optimal"`` assertion is not trusted. This function
    rebuilds the exact request from the balance's own decisions, caps,
    coefficients, and demands, re-runs the exact bounded solver with an explicit
    candidate budget, and requires full enumeration. A feasible incumbent whose
    objective is below the independently re-derived optimum is rejected as
    suboptimal. A budget-exhausted re-solve is rejected because optimality was
    not proven.
    """
    if isinstance(max_candidate_solves, bool) or not isinstance(max_candidate_solves, int):
        raise BoundedFluidMachineError("verification_candidate_budget must be an int")
    if max_candidate_solves < 0 or max_candidate_solves > MAX_CANDIDATE_BUDGET:
        raise BoundedFluidMachineError(
            "verification_candidate_budget must be between 0 and "
            f"{MAX_CANDIDATE_BUDGET}"
        )
    try:
        rederived = optimize_bounded_recipe_balance(
            catalog,
            recipes={decision.name: decision.cap for decision in balance.recipes},
            sources={decision.material.name: decision.cap for decision in balance.sources},
            demand={demand.material.name: demand.rate for demand in balance.demands},
            disposals={
                decision.material.name: decision.cap for decision in balance.disposals
            },
            objective={
                "recipes": {
                    decision.name: decision.objective_coefficient
                    for decision in balance.recipes
                },
                "sources": {
                    decision.material.name: decision.objective_coefficient
                    for decision in balance.sources
                },
                "disposals": {
                    decision.material.name: decision.objective_coefficient
                    for decision in balance.disposals
                },
            },
            max_candidate_solves=max_candidate_solves,
        )
    except BoundedRecipeBalanceError as exc:
        raise BoundedFluidMachineError(
            f"independent re-solve of the asserted balance failed: {exc}"
        ) from exc
    if rederived.status == "budget_exhausted":
        raise BoundedFluidMachineError(
            "cannot independently re-derive optimality within the explicit verification "
            f"budget of {max_candidate_solves} candidate solves; increase "
            "verification_candidate_budget or decompose the model"
        )
    if rederived.status != "optimal" or rederived.objective_value is None:
        raise BoundedFluidMachineError(
            "independent re-solve did not find a feasible optimum; "
            f"got status {rederived.status!r}"
        )
    if rederived.objective_value != balance.objective_value:
        raise BoundedFluidMachineError(
            "caller-asserted optimum is feasible but suboptimal: asserted objective "
            f"{balance.objective_value}, independently re-derived optimum "
            f"{rederived.objective_value}"
        )


def _machine_speeds(
    values: object,
    recipe_names: tuple[str, ...],
) -> dict[str, Fraction]:
    if not isinstance(values, Mapping):
        raise BoundedFluidMachineError(
            "machine_speeds must be a mapping from each selected recipe to an explicit speed"
        )
    selected = set(recipe_names)
    speeds: dict[str, Fraction] = {}
    for raw_recipe, raw_speed in values.items():
        recipe = _name(raw_recipe, "machine_speeds recipe")
        if recipe not in selected:
            raise BoundedFluidMachineError(
                f"machine_speeds names unselected recipe {recipe!r}"
            )
        speeds[recipe] = _positive_number(raw_speed, f"machine speed for {recipe!r}")
    missing = sorted(selected - set(speeds))
    if missing:
        names = ", ".join(map(repr, missing))
        raise BoundedFluidMachineError(
            f"machine_speeds is incomplete; missing {names}"
        )
    return speeds


def _verify_consumed_or_produced(
    name: str,
    label: str,
    allowed: set[str],
) -> None:
    if name not in allowed:
        raise BoundedFluidMachineError(
            f"balance {label} {name!r} is not part of the selected recipe materials"
        )


def compute_bounded_fluid_machines(
    catalog: object,
    *,
    balance: object,
    machine_speeds: object,
    verification_candidate_budget: int = MAX_CANDIDATE_BUDGET,
) -> BoundedFluidMachineResult:
    """Turn one re-verified optimal bounded recipe balance into exact fluid-machine facts.

    The caller supplies the catalog used for the balance and an explicit machine
    speed for every selected recipe. This function first requires
    ``balance.status == "optimal"`` and ``balance.optimal is True`` as an
    assertion, then independently re-solves the exact bounded model rebuilt from
    the balance's own decisions under ``verification_candidate_budget``. It
    rejects infeasible, budget-exhausted, malformed, internally inconsistent, or
    feasible-but-suboptimal results. Every selected recipe must be a fully
    supported ``FLUID_RECIPES`` entry whose catalog signature, craft time,
    ingredients, products, and machine speed all match the hard-coded geometry
    facts. The catalog machine must accept the declared recipe category and
    ingredient count. Recipe effects, unsupported recipes, and nonzero disposal are rejected.
    The result reports exact machine counts, nominal rates, capacity shortfall,
    and per-fluid consumption, gross internal production, external source rates,
    final demand, and caps. It makes no source-site, power, pipeline,
    construction, or runtime claim.
    """
    checked = _validate_balance(balance)
    recipes = _recipe_index(catalog)
    machines = _machine_index(catalog)

    recipe_decisions = checked.recipes
    if isinstance(recipe_decisions, (str, bytes)) or not isinstance(recipe_decisions, Sequence):
        raise BoundedFluidMachineError("balance recipes must be a sequence")
    if not recipe_decisions:
        raise BoundedFluidMachineError("balance must select at least one recipe")
    for position, decision in enumerate(recipe_decisions):
        if not isinstance(decision, RecipeDecision):
            raise BoundedFluidMachineError(
                f"balance recipes[{position}] must be a RecipeDecision"
            )
        _name(decision.name, f"balance recipes[{position}] name")
    recipe_names = tuple(sorted(decision.name for decision in recipe_decisions))
    if len(set(recipe_names)) != len(recipe_names):
        raise BoundedFluidMachineError("balance recipes repeat a recipe name")
    recipe_rates = _decision_rates(
        recipe_decisions, checked.recipe_rates, RecipeDecision, "recipes"
    )
    source_decisions = checked.sources
    if isinstance(source_decisions, (str, bytes)) or not isinstance(source_decisions, Sequence):
        raise BoundedFluidMachineError("balance sources must be a sequence")
    for position, decision in enumerate(source_decisions):
        if not isinstance(decision, MaterialDecision):
            raise BoundedFluidMachineError(
                f"balance sources[{position}] must be a MaterialDecision"
            )
    source_rates = _decision_rates(
        source_decisions, checked.source_rates, MaterialDecision, "sources"
    )
    disposal_decisions = checked.disposals
    if isinstance(disposal_decisions, (str, bytes)) or not isinstance(disposal_decisions, Sequence):
        raise BoundedFluidMachineError("balance disposals must be a sequence")
    for position, decision in enumerate(disposal_decisions):
        if not isinstance(decision, MaterialDecision):
            raise BoundedFluidMachineError(
                f"balance disposals[{position}] must be a MaterialDecision"
            )
    disposal_rates = _decision_rates(
        disposal_decisions, checked.disposal_rates, MaterialDecision, "disposals"
    )
    nonzero_disposals = {name: rate for name, rate in disposal_rates.items() if rate != 0}
    if nonzero_disposals:
        names = ", ".join(sorted(nonzero_disposals))
        raise BoundedFluidMachineError(
            "nonzero disposal is unsupported by the fluid-machine adapter "
            f"({names})"
        )

    explicit_speeds = _machine_speeds(machine_speeds, recipe_names)

    fixed_by_recipe: dict[str, _FixedRecipe] = {}
    catalog_signatures: dict[str, RecipeSignature] = {}
    material_kinds: dict[str, str] = {}
    produced: set[str] = set()
    consumed: set[str] = set()
    for name in recipe_names:
        recipe = recipes.get(name)
        if recipe is None:
            raise BoundedFluidMachineError(f"selected recipe {name!r} is not in the catalog")
        if recipe.get("enabled") is not True:
            raise BoundedFluidMachineError(f"selected recipe {name!r} is not enabled")
        if recipe.get("supported") is not True:
            detail = recipe.get("unsupported_reason")
            suffix = f": {detail}" if isinstance(detail, str) and detail else ""
            raise BoundedFluidMachineError(
                f"selected recipe {name!r} is not supported{suffix}"
            )

        fixed = _fixed_recipe(name)
        catalog_signature = _catalog_signature(name, recipe)
        if catalog_signature.ingredients != fixed.ingredients:
            raise BoundedFluidMachineError(
                f"catalog recipe {name!r} ingredients disagree with hard-coded FLUID_RECIPES facts "
                f"(expected {fixed.ingredients!r}, got {catalog_signature.ingredients!r})"
            )
        if catalog_signature.products != fixed.products:
            raise BoundedFluidMachineError(
                f"catalog recipe {name!r} products disagree with hard-coded FLUID_RECIPES facts "
                f"(expected {fixed.products!r}, got {catalog_signature.products!r})"
            )

        energy_ticks = _positive_number(
            recipe.get("energy_ticks"), f"catalog recipe {name!r} energy_ticks"
        )
        catalog_craft_seconds = energy_ticks / 60
        if catalog_craft_seconds != fixed.craft_seconds:
            raise BoundedFluidMachineError(
                f"catalog recipe {name!r} craft time disagrees with hard-coded FLUID_RECIPES: "
                f"catalog {catalog_craft_seconds}s from {energy_ticks} ticks, "
                f"hard-coded {fixed.craft_seconds}s"
            )

        expected_speed = _exact_number(
            FLUID_MACHINE_SPEEDS[fixed.machine],
            f"FLUID_MACHINE_SPEEDS value for {fixed.machine!r}",
        )
        machine = machines.get(fixed.machine)
        if machine is None:
            raise BoundedFluidMachineError(
                f"catalog has no machine {fixed.machine!r} required by recipe {name!r}"
            )
        category = _name(recipe.get("category"), f"catalog recipe {name!r} category")
        try:
            categories = recipe_categories(recipe, f"catalog recipe {name!r}")
        except ValueError as error:
            raise BoundedFluidMachineError(str(error)) from error
        if machine.type != "assembling-machine":
            raise BoundedFluidMachineError(
                f"catalog machine {fixed.machine!r} must be an assembling-machine"
            )
        if not categories.intersection(machine.categories):
            raise BoundedFluidMachineError(
                f"catalog machine {fixed.machine!r} does not support recipe category {category!r} "
                f"required by {name!r}"
            )
        if len(catalog_signature.ingredients) > machine.ingredient_count:
            raise BoundedFluidMachineError(
                f"catalog machine {fixed.machine!r} has {machine.ingredient_count} ingredient slots "
                f"but recipe {name!r} declares {len(catalog_signature.ingredients)} ingredients"
            )
        if machine.crafting_speed != expected_speed:
            raise BoundedFluidMachineError(
                f"catalog machine {fixed.machine!r} crafting_speed {machine.crafting_speed} "
                f"disagrees with hard-coded FLUID_MACHINE_SPEEDS value {expected_speed}"
            )
        explicit_speed = explicit_speeds[name]
        if explicit_speed != expected_speed:
            raise BoundedFluidMachineError(
                f"explicit machine speed {explicit_speed} for recipe {name!r} disagrees with "
                f"hard-coded FLUID_MACHINE_SPEEDS value {expected_speed} for {fixed.machine!r}"
            )
        if explicit_speed != machine.crafting_speed:
            raise BoundedFluidMachineError(
                f"explicit machine speed {explicit_speed} for recipe {name!r} disagrees with "
                f"catalog machine {fixed.machine!r} crafting_speed {machine.crafting_speed}"
            )

        stored_signature = next(
            (decision.signature for decision in recipe_decisions if decision.name == name),
            None,
        )
        if not isinstance(stored_signature, RecipeSignature):
            raise BoundedFluidMachineError(
                f"balance provenance for recipe {name!r} is missing or malformed"
            )
        if stored_signature != catalog_signature:
            raise BoundedFluidMachineError(
                f"balance provenance for recipe {name!r} does not match the current catalog; "
                "the balance was not computed from this catalog, or its recipe parts changed"
            )

        fixed_by_recipe[name] = fixed
        catalog_signatures[name] = catalog_signature
        for part in (*catalog_signature.ingredients, *catalog_signature.products):
            previous = material_kinds.setdefault(part.name, part.type)
            if previous != part.type:
                raise BoundedFluidMachineError(
                    f"material {part.name!r} is declared as both {previous} and {part.type} "
                    "by selected recipes"
                )
        consumed.update(part.name for part in catalog_signature.ingredients)
        produced.update(part.name for part in catalog_signature.products)

    for decision in source_decisions:
        _verify_consumed_or_produced(decision.material.name, "source", consumed)
        if material_kinds[decision.material.name] != decision.material.type:
            raise BoundedFluidMachineError(
                f"balance source {decision.material.name!r} type {decision.material.type!r} "
                f"disagrees with catalog type {material_kinds[decision.material.name]!r}"
            )
    for decision in disposal_decisions:
        _verify_consumed_or_produced(decision.material.name, "disposal", produced)
        if material_kinds[decision.material.name] != decision.material.type:
            raise BoundedFluidMachineError(
                f"balance disposal {decision.material.name!r} type {decision.material.type!r} "
                f"disagrees with catalog type {material_kinds[decision.material.name]!r}"
            )
    if isinstance(checked.demands, (str, bytes)) or not isinstance(checked.demands, Sequence):
        raise BoundedFluidMachineError("balance demands must be a sequence")
    for demand in checked.demands:
        if not isinstance(demand, MaterialDemand):
            raise BoundedFluidMachineError(
                "balance demands must contain MaterialDemand entries"
            )
        _verify_consumed_or_produced(demand.material.name, "demand", produced)
        if material_kinds[demand.material.name] != demand.material.type:
            raise BoundedFluidMachineError(
                f"balance demand {demand.material.name!r} type {demand.material.type!r} "
                f"disagrees with catalog type {material_kinds[demand.material.name]!r}"
            )

    expected_identities = tuple(
        sorted(
            (
                MaterialIdentity(name=name, type=kind)
                for name, kind in material_kinds.items()
            ),
            key=lambda material: (material.name, material.type),
        )
    )
    if checked.materials != expected_identities:
        raise BoundedFluidMachineError(
            "balance materials do not match the selected recipe materials; "
            "the result is malformed or inconsistent"
        )

    net_rates = checked.material_net_rates
    if isinstance(net_rates, (str, bytes)) or not isinstance(net_rates, Sequence):
        raise BoundedFluidMachineError("balance material_net_rates must be a sequence")
    if len(net_rates) != len(checked.materials):
        raise BoundedFluidMachineError(
            "balance material_net_rates must align with balance materials"
        )
    demand_rates: dict[str, Fraction] = {}
    for demand in checked.demands:
        if demand.material.name in demand_rates:
            raise BoundedFluidMachineError(
                f"balance demands repeat {demand.material.name!r}"
            )
        demand_rates[demand.material.name] = _nonnegative_number(
            demand.rate, f"balance demand rate for {demand.material.name!r}"
        )
    expected_net: dict[str, Fraction] = {}
    for material, raw_rate in zip(checked.materials, net_rates):
        rate = _exact_number(raw_rate, f"balance net rate for {material.name!r}")
        expected = demand_rates.get(material.name, Fraction(0))
        if rate != expected:
            raise BoundedFluidMachineError(
                f"balance net rate for {material.name!r} is {rate}, expected {expected}; "
                "the result is inconsistent"
            )
        expected_net[material.name] = rate

    for material in expected_identities:
        name = material.name
        net = Fraction(0)
        for recipe_name in recipe_names:
            signature = catalog_signatures[recipe_name]
            produced_amount = sum(
                (part.amount for part in signature.products if part.name == name),
                Fraction(0),
            )
            consumed_amount = sum(
                (part.amount for part in signature.ingredients if part.name == name),
                Fraction(0),
            )
            net += recipe_rates[recipe_name] * (produced_amount - consumed_amount)
        net += source_rates.get(name, Fraction(0))
        net -= disposal_rates.get(name, Fraction(0))
        expected = demand_rates.get(name, Fraction(0))
        if net != expected:
            raise BoundedFluidMachineError(
                f"balance rates do not satisfy the exact material equation for {name!r}: "
                f"computed net {net}, reported demand {expected}"
            )
        if net != expected_net[name]:
            raise BoundedFluidMachineError(
                f"balance net rate for {name!r} is {expected_net[name]}, "
                f"computed {net}; the result is inconsistent"
            )

    objective_value = _exact_number(checked.objective_value, "balance objective_value")
    objective_total = Fraction(0)
    for decision in recipe_decisions:
        objective_total += (
            _exact_number(decision.objective_coefficient, "recipe objective coefficient")
            * recipe_rates[decision.name]
        )
    for decision in source_decisions:
        objective_total += (
            _exact_number(decision.objective_coefficient, "source objective coefficient")
            * source_rates[decision.material.name]
        )
    for decision in disposal_decisions:
        objective_total += (
            _exact_number(decision.objective_coefficient, "disposal objective coefficient")
            * disposal_rates[decision.material.name]
        )
    if objective_total != objective_value:
        raise BoundedFluidMachineError(
            f"balance objective_value {objective_value} does not equal the decoded "
            f"objective {objective_total}; the result is inconsistent"
        )

    _reverify_optimality(
        catalog,
        checked,
        max_candidate_solves=verification_candidate_budget,
    )

    facts: list[BoundedFluidMachineFact] = []
    for name in recipe_names:
        fixed = fixed_by_recipe[name]
        required = recipe_rates[name]
        speed = explicit_speeds[name]
        exact_machines = required * fixed.craft_seconds / speed
        machine_count = _ceil_fraction(exact_machines)
        nominal = Fraction(machine_count) * speed / fixed.craft_seconds
        spare = nominal - required
        shortfall = required - nominal
        if shortfall < 0:
            shortfall = Fraction(0)
        facts.append(
            BoundedFluidMachineFact(
                recipe=name,
                machine=fixed.machine,
                required_crafts_per_second=required,
                craft_seconds=fixed.craft_seconds,
                machine_speed=speed,
                exact_machines=exact_machines,
                machine_count=machine_count,
                nominal_crafts_per_second=nominal,
                spare_crafts_per_second=spare,
                capacity_shortfall_crafts_per_second=shortfall,
            )
        )

    fluid_consumption: dict[str, Fraction] = {}
    fluid_production: dict[str, Fraction] = {}
    for name in recipe_names:
        fixed = fixed_by_recipe[name]
        rate = recipe_rates[name]
        for part in fixed.ingredients:
            if part.type != "fluid":
                continue
            fluid_consumption[part.name] = (
                fluid_consumption.get(part.name, Fraction(0)) + rate * part.amount
            )
        for part in fixed.products:
            if part.type != "fluid":
                continue
            fluid_production[part.name] = (
                fluid_production.get(part.name, Fraction(0)) + rate * part.amount
            )

    fluid_source_rates: dict[str, Fraction] = {}
    fluid_source_caps: dict[str, Fraction] = {}
    for decision in source_decisions:
        if decision.material.type != "fluid":
            continue
        name = decision.material.name
        fluid_source_rates[name] = source_rates[name]
        fluid_source_caps[name] = _nonnegative_number(
            decision.cap, f"source cap for {name!r}"
        )

    fluid_demand_rates: dict[str, Fraction] = {}
    for demand in checked.demands:
        if demand.material.type != "fluid":
            continue
        fluid_demand_rates[demand.material.name] = demand_rates[demand.material.name]

    fluid_disposal_rates: dict[str, Fraction] = {}
    for decision in disposal_decisions:
        if decision.material.type != "fluid":
            continue
        fluid_disposal_rates[decision.material.name] = disposal_rates[
            decision.material.name
        ]

    fluid_names = (
        set(fluid_consumption)
        | set(fluid_production)
        | set(fluid_source_rates)
        | set(fluid_demand_rates)
        | set(fluid_disposal_rates)
    )
    fluid_sources: list[FluidSourceDemand] = []
    for fluid in sorted(fluid_names):
        consumption = fluid_consumption.get(fluid, Fraction(0))
        production = fluid_production.get(fluid, Fraction(0))
        external = fluid_source_rates.get(fluid, Fraction(0))
        demand = fluid_demand_rates.get(fluid, Fraction(0))
        disposal = fluid_disposal_rates.get(fluid, Fraction(0))
        if production + external != consumption + demand + disposal:
            raise BoundedFluidMachineError(
                f"fluid balance for {fluid!r} is inconsistent: internal production "
                f"{production} + external source {external} does not equal machine "
                f"consumption {consumption} + final demand {demand} + disposal "
                f"{disposal}"
            )
        cap = fluid_source_caps.get(fluid)
        if cap is not None and external > cap:
            raise BoundedFluidMachineError(
                f"fluid source rate for {fluid!r} exceeds its explicit cap "
                f"({external} > {cap})"
            )
        fluid_sources.append(
            FluidSourceDemand(
                fluid=fluid,
                consumption_rate=consumption,
                external_source_rate=external,
                internally_produced_rate=production,
                final_demand_rate=demand,
                cap=cap,
                shortfall_rate=Fraction(0),
            )
        )

    return BoundedFluidMachineResult(
        facts=tuple(facts),
        total_machines=sum(fact.machine_count for fact in facts),
        fluid_sources=tuple(fluid_sources),
    )
