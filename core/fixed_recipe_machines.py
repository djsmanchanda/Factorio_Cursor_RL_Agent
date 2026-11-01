# Path: core/fixed_recipe_machines.py
# Purpose: Turn one feasible unique fixed-recipe balance into exact craft rates and rounded-up machine facts.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from math import isfinite
from typing import Literal

from core.fixed_recipe_balance import FixedRecipeBalanceResult, RecipeSignature
from core.recipe_categories import recipe_categories

_MACHINE_TYPES = frozenset({"assembling-machine", "furnace"})
_MACHINE_FIELDS = frozenset({"name", "type", "ingredient_count", "crafting_speed", "categories"})
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

# A catalog that claims any of these would change the speed computation. This
# module models only energy_ticks and crafting_speed, so it fails closed instead
# of silently ignoring a claimed module, productivity, quality, or power effect.
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

UNMODELLED_EFFECTS = (
    "modules and beacons are not modelled",
    "productivity bonuses are not modelled",
    "quality and quality bonuses are not modelled",
    "feeder, inserter, belt, pipe, transport and power limits are not modelled",
    "only energy_ticks and crafting_speed affect the reported facts",
    "energy_ticks is catalog tick-rounded data and may not reflect actual craft time",
)


class FixedRecipeMachineError(ValueError):
    """The balance, catalog, or mapping cannot support an exact machine computation."""


@dataclass(frozen=True)
class FixedRecipeMachineFact:
    """Exact machine facts for one recipe at a caller-chosen machine.

    ``craft_seconds_per_second`` is required craft work per wall second
    (``rate * energy_ticks / 60``). ``energy_ticks`` is catalog tick-rounded
    data, so these durations may not reflect the game's actual craft time.
    ``exact_machines`` divides that work by the machine's exported
    ``crafting_speed``; ``machine_count`` is its ceiling.
    ``nominal_crafts_per_second`` and ``spare_crafts_per_second`` describe the
    rounded-up count as facts, not a recommendation.
    """

    recipe: str
    machine: str
    required_crafts_per_second: Fraction
    energy_ticks: Fraction
    base_craft_seconds: Fraction
    craft_seconds_per_second: Fraction
    crafting_speed: Fraction
    exact_machines: Fraction
    machine_count: int
    nominal_crafts_per_second: Fraction
    spare_crafts_per_second: Fraction
    spare_machine_equivalents: Fraction
    ingredient_count: int


@dataclass(frozen=True)
class FixedRecipeMachineResult:
    """Facts for every selected recipe in deterministic recipe-name order."""

    facts: tuple[FixedRecipeMachineFact, ...]
    total_machines: int
    unmodelled_effects: tuple[str, ...] = UNMODELLED_EFFECTS


@dataclass(frozen=True)
class _Machine:
    name: str
    type: str
    ingredient_count: int
    crafting_speed: Fraction
    categories: frozenset[str]


@dataclass(frozen=True)
class _PreparedRecipe:
    name: str
    machine: _Machine
    ingredient_count: int
    energy_ticks: Fraction
    ingredients: tuple[tuple[str, str, Fraction], ...]
    products: tuple[tuple[str, str, Fraction], ...]
    rate: Fraction


def _exact_number(value: object, label: str) -> Fraction:
    if isinstance(value, bool):
        raise FixedRecipeMachineError(f"{label} must be a finite number, not a boolean")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if not isfinite(value):
            raise FixedRecipeMachineError(f"{label} must be finite")
        return Fraction(Decimal(str(value)))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise FixedRecipeMachineError(f"{label} must be finite")
        return Fraction(value)
    raise FixedRecipeMachineError(f"{label} must be an int, Fraction, float, or Decimal")


def _positive_number(value: object, label: str) -> Fraction:
    number = _exact_number(value, label)
    if number <= 0:
        raise FixedRecipeMachineError(f"{label} must be positive")
    return number


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise FixedRecipeMachineError(f"{label} must be a nonempty string")
    return value


def _recipe_index(catalog: object) -> dict[str, Mapping]:
    if not isinstance(catalog, Mapping):
        raise FixedRecipeMachineError("catalog must be a mapping")
    recipes = catalog.get("recipes")
    if not isinstance(recipes, list):
        raise FixedRecipeMachineError("catalog must contain a recipes list")
    index: dict[str, Mapping] = {}
    for position, recipe in enumerate(recipes):
        if not isinstance(recipe, Mapping):
            raise FixedRecipeMachineError(f"catalog recipe at index {position} must be a mapping")
        name = _name(recipe.get("name"), f"catalog recipe at index {position} name")
        if name in index:
            raise FixedRecipeMachineError(f"catalog defines ambiguous duplicate recipe {name!r}")
        index[name] = recipe
    return index


def _machine_index(catalog: Mapping) -> dict[str, _Machine]:
    raw_machines = catalog.get("machines")
    if not isinstance(raw_machines, list) or not raw_machines:
        raise FixedRecipeMachineError("catalog must contain a nonempty machines list")
    index: dict[str, _Machine] = {}
    for position, machine in enumerate(raw_machines):
        if not isinstance(machine, Mapping):
            raise FixedRecipeMachineError(f"catalog machine at index {position} must be a mapping")
        name = _name(machine.get("name"), f"catalog machine at index {position} name")
        if name in index:
            raise FixedRecipeMachineError(f"catalog defines ambiguous duplicate machine {name!r}")
        effect_fields = sorted(set(machine) & _UNSUPPORTED_EFFECT_FIELDS)
        if effect_fields:
            fields = ", ".join(map(repr, effect_fields))
            raise FixedRecipeMachineError(
                f"machine {name!r} claims unsupported effects ({fields})"
            )
        extra = sorted(set(machine) - _MACHINE_FIELDS)
        if extra:
            fields = ", ".join(map(repr, extra))
            raise FixedRecipeMachineError(
                f"machine {name!r} has unsupported fields ({fields})"
            )
        kind = machine.get("type")
        if kind not in _MACHINE_TYPES:
            raise FixedRecipeMachineError(
                f"machine {name!r} has unsupported type {kind!r}"
            )
        slots = machine.get("ingredient_count")
        if isinstance(slots, bool) or not isinstance(slots, int) or slots < 0:
            raise FixedRecipeMachineError(
                f"machine {name!r} ingredient_count must be a nonnegative integer"
            )
        speed = _positive_number(machine.get("crafting_speed"), f"machine {name!r} crafting_speed")
        raw_categories = machine.get("categories")
        if not isinstance(raw_categories, list):
            raise FixedRecipeMachineError(f"machine {name!r} categories must be a list")
        categories: list[str] = []
        for category_index, category in enumerate(raw_categories):
            categories.append(
                _name(category, f"machine {name!r} categories[{category_index}]")
            )
        index[name] = _Machine(
            name=name,
            type=kind,
            ingredient_count=slots,
            crafting_speed=speed,
            categories=frozenset(categories),
        )
    return index


def _recipe_names(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise FixedRecipeMachineError("balance recipe_names must be a non-string sequence")
    names = tuple(values)
    if not names:
        raise FixedRecipeMachineError("balance must select at least one recipe")
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not name:
            raise FixedRecipeMachineError("balance recipe names must be nonempty strings")
        if name in seen:
            raise FixedRecipeMachineError(f"balance recipe {name!r} is repeated")
        seen.add(name)
    return names


def _parts(
    recipe_name: str,
    recipe: Mapping,
    side: Literal["ingredients", "products"],
) -> tuple[tuple[str, str, Fraction], ...]:
    parts = recipe.get(side)
    if not isinstance(parts, list):
        raise FixedRecipeMachineError(f"recipe {recipe_name!r} {side} must be a list")
    parsed: list[tuple[str, str, Fraction]] = []
    seen: set[str] = set()
    for index, part in enumerate(parts):
        if not isinstance(part, Mapping):
            raise FixedRecipeMachineError(
                f"recipe {recipe_name!r} {side}[{index}] must be a mapping"
            )
        extra = set(part) - {"name", "type", "amount"}
        if extra:
            fields = ", ".join(sorted(map(repr, extra)))
            raise FixedRecipeMachineError(
                f"recipe {recipe_name!r} {side}[{index}] has unsupported fields ({fields})"
            )
        name = _name(part.get("name"), f"recipe {recipe_name!r} {side}[{index}] name")
        kind = part.get("type")
        if kind not in ("item", "fluid"):
            raise FixedRecipeMachineError(
                f"recipe {recipe_name!r} {side}[{index}] has unsupported type {kind!r}"
            )
        amount = _positive_number(
            part.get("amount"), f"recipe {recipe_name!r} {side}[{index}] amount"
        )
        if name in seen:
            raise FixedRecipeMachineError(f"recipe {recipe_name!r} repeats {name!r} in {side}")
        seen.add(name)
        parsed.append((name, kind, amount))
    return tuple(parsed)


def _ceil_fraction(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


def _checked_rates(balance: FixedRecipeBalanceResult, names: tuple[str, ...]) -> dict[str, Fraction]:
    if balance.status == "underdetermined":
        raise FixedRecipeMachineError(
            "underdetermined balance has no unique recipe rates; machine counts are not computed"
        )
    if balance.status == "inconsistent":
        raise FixedRecipeMachineError(
            "inconsistent balance has no recipe rates; machine counts are not computed"
        )
    if balance.status != "unique":
        raise FixedRecipeMachineError(f"unsupported balance status {balance.status!r}")
    if balance.feasible is not True:
        if balance.shortages:
            raise FixedRecipeMachineError(
                "source-cap-short unique balance cannot size machines; the solution is unchanged"
            )
        raise FixedRecipeMachineError(
            "unique balance is not feasible (negative exact rates); machine counts are not computed"
        )
    if balance.shortages:
        raise FixedRecipeMachineError(
            "unique balance is marked feasible but reports source-cap shortages"
        )
    rates = balance.rates
    if rates is None:
        raise FixedRecipeMachineError("unique balance must include exact rates")
    if len(rates) != len(names):
        raise FixedRecipeMachineError(
            "balance rates must align with balance recipe_names"
        )
    exact: dict[str, Fraction] = {}
    for name, value in zip(names, rates):
        rate = _exact_number(value, f"balance rate for {name!r}")
        if rate < 0:
            raise FixedRecipeMachineError(
                f"balance rate for {name!r} must not be negative"
            )
        exact[name] = rate
    return exact


def _machine_by_recipe(values: object, names: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(values, Mapping):
        raise FixedRecipeMachineError(
            "machine_by_recipe must be a mapping of selected recipe names to machine names"
        )
    selected = set(names)
    mapping: dict[str, str] = {}
    for recipe, machine in values.items():
        name = _name(recipe, "machine_by_recipe key")
        mapping[name] = _name(machine, f"machine for recipe {name!r}")
    missing = sorted(selected - set(mapping))
    if missing:
        raise FixedRecipeMachineError(
            "machine_by_recipe is incomplete; missing " + ", ".join(map(repr, missing))
        )
    extra = sorted(set(mapping) - selected)
    if extra:
        raise FixedRecipeMachineError(
            "machine_by_recipe maps unselected recipes " + ", ".join(map(repr, extra))
        )
    return mapping


def _verify_signatures(
    balance: FixedRecipeBalanceResult,
    names: tuple[str, ...],
    prepared: Mapping[str, _PreparedRecipe],
) -> None:
    """Recompute provenance from the current catalog and require an exact match.

    A balance carries the normalized parts it actually solved. Comparing those
    signatures to the current catalog prevents sizing machines from a catalog
    that reuses recipe names but changes an ingredient, product, part type, or
    exact amount.
    """
    stored = balance.recipe_signatures
    if not isinstance(stored, tuple):
        raise FixedRecipeMachineError(
            "balance recipe provenance must be a tuple of RecipeSignature entries"
        )
    if not stored:
        raise FixedRecipeMachineError(
            "balance has no recipe provenance signatures; "
            "recompute it with solve_fixed_recipe_balance"
        )
    by_recipe: dict[str, RecipeSignature] = {}
    for signature in stored:
        if not isinstance(signature, RecipeSignature):
            raise FixedRecipeMachineError(
                "balance recipe provenance must contain only RecipeSignature entries"
            )
        if signature.recipe in by_recipe:
            raise FixedRecipeMachineError(
                f"balance recipe provenance repeats recipe {signature.recipe!r}"
            )
        by_recipe[signature.recipe] = signature
    if set(by_recipe) != set(names):
        missing = sorted(set(names) - set(by_recipe))
        extra = sorted(set(by_recipe) - set(names))
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(map(repr, missing)))
        if extra:
            details.append("unexpected " + ", ".join(map(repr, extra)))
        raise FixedRecipeMachineError(
            "balance recipe provenance does not align with balance recipe_names ("
            + "; ".join(details)
            + ")"
        )
    for name in names:
        recipe = prepared[name]
        expected = RecipeSignature.from_parts(
            name, recipe.ingredients, recipe.products
        )
        if by_recipe[name] != expected:
            raise FixedRecipeMachineError(
                f"balance provenance for recipe {name!r} does not match the current catalog; "
                "ingredients, products, part types, or exact amounts changed"
            )


def compute_fixed_recipe_machines(
    catalog: object,
    *,
    balance: FixedRecipeBalanceResult,
    machine_by_recipe: object,
) -> FixedRecipeMachineResult:
    """Report exact machine facts for one explicitly selected feasible balance.

    The caller chooses every machine. This function does not select tiers and
    does not assume modules, productivity bonuses, quality, feeders, transport,
    or power. Ingredient capacity counts every declared ingredient entry,
    including fluids, matching the existing catalog's ``len(ingredients)``
    semantics. A positive nominal rate is not runtime or transport feasibility.

    Catalog ``energy_ticks`` is exporter tick-rounded data: the reported craft
    durations and machine counts are exact for that catalog value but may not
    match the game's actual craft time. The balance's recipe provenance is
    independently recomputed from this catalog before any fact is produced.
    """
    if not isinstance(balance, FixedRecipeBalanceResult):
        raise FixedRecipeMachineError("balance must be a FixedRecipeBalanceResult")
    names = _recipe_names(balance.recipe_names)
    rates = _checked_rates(balance, names)
    recipes = _recipe_index(catalog)
    machines = _machine_index(catalog)
    mapping = _machine_by_recipe(machine_by_recipe, names)

    prepared: dict[str, _PreparedRecipe] = {}
    for name in sorted(names):
        recipe = recipes.get(name)
        if recipe is None:
            raise FixedRecipeMachineError(f"balance recipe {name!r} is not in the catalog")
        if recipe.get("enabled") is not True:
            raise FixedRecipeMachineError(f"balance recipe {name!r} is not enabled")
        if recipe.get("supported") is not True:
            detail = recipe.get("unsupported_reason")
            suffix = f": {detail}" if isinstance(detail, str) and detail else ""
            raise FixedRecipeMachineError(
                f"balance recipe {name!r} is not supported{suffix}"
            )
        effect_fields = sorted(set(recipe) & _UNSUPPORTED_EFFECT_FIELDS)
        if effect_fields:
            fields = ", ".join(map(repr, effect_fields))
            raise FixedRecipeMachineError(f"recipe {name!r} claims unsupported effects ({fields})")
        extra = sorted(set(recipe) - _RECIPE_FIELDS)
        if extra:
            fields = ", ".join(map(repr, extra))
            raise FixedRecipeMachineError(f"recipe {name!r} has unsupported fields ({fields})")
        category = _name(recipe.get("category"), f"recipe {name!r} category")
        try:
            categories = recipe_categories(recipe, f"recipe {name!r}")
        except ValueError as error:
            raise FixedRecipeMachineError(str(error)) from error
        energy_ticks = _positive_number(recipe.get("energy_ticks"), f"recipe {name!r} energy_ticks")
        ingredients = _parts(name, recipe, "ingredients")
        products = _parts(name, recipe, "products")
        if not products:
            raise FixedRecipeMachineError(f"recipe {name!r} has no deterministic products")

        machine_name = mapping[name]
        machine = machines.get(machine_name)
        if machine is None:
            raise FixedRecipeMachineError(
                f"recipe {name!r} maps to machine {machine_name!r}, which is not in the catalog"
            )
        if not categories.intersection(machine.categories):
            raise FixedRecipeMachineError(
                f"machine {machine_name!r} does not support recipe category {category!r} "
                f"required by {name!r}"
            )
        # Items and fluids are both ingredient entries; this matches the
        # catalog semantics used by machine_holds (len(ingredients)).
        ingredient_count = len(ingredients)
        if ingredient_count > machine.ingredient_count:
            raise FixedRecipeMachineError(
                f"machine {machine_name!r} has {machine.ingredient_count} ingredient slots "
                f"but recipe {name!r} declares {ingredient_count} ingredients"
            )

        prepared[name] = _PreparedRecipe(
            name=name,
            machine=machine,
            ingredient_count=ingredient_count,
            energy_ticks=energy_ticks,
            ingredients=ingredients,
            products=products,
            rate=rates[name],
        )

    _verify_signatures(balance, names, prepared)

    facts: list[FixedRecipeMachineFact] = []
    for name in sorted(names):
        recipe = prepared[name]
        machine = recipe.machine
        rate = recipe.rate
        base_craft_seconds = recipe.energy_ticks / 60
        craft_seconds_per_second = rate * base_craft_seconds
        exact_machines = craft_seconds_per_second / machine.crafting_speed
        machine_count = _ceil_fraction(exact_machines)
        nominal_crafts_per_second = (
            Fraction(machine_count) * machine.crafting_speed / base_craft_seconds
        )
        facts.append(
            FixedRecipeMachineFact(
                recipe=name,
                machine=machine.name,
                required_crafts_per_second=rate,
                energy_ticks=recipe.energy_ticks,
                base_craft_seconds=base_craft_seconds,
                craft_seconds_per_second=craft_seconds_per_second,
                crafting_speed=machine.crafting_speed,
                exact_machines=exact_machines,
                machine_count=machine_count,
                nominal_crafts_per_second=nominal_crafts_per_second,
                spare_crafts_per_second=nominal_crafts_per_second - rate,
                spare_machine_equivalents=Fraction(machine_count) - exact_machines,
                ingredient_count=recipe.ingredient_count,
            )
        )
    return FixedRecipeMachineResult(
        facts=tuple(facts),
        total_machines=sum(fact.machine_count for fact in facts),
    )
