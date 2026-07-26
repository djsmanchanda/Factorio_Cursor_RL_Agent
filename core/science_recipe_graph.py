# Path: core/science_recipe_graph.py
# Purpose: Compile deterministic structural recipe knowledge for every Space Age science pack without widening live execution beyond Nauvis.

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from core.recipe_dag import validate_recipe_catalog


class ScienceLocation(str, Enum):
    """The production domain that owns a science pack."""

    NAUVIS = "nauvis"
    ORBIT = "orbit"
    VULCANUS = "vulcanus"
    GLEBA = "gleba"
    FULGORA = "fulgora"
    AQUILO = "aquilo"
    DEEP_SPACE = "deep-space"


@dataclass(frozen=True)
class SciencePackSpec:
    """Stable identity and execution scope for one science pack."""

    name: str
    location: ScienceLocation

    @property
    def nauvis_in_scope(self) -> bool:
        return self.location is ScienceLocation.NAUVIS


SCIENCE_PACK_SPECS = (
    SciencePackSpec("automation-science-pack", ScienceLocation.NAUVIS),
    SciencePackSpec("logistic-science-pack", ScienceLocation.NAUVIS),
    SciencePackSpec("chemical-science-pack", ScienceLocation.NAUVIS),
    SciencePackSpec("production-science-pack", ScienceLocation.NAUVIS),
    SciencePackSpec("utility-science-pack", ScienceLocation.NAUVIS),
    SciencePackSpec("military-science-pack", ScienceLocation.NAUVIS),
    SciencePackSpec("space-science-pack", ScienceLocation.ORBIT),
    SciencePackSpec("metallurgic-science-pack", ScienceLocation.VULCANUS),
    SciencePackSpec("agricultural-science-pack", ScienceLocation.GLEBA),
    SciencePackSpec("electromagnetic-science-pack", ScienceLocation.FULGORA),
    SciencePackSpec("cryogenic-science-pack", ScienceLocation.AQUILO),
    SciencePackSpec("promethium-science-pack", ScienceLocation.DEEP_SPACE),
)
SCIENCE_PACK_BY_NAME = MappingProxyType(
    {spec.name: spec for spec in SCIENCE_PACK_SPECS}
)
ALL_SCIENCE_PACKS = tuple(spec.name for spec in SCIENCE_PACK_SPECS)
NAUVIS_DIRECT_RESOURCE_INPUTS = frozenset({"copper-ore", "iron-ore"})

NAUVIS_SCIENCE_PACKS = tuple(
    spec.name for spec in SCIENCE_PACK_SPECS if spec.nauvis_in_scope
)

# Acquisition facts that are outside recipe contracts. They also backfill catalogs
# exported before plants and asteroid chunks were included as environmental leaves.
_ENVIRONMENTAL_SOURCES = MappingProxyType(
    {
        "ammoniacal-solution": "aquilo-resource",
        "carbonic-asteroid-chunk": "space-asteroid",
        "fish": "nauvis-wildlife",
        "jellynut": "gleba-harvest",
        "lava": "vulcanus-fluid",
        "metallic-asteroid-chunk": "space-asteroid",
        "oxide-asteroid-chunk": "space-asteroid",
        "pentapod-egg": "gleba-egg-raft-or-stomper",
        "promethium-asteroid-chunk": "deep-space-asteroid",
        "yumako": "gleba-harvest",
    }
)


def validate_nauvis_execution_target(target: str, surface: str) -> None:
    """Fail before connection when the current executor is asked to leave Nauvis."""
    if surface != ScienceLocation.NAUVIS.value:
        raise ValueError(
            f"Current execution is Nauvis-only; surface {surface!r} is outside scope"
        )
    spec = SCIENCE_PACK_BY_NAME.get(target)
    if spec is not None and not spec.nauvis_in_scope:
        raise ValueError(
            f"{target} is understood as {spec.location.value} science but is "
            "knowledge-only while execution remains on Nauvis"
        )


def validate_current_builder_target(
    target: str, surface: str, recipes: Mapping[str, Mapping]
) -> None:
    """Prove current surface scope and complete builder closure before RCON."""
    validate_nauvis_execution_target(target, surface)
    visited: set[str] = set()
    visiting: list[str] = []

    def visit(item: str) -> None:
        if item in visited:
            return
        if item in visiting:
            raise ValueError("Builder recipe cycle: " + " -> ".join(visiting + [item]))
        spec = recipes.get(item)
        if spec is None:
            raise ValueError(
                f"No executable recipe knowledge for {item!r}; "
                "the structural catalog does not make a builder stage executable"
            )
        ingredients = spec["ingredients"]
        if len(ingredients) == 1 and ingredients[0] in NAUVIS_DIRECT_RESOURCE_INPUTS:
            visited.add(item)
            return
        visiting.append(item)
        for ingredient in ingredients:
            visit(ingredient)
        visiting.pop()
        visited.add(item)

    visit(target)


def _is_recovery_or_transport(recipe: Mapping) -> bool:
    """Exclude loops that recover an item rather than establish primary supply."""
    name = str(recipe["name"])
    if name == "scrap-recycling":
        return False
    if recipe["category"] == "recycling" or name.endswith("-recycling"):
        return True
    return name.startswith("empty-") and name.endswith("-barrel")


def _normalized_recipe(recipe: Mapping) -> dict:
    result = {
        "name": recipe["name"],
        "enabled": recipe["enabled"],
        "category": recipe["category"],
        "energy_ticks": recipe["energy_ticks"],
        "ingredients": sorted(
            (dict(part) for part in recipe["ingredients"]),
            key=lambda part: (part["name"], part["type"]),
        ),
        "products": sorted(
            (dict(part) for part in recipe["products"]),
            key=lambda part: (part["name"], part["type"]),
        ),
        "supported": recipe["supported"],
    }
    if "unsupported_reason" in recipe:
        result["unsupported_reason"] = recipe["unsupported_reason"]
    return result


def compile_science_chain(target: str, catalog: Mapping) -> dict:
    """Compile structural knowledge, including alternatives and inexact recipes.

    This intentionally does not calculate rates. Probabilistic recipes and
    coproducts are valid knowledge, but remain barred from the exact executable
    DAG until a separate expected-yield policy exists.
    """
    spec = SCIENCE_PACK_BY_NAME.get(target)
    if spec is None:
        raise ValueError(f"Unknown science pack: {target}")
    validate_recipe_catalog(catalog)
    recipes_by_name = {recipe["name"]: recipe for recipe in catalog["recipes"]}
    producers: dict[str, list[Mapping]] = defaultdict(list)
    for recipe in catalog["recipes"]:
        if _is_recovery_or_transport(recipe):
            continue
        for product in recipe["products"]:
            producers[product["name"]].append(recipe)
    for candidates in producers.values():
        candidates.sort(key=lambda recipe: recipe["name"])

    catalog_sources = set(catalog["raw_resources"])
    grounded_items = catalog_sources | set(_ENVIRONMENTAL_SOURCES)
    eligible_recipes = sorted(
        (
            recipe
            for recipe in catalog["recipes"]
            if not _is_recovery_or_transport(recipe)
        ),
        key=lambda recipe: recipe["name"],
    )
    changed = True
    while changed:
        changed = False
        for recipe in eligible_recipes:
            if all(
                ingredient["name"] in grounded_items
                for ingredient in recipe["ingredients"]
            ):
                for product in recipe["products"]:
                    if product["name"] not in grounded_items:
                        grounded_items.add(product["name"])
                        changed = True
    selected_recipes: set[str] = set()
    visited_items: set[str] = set()
    external_inputs: dict[str, str] = {}
    unresolved_inputs: set[str] = set()
    producer_choices: dict[str, list[str]] = {}
    primary_producers: dict[str, str] = {}
    cycles: set[tuple[str, ...]] = set()
    stack: list[str] = []

    def visit(item: str) -> None:
        if item in catalog_sources:
            external_inputs[item] = "catalog-environment"
            return
        if item in _ENVIRONMENTAL_SOURCES:
            external_inputs[item] = _ENVIRONMENTAL_SOURCES[item]
            return
        if item in stack:
            start = stack.index(item)
            cycles.add(tuple(stack[start:] + [item]))
            return
        if item in visited_items:
            return
        visited_items.add(item)
        stack.append(item)
        candidates = producers.get(item, [])
        exact = [recipe for recipe in candidates if recipe["name"] == item]
        if not candidates:
            unresolved_inputs.add(item)
        else:
            producer_choices[item] = [recipe["name"] for recipe in candidates]
            if len(exact) == 1:
                primary_producers[item] = exact[0]["name"]
            elif len(candidates) == 1:
                primary_producers[item] = candidates[0]["name"]
            for recipe in candidates:
                selected_recipes.add(recipe["name"])
                for ingredient in recipe["ingredients"]:
                    visit(ingredient["name"])
        stack.pop()

    visit(target)
    return {
        "version": "1.0.0",
        "target": target,
        "location": spec.location.value,
        "nauvis_in_scope": spec.nauvis_in_scope,
        "complete": target in grounded_items,
        "recipes": [
            _normalized_recipe(recipes_by_name[name])
            for name in sorted(selected_recipes)
        ],
        "producer_choices": {
            item: producer_choices[item] for item in sorted(producer_choices)
        },
        "primary_producers": {
            item: primary_producers[item] for item in sorted(primary_producers)
        },
        "external_inputs": sorted(external_inputs),
        "external_sources": {
            item: external_inputs[item] for item in sorted(external_inputs)
        },
        "unresolved_inputs": sorted(unresolved_inputs),
        "ungrounded_inputs": sorted(
            item for item in visited_items if item not in grounded_items
        ),
        "cycles": [list(cycle) for cycle in sorted(cycles)],
    }


def compile_all_science_chains(catalog: Mapping) -> dict[str, dict]:
    """Return all twelve chains in stable progression order."""
    return {
        science_pack: compile_science_chain(science_pack, catalog)
        for science_pack in ALL_SCIENCE_PACKS
    }
