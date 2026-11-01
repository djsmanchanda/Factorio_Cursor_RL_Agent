# Path: core/fixed_recipe_balance.py
# Purpose: Adapt an explicitly selected fixed recipe set from the repository recipe catalog to the exact material-balance solver.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from math import isfinite
from typing import Literal

from core.material_balance import solve_fixed_balance

BalanceStatus = Literal["unique", "underdetermined", "inconsistent"]


class FixedRecipeBalanceError(ValueError):
    """The request is malformed, unsupported, or ambiguous, so no balance was attempted."""


@dataclass(frozen=True)
class SourceShortage:
    """One solved unknown-source rate above its fixed cap; the rate is unchanged."""

    material: str
    required: Fraction
    cap: Fraction


@dataclass(frozen=True, order=True)
class RecipePartSignature:
    """One normalized recipe ingredient or product entry.

    ``type`` distinguishes items from fluids. ``amount`` is the exact catalog
    quantity, so a float such as ``0.5`` becomes ``Fraction(1, 2)``.
    """

    name: str
    type: str
    amount: Fraction


@dataclass(frozen=True)
class RecipeSignature:
    """Deterministic immutable provenance for one selected recipe's math inputs.

    ``ingredients`` and ``products`` are normalized from the catalog at solve
    time and sorted by name, type, and exact amount. The signature binds the
    recipe names, part types, and exact quantities that the balance used, so a
    later machine computation can reject a different catalog that happens to
    reuse the same recipe names.
    """

    recipe: str
    ingredients: tuple[RecipePartSignature, ...]
    products: tuple[RecipePartSignature, ...]

    @classmethod
    def from_parts(
        cls,
        recipe: str,
        ingredients: Sequence[tuple[str, str, Fraction]],
        products: Sequence[tuple[str, str, Fraction]],
    ) -> RecipeSignature:
        """Build a signature from parsed ``(name, type, exact amount)`` triples."""

        def normalized(parts: Sequence[tuple[str, str, Fraction]]) -> tuple[RecipePartSignature, ...]:
            return tuple(
                sorted(
                    (RecipePartSignature(name, kind, amount) for name, kind, amount in parts),
                    key=lambda part: (part.name, part.type, part.amount),
                )
            )

        return cls(
            recipe=recipe,
            ingredients=normalized(ingredients),
            products=normalized(products),
        )


@dataclass(frozen=True)
class FixedRecipeBalanceResult:
    """Exact fixed-recipe material balance.

    ``feasible`` is ``True`` only when unique exact recipe and solved source
    rates are all nonnegative and ``False`` when the system is inconsistent,
    unique rates include negatives, or a unique solved source rate exceeds its
    fixed cap. It is ``None`` when the system is underdetermined, so
    nonnegative feasibility is unknown. It does not establish machine,
    transport, power, or runtime feasibility.

    ``source_names`` and ``source_rates`` describe explicitly allowed unknown
    external source rates; ``source_caps`` is aligned with ``source_names``.
    ``shortages`` records caps exceeded by a unique solution without altering
    that solution. Caps are not evaluated for underdetermined or inconsistent
    systems.

    ``recipe_signatures`` is aligned with ``recipe_names`` and records the
    normalized ingredient/product names, types, and exact amounts read from the
    catalog for this solve. It is deterministic and immutable provenance, not a
    substitute for recomputing against a catalog.
    """

    status: BalanceStatus
    feasible: bool | None
    recipe_names: tuple[str, ...]
    rates: tuple[Fraction, ...] | None
    reason: str
    source_names: tuple[str, ...] = ()
    source_rates: tuple[Fraction, ...] | None = None
    source_caps: tuple[Fraction | None, ...] = ()
    shortages: tuple[SourceShortage, ...] = ()
    recipe_signatures: tuple[RecipeSignature, ...] = ()


def _exact_rate(value: object, label: str) -> Fraction:
    if isinstance(value, bool):
        raise FixedRecipeBalanceError(f"{label} must be a finite number, not a boolean")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if not isfinite(value):
            raise FixedRecipeBalanceError(f"{label} must be finite")
        return Fraction(Decimal(str(value)))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise FixedRecipeBalanceError(f"{label} must be finite")
        return Fraction(value)
    raise FixedRecipeBalanceError(f"{label} must be an int, Fraction, float, or Decimal")


def _named_rates(values: object, label: str, *, positive: bool) -> dict[str, Fraction]:
    if not isinstance(values, Mapping):
        raise FixedRecipeBalanceError(f"{label} must be a mapping of item names to rates")
    rates: dict[str, Fraction] = {}
    for name, value in values.items():
        if not isinstance(name, str) or not name:
            raise FixedRecipeBalanceError(f"{label} keys must be nonempty strings")
        rate = _exact_rate(value, f"{label} rate for {name!r}")
        if positive and rate <= 0:
            raise FixedRecipeBalanceError(f"{label} rate for {name!r} must be positive")
        if not positive and rate < 0:
            raise FixedRecipeBalanceError(f"{label} rate for {name!r} must not be negative")
        rates[name] = rate
    return rates


def _allowed_source_names(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise FixedRecipeBalanceError("allowed_sources must be a non-string sequence of material names")
    names = tuple(values)
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not name:
            raise FixedRecipeBalanceError("allowed source names must be nonempty strings")
        if name in seen:
            raise FixedRecipeBalanceError(f"allowed source {name!r} is repeated")
        seen.add(name)
    return tuple(sorted(names))


def _source_caps(values: object, allowed: tuple[str, ...]) -> dict[str, Fraction]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise FixedRecipeBalanceError("source_caps must be a mapping of allowed source names to caps")
    allowed_set = set(allowed)
    caps: dict[str, Fraction] = {}
    for name, value in values.items():
        if not isinstance(name, str) or not name:
            raise FixedRecipeBalanceError("source_caps keys must be nonempty strings")
        if name not in allowed_set:
            raise FixedRecipeBalanceError(
                f"source cap {name!r} does not name an allowed unknown source material"
            )
        cap = _exact_rate(value, f"source cap for {name!r}")
        if cap < 0:
            raise FixedRecipeBalanceError(f"source cap for {name!r} must not be negative")
        caps[name] = cap
    return caps


def _part_amounts(
    parts: object, recipe_name: str, side: str
) -> tuple[dict[str, Fraction], dict[str, str]]:
    if not isinstance(parts, list):
        raise FixedRecipeBalanceError(f"recipe {recipe_name!r} {side} must be a list")
    amounts: dict[str, Fraction] = {}
    kind_by_name: dict[str, str] = {}
    for index, part in enumerate(parts):
        if not isinstance(part, Mapping):
            raise FixedRecipeBalanceError(f"recipe {recipe_name!r} {side}[{index}] must be a mapping")
        extra = set(part) - {"name", "type", "amount"}
        if extra:
            fields = ", ".join(sorted(map(repr, extra)))
            raise FixedRecipeBalanceError(
                f"recipe {recipe_name!r} {side}[{index}] has unsupported fields ({fields})"
            )
        name = part.get("name")
        kind = part.get("type")
        if not isinstance(name, str) or not name:
            raise FixedRecipeBalanceError(f"recipe {recipe_name!r} {side}[{index}] needs a nonempty name")
        if kind not in ("item", "fluid"):
            raise FixedRecipeBalanceError(f"recipe {recipe_name!r} {side}[{index}] has unsupported type {kind!r}")
        amount = _exact_rate(part.get("amount"), f"recipe {recipe_name!r} {side}[{index}] amount")
        if amount <= 0:
            raise FixedRecipeBalanceError(f"recipe {recipe_name!r} {side}[{index}] amount must be positive")
        if name in amounts:
            raise FixedRecipeBalanceError(f"recipe {recipe_name!r} repeats {name!r} in {side}")
        amounts[name] = amount
        kind_by_name[name] = kind
    return amounts, kind_by_name


def _recipe_index(catalog: object) -> dict[str, Mapping]:
    if not isinstance(catalog, Mapping):
        raise FixedRecipeBalanceError("catalog must be a mapping")
    recipes = catalog.get("recipes")
    if not isinstance(recipes, list):
        raise FixedRecipeBalanceError("catalog must contain a recipes list")
    index: dict[str, Mapping] = {}
    for position, recipe in enumerate(recipes):
        if not isinstance(recipe, Mapping):
            raise FixedRecipeBalanceError(f"catalog recipe at index {position} must be a mapping")
        name = recipe.get("name")
        if not isinstance(name, str) or not name:
            raise FixedRecipeBalanceError(f"catalog recipe at index {position} needs a nonempty name")
        if name in index:
            raise FixedRecipeBalanceError(f"catalog defines ambiguous duplicate recipe {name!r}")
        index[name] = recipe
    return index


def _selected_names(recipes: object) -> tuple[str, ...]:
    if isinstance(recipes, (str, bytes)) or not isinstance(recipes, Sequence):
        raise FixedRecipeBalanceError("recipes must be a non-string sequence of names")
    names = tuple(recipes)
    if not names:
        raise FixedRecipeBalanceError("at least one recipe must be selected explicitly")
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not name:
            raise FixedRecipeBalanceError("selected recipe names must be nonempty strings")
        if name in seen:
            raise FixedRecipeBalanceError(f"selected recipe {name!r} is repeated")
        seen.add(name)
    return tuple(sorted(names))


def solve_fixed_recipe_balance(
    catalog: object,
    *,
    recipes: object,
    demand: object,
    sources: object = None,
    allowed_sources: object = (),
    source_caps: object = None,
) -> FixedRecipeBalanceResult:
    """Solve one explicitly selected fixed recipe set exactly.

    ``demand`` is requested net external output (nonnegative). ``sources`` are
    explicit fixed external input rates (positive). ``allowed_sources`` names
    additional consumed materials whose external input rates are solved as
    unknowns rather than supplied. ``source_caps`` sets fixed upper bounds for
    those solved rates; caps are only evaluated for a unique solution and a
    shortfall is reported without changing the exact solution.

    Every consumed material must have a selected producer, a fixed source
    boundary, or an allowed unknown source. Every produced material must have a
    selected consumer or declared net demand. Recipe choice, source sizing,
    disposal strategy, machine counts, and runtime execution are out of scope.
    Underdetermined systems do not establish nonnegative feasibility.

    The result records normalized ``recipe_signatures`` for every selected
    recipe so a later step can verify that a catalog still has the same recipe
    parts, types, and exact amounts before reusing the solved rates.
    """
    index = _recipe_index(catalog)
    names = _selected_names(recipes)
    demand_rates = _named_rates(demand, "demand", positive=False)
    source_rates = {} if sources is None else _named_rates(sources, "source", positive=True)
    allowed_names = _allowed_source_names(allowed_sources)
    caps = _source_caps(source_caps, allowed_names)
    for material in sorted(set(source_rates) & set(allowed_names)):
        raise FixedRecipeBalanceError(
            f"material {material!r} is both a fixed source and an allowed unknown source"
        )

    ingredients_by_recipe: dict[str, dict[str, Fraction]] = {}
    products_by_recipe: dict[str, dict[str, Fraction]] = {}
    kind_by_material: dict[str, str] = {}
    signatures: list[RecipeSignature] = []
    for name in names:
        recipe = index.get(name)
        if recipe is None:
            raise FixedRecipeBalanceError(f"selected recipe {name!r} is not in the catalog")
        if recipe.get("enabled") is not True:
            raise FixedRecipeBalanceError(f"selected recipe {name!r} is not enabled")
        if recipe.get("supported") is not True:
            detail = recipe.get("unsupported_reason")
            suffix = f": {detail}" if isinstance(detail, str) and detail else ""
            raise FixedRecipeBalanceError(f"selected recipe {name!r} is not supported{suffix}")
        ingredients, ingredient_kinds = _part_amounts(recipe.get("ingredients"), name, "ingredients")
        products, product_kinds = _part_amounts(recipe.get("products"), name, "products")
        if not products:
            raise FixedRecipeBalanceError(f"recipe {name!r} has no deterministic products")
        if set(ingredients) & set(products):
            shared = ", ".join(sorted(set(ingredients) & set(products)))
            raise FixedRecipeBalanceError(
                f"recipe {name!r} uses catalyst or self-referential parts ({shared}); this is unsupported"
            )
        for material, kind in {**ingredient_kinds, **product_kinds}.items():
            previous = kind_by_material.setdefault(material, kind)
            if previous != kind:
                raise FixedRecipeBalanceError(
                    f"material {material!r} is declared as both {previous} and {kind} by selected recipes"
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
    allowed_set = set(allowed_names)

    for item in sorted(consumed - produced):
        if item not in source_rates and item not in allowed_set:
            raise FixedRecipeBalanceError(
                f"material {item!r} is consumed but has no selected producer and no explicit source boundary"
            )
    for item in sorted(produced - consumed):
        if item not in demand_rates:
            raise FixedRecipeBalanceError(
                f"material {item!r} is produced but has no selected consumer and no requested net demand; "
                "surplus or disposal is unsupported"
            )
    for item in sorted(source_rates):
        if item not in consumed:
            raise FixedRecipeBalanceError(
                f"source boundary {item!r} is not consumed by any selected recipe"
            )
    for item in sorted(allowed_names):
        if item not in consumed:
            raise FixedRecipeBalanceError(
                f"allowed unknown source {item!r} is not consumed by any selected recipe"
            )
    for item in sorted(demand_rates):
        if item not in produced:
            raise FixedRecipeBalanceError(
                f"requested net demand {item!r} is not produced by any selected recipe"
            )

    width = len(names) + len(allowed_names)
    materials = sorted(produced | consumed | set(demand_rates) | set(source_rates) | allowed_set)
    coefficients = {material: [Fraction(0)] * width for material in materials}
    for column, name in enumerate(names):
        for item, amount in products_by_recipe[name].items():
            coefficients[item][column] += amount
        for item, amount in ingredients_by_recipe[name].items():
            coefficients[item][column] -= amount
    for offset, material in enumerate(allowed_names, start=len(names)):
        coefficients[material][offset] += 1

    rows = [coefficients[material] for material in materials]
    targets = [
        demand_rates.get(material, Fraction(0)) - source_rates.get(material, Fraction(0))
        for material in materials
    ]
    solution = solve_fixed_balance(rows, targets)

    cap_by_name = tuple(caps.get(name) for name in allowed_names)

    if solution.status == "underdetermined":
        return FixedRecipeBalanceResult(
            status="underdetermined",
            feasible=None,
            recipe_names=names,
            rates=solution.rates,
            reason="underdetermined exact rates; nonnegative feasibility is not established",
            source_names=allowed_names,
            source_rates=None,
            source_caps=cap_by_name,
            recipe_signatures=tuple(signatures),
        )
    if solution.status == "inconsistent":
        return FixedRecipeBalanceResult(
            status="inconsistent",
            feasible=False,
            recipe_names=names,
            rates=solution.rates,
            reason="requested demand and source boundaries have no exact fixed-recipe solution",
            source_names=allowed_names,
            source_rates=None,
            source_caps=cap_by_name,
            recipe_signatures=tuple(signatures),
        )

    rates = solution.rates
    if rates is None:
        raise AssertionError("unique balance must include exact rates")
    recipe_rates = rates[: len(names)]
    unknown_rates = rates[len(names) :]
    negative_recipes = [(name, rate) for name, rate in zip(names, recipe_rates) if rate < 0]
    negative_sources = [
        (name, rate) for name, rate in zip(allowed_names, unknown_rates) if rate < 0
    ]
    if negative_recipes or negative_sources:
        details: list[str] = []
        if negative_recipes:
            details.append(
                "recipes " + ", ".join(f"{name}={rate}" for name, rate in negative_recipes)
            )
        if negative_sources:
            details.append(
                "sources " + ", ".join(f"{name}={rate}" for name, rate in negative_sources)
            )
        return FixedRecipeBalanceResult(
            status="unique",
            feasible=False,
            recipe_names=names,
            rates=recipe_rates,
            reason=f"unique exact rates include negative values ({'; '.join(details)}); not feasible",
            source_names=allowed_names,
            source_rates=unknown_rates,
            source_caps=cap_by_name,
            recipe_signatures=tuple(signatures),
        )

    shortages = tuple(
        SourceShortage(material=name, required=rate, cap=caps[name])
        for name, rate in zip(allowed_names, unknown_rates)
        if name in caps and rate > caps[name]
    )
    if shortages:
        detail = ", ".join(
            f"{shortage.material} requires {shortage.required} above cap {shortage.cap}"
            for shortage in shortages
        )
        return FixedRecipeBalanceResult(
            status="unique",
            feasible=False,
            recipe_names=names,
            rates=recipe_rates,
            reason=f"unique exact rates exceed fixed source caps ({detail}); solution is unchanged",
            source_names=allowed_names,
            source_rates=unknown_rates,
            source_caps=cap_by_name,
            shortages=shortages,
            recipe_signatures=tuple(signatures),
        )
    return FixedRecipeBalanceResult(
        status="unique",
        feasible=True,
        recipe_names=names,
        rates=recipe_rates,
        reason="unique exact nonnegative fixed-recipe material balance",
        source_names=allowed_names,
        source_rates=unknown_rates,
        source_caps=cap_by_name,
        recipe_signatures=tuple(signatures),
    )
