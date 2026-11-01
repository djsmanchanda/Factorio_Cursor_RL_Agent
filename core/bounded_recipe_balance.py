# Path: core/bounded_recipe_balance.py
# Purpose: Optimise an explicitly bounded catalog recipe/source/disposal set with an exact material balance.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from math import isfinite
from typing import Literal

from core.bounded_linear import (
    DEFAULT_CANDIDATE_BUDGET,
    MAX_CANDIDATE_BUDGET,
    solve_bounded_linear,
)
from core.fixed_recipe_balance import RecipeSignature
from core.recipe_categories import recipe_categories

BoundedBalanceStatus = Literal["optimal", "infeasible", "budget_exhausted"]

# Recipe fields present in the repository's exported catalog contract. Any
# other field, including probability or temperature, is rejected so a caller
# cannot silently model unsupported mechanics.
_ALLOWED_RECIPE_FIELDS = frozenset(
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

NON_CLAIMS = (
    "This result is an exact material-balance optimum only; it makes no machine, "
    "power, transport, geometry, construction, or runtime feasibility claim.",
    "Raw resources exist only through the caller's explicitly bounded sources.",
    "Disposal exists only through the caller's explicitly bounded disposal outputs.",
    "Strategy exists only in the caller's explicit objective coefficients.",
)


class BoundedRecipeBalanceError(ValueError):
    """The request is malformed, unsupported, or ambiguous, so no model was solved."""


@dataclass(frozen=True, order=True)
class MaterialIdentity:
    """One material name with its item/fluid type retained."""

    name: str
    type: str


@dataclass(frozen=True)
class MaterialDemand:
    """One fixed net output rate for an identified material."""

    material: MaterialIdentity
    rate: Fraction


@dataclass(frozen=True)
class RecipeDecision:
    """One caller-selected recipe decision with its bound and objective coefficient."""

    name: str
    cap: Fraction
    objective_coefficient: Fraction
    signature: RecipeSignature


@dataclass(frozen=True)
class MaterialDecision:
    """One caller-selected source or disposal decision for a material."""

    material: MaterialIdentity
    cap: Fraction
    objective_coefficient: Fraction


@dataclass(frozen=True)
class BoundedRecipeBalanceResult:
    """Exact bounded material-balance result.

    ``status`` mirrors ``core.bounded_linear``. ``optimal`` is ``True`` only when
    that solver completed its full candidate enumeration; a
    ``budget_exhausted`` incumbent is feasible for this exact model but is never
    claimed optimal. ``material_net_rates`` is aligned with ``materials`` and is
    populated only when a candidate was decoded and independently verified
    against the material equations and caps.
    """

    status: BoundedBalanceStatus
    optimal: bool
    objective_value: Fraction | None
    recipes: tuple[RecipeDecision, ...]
    sources: tuple[MaterialDecision, ...]
    disposals: tuple[MaterialDecision, ...]
    demands: tuple[MaterialDemand, ...]
    materials: tuple[MaterialIdentity, ...]
    recipe_rates: tuple[Fraction, ...] | None
    source_rates: tuple[Fraction, ...] | None
    disposal_rates: tuple[Fraction, ...] | None
    material_net_rates: tuple[Fraction, ...] | None
    candidates_evaluated: int
    candidate_budget: int
    reason: str


def _exact(value: object, label: str) -> Fraction:
    if isinstance(value, bool):
        raise BoundedRecipeBalanceError(f"{label} must be a finite number, not a boolean")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if not isfinite(value):
            raise BoundedRecipeBalanceError(f"{label} must be finite")
        return Fraction(Decimal(str(value)))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise BoundedRecipeBalanceError(f"{label} must be finite")
        return Fraction(value)
    raise BoundedRecipeBalanceError(f"{label} must be an int, Fraction, float, or Decimal")


def _bounded_decisions(values: object, label: str) -> dict[str, Fraction]:
    if not isinstance(values, Mapping):
        raise BoundedRecipeBalanceError(f"{label} must be a mapping of names to explicit bounds")
    bounds: dict[str, Fraction] = {}
    for name, value in values.items():
        if not isinstance(name, str) or not name:
            raise BoundedRecipeBalanceError(f"{label} keys must be nonempty strings")
        cap = _exact(value, f"{label} bound for {name!r}")
        if cap < 0:
            raise BoundedRecipeBalanceError(f"{label} bound for {name!r} must not be negative")
        bounds[name] = cap
    return bounds


def _demand_rates(values: object) -> dict[str, Fraction]:
    if not isinstance(values, Mapping):
        raise BoundedRecipeBalanceError("demand must be a mapping of material names to exact net rates")
    rates: dict[str, Fraction] = {}
    for name, value in values.items():
        if not isinstance(name, str) or not name:
            raise BoundedRecipeBalanceError("demand keys must be nonempty strings")
        rate = _exact(value, f"demand rate for {name!r}")
        if rate < 0:
            raise BoundedRecipeBalanceError(f"demand rate for {name!r} must not be negative")
        rates[name] = rate
    return rates


def _objective_coefficients(
    objective: object,
    section: str,
    names: tuple[str, ...],
) -> dict[str, Fraction]:
    if not isinstance(objective, Mapping):
        raise BoundedRecipeBalanceError(
            "objective must be a mapping with 'recipes', 'sources', and 'disposals' sections"
        )
    expected_sections = {"recipes", "sources", "disposals"}
    extra_sections = set(objective) - expected_sections
    missing_sections = expected_sections - set(objective)
    if extra_sections or missing_sections:
        details: list[str] = []
        if missing_sections:
            details.append("missing " + ", ".join(sorted(map(repr, missing_sections))))
        if extra_sections:
            details.append("unexpected " + ", ".join(sorted(map(repr, extra_sections))))
        raise BoundedRecipeBalanceError(
            f"objective sections are invalid ({'; '.join(details)})"
        )

    coefficients = objective[section]
    if not isinstance(coefficients, Mapping):
        raise BoundedRecipeBalanceError(f"objective[{section!r}] must be a mapping")
    expected = set(names)
    provided = set(coefficients)
    missing = expected - provided
    extra = provided - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(map(repr, missing))))
        if extra:
            details.append("unexpected " + ", ".join(sorted(map(repr, extra))))
        raise BoundedRecipeBalanceError(
            f"objective[{section!r}] must supply one coefficient per decision ({'; '.join(details)})"
        )
    return {
        name: _exact(coefficients[name], f"objective[{section!r}][{name!r}]")
        for name in names
    }


def _part_amounts(
    parts: object,
    recipe_name: str,
    side: str,
) -> tuple[dict[str, Fraction], dict[str, str]]:
    if isinstance(parts, (str, bytes)) or not isinstance(parts, Sequence):
        raise BoundedRecipeBalanceError(f"recipe {recipe_name!r} {side} must be a sequence")
    amounts: dict[str, Fraction] = {}
    kind_by_name: dict[str, str] = {}
    for index, part in enumerate(parts):
        if not isinstance(part, Mapping):
            raise BoundedRecipeBalanceError(
                f"recipe {recipe_name!r} {side}[{index}] must be a mapping"
            )
        extra = set(part) - {"name", "type", "amount"}
        if extra:
            fields = ", ".join(sorted(map(repr, extra)))
            raise BoundedRecipeBalanceError(
                f"recipe {recipe_name!r} {side}[{index}] has unsupported fields ({fields})"
            )
        name = part.get("name")
        kind = part.get("type")
        if not isinstance(name, str) or not name:
            raise BoundedRecipeBalanceError(
                f"recipe {recipe_name!r} {side}[{index}] needs a nonempty name"
            )
        if kind not in ("item", "fluid"):
            raise BoundedRecipeBalanceError(
                f"recipe {recipe_name!r} {side}[{index}] has unsupported type {kind!r}"
            )
        amount = _exact(
            part.get("amount"),
            f"recipe {recipe_name!r} {side}[{index}] amount",
        )
        if amount <= 0:
            raise BoundedRecipeBalanceError(
                f"recipe {recipe_name!r} {side}[{index}] amount must be positive"
            )
        if name in amounts:
            raise BoundedRecipeBalanceError(
                f"recipe {recipe_name!r} repeats {name!r} in {side}"
            )
        amounts[name] = amount
        kind_by_name[name] = kind
    return amounts, kind_by_name


def _recipe_index(catalog: object) -> dict[str, Mapping]:
    if not isinstance(catalog, Mapping):
        raise BoundedRecipeBalanceError("catalog must be a mapping")
    recipes = catalog.get("recipes")
    if not isinstance(recipes, list):
        raise BoundedRecipeBalanceError("catalog must contain a recipes list")
    index: dict[str, Mapping] = {}
    for position, recipe in enumerate(recipes):
        if not isinstance(recipe, Mapping):
            raise BoundedRecipeBalanceError(
                f"catalog recipe at index {position} must be a mapping"
            )
        name = recipe.get("name")
        if not isinstance(name, str) or not name:
            raise BoundedRecipeBalanceError(
                f"catalog recipe at index {position} needs a nonempty name"
            )
        if name in index:
            raise BoundedRecipeBalanceError(
                f"catalog defines ambiguous duplicate recipe {name!r}"
            )
        index[name] = recipe
    return index


def _exact_budget(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BoundedRecipeBalanceError("max_candidate_solves must be an int")
    if value < 0:
        raise BoundedRecipeBalanceError("max_candidate_solves must be nonnegative")
    if value > MAX_CANDIDATE_BUDGET:
        raise BoundedRecipeBalanceError(
            f"max_candidate_solves may not exceed {MAX_CANDIDATE_BUDGET}"
        )
    return value


def _dot(row: Sequence[Fraction], values: Sequence[Fraction]) -> Fraction:
    total = Fraction(0)
    for coefficient, value in zip(row, values):
        total += coefficient * value
    return total


def _verify_candidate(
    variables: tuple[Fraction, ...],
    caps: tuple[Fraction, ...],
    rows: tuple[tuple[Fraction, ...], ...],
    targets: tuple[Fraction, ...],
    objective: tuple[Fraction, ...],
    objective_value: Fraction,
) -> None:
    if len(variables) != len(caps):
        raise RuntimeError("returned candidate has the wrong variable width")
    for index, value in enumerate(variables):
        if value < 0:
            raise RuntimeError(f"returned candidate variable {index} is negative")
        if value > caps[index]:
            raise RuntimeError(f"returned candidate variable {index} exceeds its explicit cap")
    for row, target in zip(rows, targets):
        if _dot(row, variables) != target:
            raise RuntimeError("returned candidate violates an exact material equation")
    if _dot(objective, variables) != objective_value:
        raise RuntimeError("returned candidate objective failed independent revalidation")


def optimize_bounded_recipe_balance(
    catalog: object,
    *,
    recipes: object,
    sources: object,
    demand: object,
    disposals: object,
    objective: object,
    max_candidate_solves: int = DEFAULT_CANDIDATE_BUDGET,
) -> BoundedRecipeBalanceResult:
    """Maximise an explicit objective over bounded recipe, source, and disposal rates.

    ``recipes`` maps caller-selected recipe names to explicit nonnegative rate
    caps. Every selected recipe must be enabled and supported by the catalog and
    must have positive ingredients and products. ``sources`` maps material names
    to explicit nonnegative external input caps. ``disposals`` maps produced
    material names to explicit nonnegative output caps. ``demand`` gives exact
    fixed net output rates. ``objective`` must have ``recipes``, ``sources``,
    and ``disposals`` sections, each containing exactly one coefficient for
    every corresponding decision; there are no default weights or implicit
    recipe choices.

    The model is an exact equality per material::

        sum(recipe rate * (products - ingredients))
            + source rate - disposal rate = fixed net demand

    All rates are nonnegative and bounded by their caller cap. Raw resources and
    disposal are unavailable unless explicitly bounded by the caller. The
    underlying ``core.bounded_linear`` solver enumerates bounded vertices, so an
    ``optimal`` status means full enumeration completed. Budget-exhausted
    incumbents are decoded and independently checked against the material
    equations and caps, but are not claimed optimal. This function makes no
    machine, geometry, power, construction, strategic, or runtime feasibility
    claim.
    """
    budget = _exact_budget(max_candidate_solves)
    index = _recipe_index(catalog)
    recipe_caps = _bounded_decisions(recipes, "recipes")
    if not recipe_caps:
        raise BoundedRecipeBalanceError("at least one recipe must be selected explicitly")
    source_caps = _bounded_decisions(sources, "sources")
    disposal_caps = _bounded_decisions(disposals, "disposals")
    demand_rates = _demand_rates(demand)

    overlap = set(source_caps) & set(disposal_caps)
    if overlap:
        names = ", ".join(sorted(map(repr, overlap)))
        raise BoundedRecipeBalanceError(
            f"material(s) {names} are both explicit sources and explicit disposal targets"
        )

    recipe_names = tuple(sorted(recipe_caps))
    source_names = tuple(sorted(source_caps))
    disposal_names = tuple(sorted(disposal_caps))
    demand_names = tuple(sorted(demand_rates))

    recipe_objective = _objective_coefficients(objective, "recipes", recipe_names)
    source_objective = _objective_coefficients(objective, "sources", source_names)
    disposal_objective = _objective_coefficients(objective, "disposals", disposal_names)

    ingredients_by_recipe: dict[str, dict[str, Fraction]] = {}
    products_by_recipe: dict[str, dict[str, Fraction]] = {}
    kind_by_material: dict[str, str] = {}
    signatures: list[RecipeSignature] = []
    for name in recipe_names:
        recipe = index.get(name)
        if recipe is None:
            raise BoundedRecipeBalanceError(f"selected recipe {name!r} is not in the catalog")
        extra_fields = set(recipe) - _ALLOWED_RECIPE_FIELDS
        if extra_fields:
            fields = ", ".join(sorted(map(repr, extra_fields)))
            raise BoundedRecipeBalanceError(
                f"selected recipe {name!r} has unsupported fields ({fields})"
            )
        if recipe.get("enabled") is not True:
            raise BoundedRecipeBalanceError(f"selected recipe {name!r} is not enabled")
        if recipe.get("supported") is not True:
            detail = recipe.get("unsupported_reason")
            suffix = f": {detail}" if isinstance(detail, str) and detail else ""
            raise BoundedRecipeBalanceError(
                f"selected recipe {name!r} is not supported{suffix}"
            )
        if "categories" in recipe:
            try:
                recipe_categories(recipe, f"recipe {name!r}")
            except ValueError as error:
                raise BoundedRecipeBalanceError(str(error)) from error
        ingredients, ingredient_kinds = _part_amounts(
            recipe.get("ingredients"), name, "ingredients"
        )
        products, product_kinds = _part_amounts(
            recipe.get("products"), name, "products"
        )
        if not ingredients:
            raise BoundedRecipeBalanceError(
                f"recipe {name!r} has no explicit ingredients; free production is unsupported"
            )
        if not products:
            raise BoundedRecipeBalanceError(
                f"recipe {name!r} has no deterministic products"
            )
        shared = set(ingredients) & set(products)
        if shared:
            names = ", ".join(sorted(shared))
            raise BoundedRecipeBalanceError(
                f"recipe {name!r} uses catalyst or self-referential parts ({names}); "
                "this is unsupported"
            )
        for material, kind in {**ingredient_kinds, **product_kinds}.items():
            previous = kind_by_material.setdefault(material, kind)
            if previous != kind:
                raise BoundedRecipeBalanceError(
                    f"material {material!r} is declared as both {previous} and {kind} "
                    "by selected recipes"
                )
        ingredients_by_recipe[name] = ingredients
        products_by_recipe[name] = products
        signatures.append(
            RecipeSignature.from_parts(
                name,
                [(item, ingredient_kinds[item], amount) for item, amount in ingredients.items()],
                [(item, product_kinds[item], amount) for item, amount in products.items()],
            )
        )

    produced = {item for amounts in products_by_recipe.values() for item in amounts}
    consumed = {item for amounts in ingredients_by_recipe.values() for item in amounts}

    for item in sorted(consumed - produced):
        if item not in source_caps:
            raise BoundedRecipeBalanceError(
                f"material {item!r} is consumed but has no selected producer and no "
                "explicitly bounded source"
            )
    for item in sorted(produced - consumed):
        if item not in demand_rates and item not in disposal_caps:
            raise BoundedRecipeBalanceError(
                f"material {item!r} is produced but has no selected consumer, no fixed "
                "net demand, and no explicitly bounded disposal"
            )
    for item in sorted(source_caps):
        if item not in consumed:
            raise BoundedRecipeBalanceError(
                f"source {item!r} is not consumed by any selected recipe"
            )
    for item in sorted(disposal_caps):
        if item not in produced:
            raise BoundedRecipeBalanceError(
                f"disposal target {item!r} is not produced by any selected recipe"
            )
    for item in sorted(demand_rates):
        if item not in produced:
            raise BoundedRecipeBalanceError(
                f"fixed net demand {item!r} is not produced by any selected recipe"
            )

    materials = tuple(
        sorted(
            {
                MaterialIdentity(name, kind_by_material[name])
                for name in produced | consumed | set(source_caps) | set(disposal_caps) | set(demand_rates)
            },
            key=lambda material: (material.name, material.type),
        )
    )
    material_index = {material.name: position for position, material in enumerate(materials)}
    width = len(recipe_names) + len(source_names) + len(disposal_names)
    rows: list[list[Fraction]] = [[Fraction(0)] * width for _ in materials]
    for column, name in enumerate(recipe_names):
        for item, amount in products_by_recipe[name].items():
            rows[material_index[item]][column] += amount
        for item, amount in ingredients_by_recipe[name].items():
            rows[material_index[item]][column] -= amount
    for offset, name in enumerate(source_names, start=len(recipe_names)):
        rows[material_index[name]][offset] += 1
    for offset, name in enumerate(disposal_names, start=len(recipe_names) + len(source_names)):
        rows[material_index[name]][offset] -= 1
    targets = [demand_rates.get(material.name, Fraction(0)) for material in materials]

    recipe_caps_tuple = tuple(recipe_caps[name] for name in recipe_names)
    source_caps_tuple = tuple(source_caps[name] for name in source_names)
    disposal_caps_tuple = tuple(disposal_caps[name] for name in disposal_names)
    caps = (*recipe_caps_tuple, *source_caps_tuple, *disposal_caps_tuple)
    objective_values = (
        *(recipe_objective[name] for name in recipe_names),
        *(source_objective[name] for name in source_names),
        *(disposal_objective[name] for name in disposal_names),
    )

    solution = solve_bounded_linear(
        objective_values,
        caps,
        equalities=tuple(tuple(row) for row in rows),
        equality_targets=tuple(targets),
        max_candidate_solves=budget,
    )

    recipe_rate_tuple: tuple[Fraction, ...] | None = None
    source_rate_tuple: tuple[Fraction, ...] | None = None
    disposal_rate_tuple: tuple[Fraction, ...] | None = None
    net_rate_tuple: tuple[Fraction, ...] | None = None
    if solution.variables is not None:
        _verify_candidate(
            solution.variables,
            caps,
            tuple(tuple(row) for row in rows),
            tuple(targets),
            tuple(objective_values),
            solution.objective_value if solution.objective_value is not None else Fraction(0),
        )
        recipe_rate_tuple = solution.variables[: len(recipe_names)]
        source_rate_tuple = solution.variables[
            len(recipe_names) : len(recipe_names) + len(source_names)
        ]
        disposal_rate_tuple = solution.variables[len(recipe_names) + len(source_names) :]
        net_rate_tuple = tuple(
            _dot(row, solution.variables) for row in rows
        )

    if solution.status == "optimal":
        reason = (
            "full candidate enumeration completed; exact material-balance optimum for the "
            "caller's explicit objective"
        )
    elif solution.status == "infeasible":
        reason = (
            "no exact material balance satisfies the fixed net demands within the explicit "
            "recipe, source, and disposal caps"
        )
    elif solution.variables is None:
        reason = (
            f"candidate budget {budget} exhausted after {solution.candidates_evaluated} "
            "candidates with no verified incumbent; neither infeasibility nor optimality is claimed"
        )
    else:
        reason = (
            f"candidate budget {budget} exhausted after {solution.candidates_evaluated} "
            "candidates; the incumbent satisfies the model but optimality is not claimed"
        )

    return BoundedRecipeBalanceResult(
        status=solution.status,
        optimal=solution.status == "optimal",
        objective_value=solution.objective_value,
        recipes=tuple(
            RecipeDecision(
                name=name,
                cap=recipe_caps[name],
                objective_coefficient=recipe_objective[name],
                signature=signature,
            )
            for name, signature in zip(recipe_names, signatures)
        ),
        sources=tuple(
            MaterialDecision(
                material=MaterialIdentity(name, kind_by_material[name]),
                cap=source_caps[name],
                objective_coefficient=source_objective[name],
            )
            for name in source_names
        ),
        disposals=tuple(
            MaterialDecision(
                material=MaterialIdentity(name, kind_by_material[name]),
                cap=disposal_caps[name],
                objective_coefficient=disposal_objective[name],
            )
            for name in disposal_names
        ),
        demands=tuple(
            MaterialDemand(
                material=MaterialIdentity(name, kind_by_material[name]),
                rate=demand_rates[name],
            )
            for name in demand_names
        ),
        materials=materials,
        recipe_rates=recipe_rate_tuple,
        source_rates=source_rate_tuple,
        disposal_rates=disposal_rate_tuple,
        material_net_rates=net_rate_tuple,
        candidates_evaluated=solution.candidates_evaluated,
        candidate_budget=budget,
        reason=reason,
    )
