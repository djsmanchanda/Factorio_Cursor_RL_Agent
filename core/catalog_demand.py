# Path: core/catalog_demand.py
# Purpose: Adapt a hash-provenanced recipe catalog and net demand to an explicit fixed-recipe material balance.

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction
import hashlib
import json
from math import isfinite
import re

from core.fixed_recipe_balance import (
    FixedRecipeBalanceError,
    FixedRecipeBalanceResult,
    solve_fixed_recipe_balance,
)
from core.recipe_dag import validate_recipe_catalog

NON_CLAIMS = (
    "material balance only; recipe selection is explicit or from one unique producer",
    "no machine, inserter, belt, pipe, power, transport, or runtime feasibility is claimed",
    "catalog provenance is caller supplied and hash checked; it is not live-game verification",
)

_PROVENANCE_FIELDS = (
    "catalog_sha256",
    "game_version",
    "force",
    "surface",
    "export_tick",
    "export_command",
    "mods",
    "mod_set_sha256",
)
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
_GAME_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_BOUNDARY_FIELDS = frozenset({"name", "rate", "cap"})


class CatalogDemandReason(str, Enum):
    """Typed fail-closed reason codes for the catalog-to-demand adapter."""

    MISSING_PROVENANCE_FIELD = "provenance_missing_field"
    INVALID_PROVENANCE = "provenance_invalid"
    CATALOG_HASH_MISMATCH = "catalog_sha256_mismatch"
    PROVENANCE_MISMATCH = "provenance_mismatch"
    INVALID_CATALOG_BYTES = "catalog_bytes_invalid"
    SCHEMA_INVALID = "schema_invalid"
    INVALID_DEMAND = "invalid_demand"
    INVALID_BOUNDARY = "invalid_boundary"
    DUPLICATE_NAME = "duplicate_name"
    INVALID_PREFERENCE = "invalid_preference"
    UNSELECTED_PREFERENCE = "unselected_preference"
    AMBIGUITY = "ambiguity"
    MISSING_PRODUCER = "missing_producer"
    DISABLED_PRODUCER = "disabled_producer"
    UNSUPPORTED_PRODUCER = "unsupported_producer"
    CYCLE = "cycle"
    COPRODUCT = "coproduct"
    IDENTITY_COLLISION = "identity_collision"
    DEMAND_BOUNDARY_CONFLICT = "demand_boundary_conflict"
    BOUNDARY_NOT_CONSUMED = "boundary_not_consumed"
    BALANCE_REJECTED = "balance_rejected"


class CatalogDemandError(ValueError):
    """The request is rejected before or during the fixed material balance."""

    def __init__(self, reason: CatalogDemandReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.code = reason.value


@dataclass(frozen=True)
class ModEntry:
    """One exported mod identity in the caller-supplied catalog provenance."""

    name: str
    version: str


@dataclass(frozen=True)
class CatalogProvenance:
    """Normalized, hash-checked provenance for the exact catalog bytes used."""

    catalog_sha256: str
    game_version: str
    force: str
    surface: str
    export_tick: int
    export_command: str
    mods: tuple[ModEntry, ...]
    mod_set_sha256: str


@dataclass(frozen=True)
class InputBoundary:
    """One explicit external input boundary: either a fixed rate or a solved cap."""

    name: str
    rate: Fraction | None = None
    cap: Fraction | None = None


@dataclass(frozen=True)
class RecipeSelection:
    """Why one single-output recipe was selected for one material."""

    material: str
    recipe: str
    basis: str


@dataclass(frozen=True)
class CatalogDemandResult:
    """Exact fixed-recipe balance plus the provenance and choices that produced it."""

    provenance: CatalogProvenance
    demand: tuple[tuple[str, Fraction], ...]
    boundaries: tuple[InputBoundary, ...]
    selections: tuple[RecipeSelection, ...]
    balance: FixedRecipeBalanceResult
    notes: tuple[str, ...] = NON_CLAIMS

    @property
    def selected_recipes(self) -> tuple[str, ...]:
        return self.balance.recipe_names


def sha256_hex(data: bytes) -> str:
    """Return the lowercase SHA-256 of the exact bytes supplied by the caller."""

    if not isinstance(data, (bytes, bytearray)):
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_CATALOG_BYTES,
            "catalog bytes must be bytes, not text or a parsed object",
        )
    return hashlib.sha256(bytes(data)).hexdigest()


def _normalize_mods(value: object) -> tuple[ModEntry, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_PROVENANCE,
            "mods must be a sequence of name/version entries",
        )
    seen: set[str] = set()
    entries: list[ModEntry] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_PROVENANCE,
                f"mods[{index}] must be a mapping",
            )
        extra = set(entry) - {"name", "version"}
        if extra:
            fields = ", ".join(sorted(map(repr, extra)))
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_PROVENANCE,
                f"mods[{index}] has unsupported fields ({fields})",
            )
        name = entry.get("name")
        version = entry.get("version")
        if not isinstance(name, str) or not name:
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_PROVENANCE,
                f"mods[{index}] needs a nonempty name",
            )
        if not isinstance(version, str) or not version:
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_PROVENANCE,
                f"mods[{index}] needs a nonempty version",
            )
        if name in seen:
            raise CatalogDemandError(
                CatalogDemandReason.DUPLICATE_NAME,
                f"mod {name!r} is repeated in the provenance",
            )
        seen.add(name)
        entries.append(ModEntry(name, version))
    return tuple(sorted(entries, key=lambda mod: mod.name))


def mod_set_sha256(mods: object) -> str:
    """Return the canonical hash of the caller-declared mod set."""

    normalized = _normalize_mods(mods)
    payload = [
        {"name": mod.name, "version": mod.version}
        for mod in normalized
    ]
    data = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _nonempty_string(provenance: Mapping, field: str) -> str:
    value = provenance.get(field)
    if not isinstance(value, str) or not value:
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_PROVENANCE,
            f"provenance {field} must be a nonempty string",
        )
    return value


def _normalize_provenance(value: object) -> CatalogProvenance:
    if not isinstance(value, Mapping):
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_PROVENANCE,
            "provenance must be a mapping",
        )
    missing = [field for field in _PROVENANCE_FIELDS if field not in value]
    if missing:
        raise CatalogDemandError(
            CatalogDemandReason.MISSING_PROVENANCE_FIELD,
            "provenance is missing caller-supplied " + ", ".join(missing),
        )

    digest = value["catalog_sha256"]
    if not isinstance(digest, str) or not _HEX_SHA256.fullmatch(digest):
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_PROVENANCE,
            "provenance catalog_sha256 must be 64 lowercase hexadecimal characters",
        )
    game_version = _nonempty_string(value, "game_version")
    if not _GAME_VERSION.fullmatch(game_version):
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_PROVENANCE,
            "provenance game_version must look like MAJOR.MINOR.PATCH",
        )
    force = _nonempty_string(value, "force")
    surface = _nonempty_string(value, "surface")
    export_command = _nonempty_string(value, "export_command")
    export_tick = value["export_tick"]
    if isinstance(export_tick, bool) or not isinstance(export_tick, int) or export_tick < 0:
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_PROVENANCE,
            "provenance export_tick must be a nonnegative integer",
        )
    mods = _normalize_mods(value["mods"])
    declared_mod_hash = value["mod_set_sha256"]
    if not isinstance(declared_mod_hash, str) or not _HEX_SHA256.fullmatch(declared_mod_hash):
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_PROVENANCE,
            "provenance mod_set_sha256 must be 64 lowercase hexadecimal characters",
        )
    actual_mod_hash = mod_set_sha256(value["mods"])
    if declared_mod_hash != actual_mod_hash:
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_PROVENANCE,
            "provenance mod_set_sha256 does not match the declared mods",
        )
    return CatalogProvenance(
        catalog_sha256=digest,
        game_version=game_version,
        force=force,
        surface=surface,
        export_tick=export_tick,
        export_command=export_command,
        mods=mods,
        mod_set_sha256=declared_mod_hash,
    )


def _exact(value: object, label: str, reason: CatalogDemandReason) -> Fraction:
    if isinstance(value, bool):
        raise CatalogDemandError(
            reason,
            f"{label} must be a finite number, not a boolean",
        )
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if not isfinite(value):
            raise CatalogDemandError(reason, f"{label} must be finite")
        return Fraction(Decimal(str(value)))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CatalogDemandError(reason, f"{label} must be finite")
        return Fraction(value)
    raise CatalogDemandError(
        reason,
        f"{label} must be an int, Fraction, float, or Decimal",
    )


def _normalize_demand(value: object) -> dict[str, Fraction]:
    if not isinstance(value, Mapping) or not value:
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_DEMAND,
            "demand must be a nonempty mapping of material names to net output rates",
        )
    demand: dict[str, Fraction] = {}
    for name, raw in value.items():
        if not isinstance(name, str) or not name:
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_DEMAND,
                "demand keys must be nonempty strings",
            )
        rate = _exact(raw, f"demand rate for {name!r}", CatalogDemandReason.INVALID_DEMAND)
        if rate <= 0:
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_DEMAND,
                f"demand rate for {name!r} must be positive",
            )
        demand[name] = rate
    return demand


def _normalize_boundaries(value: object) -> tuple[InputBoundary, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_BOUNDARY,
            "boundaries must be a non-string sequence of explicit input boundaries",
        )
    seen: set[str] = set()
    boundaries: list[InputBoundary] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_BOUNDARY,
                f"boundaries[{index}] must be a mapping",
            )
        extra = set(entry) - _BOUNDARY_FIELDS
        if extra:
            fields = ", ".join(sorted(map(repr, extra)))
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_BOUNDARY,
                f"boundaries[{index}] has unsupported fields ({fields})",
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_BOUNDARY,
                f"boundaries[{index}] needs a nonempty name",
            )
        if name in seen:
            raise CatalogDemandError(
                CatalogDemandReason.DUPLICATE_NAME,
                f"boundary {name!r} is repeated",
            )
        seen.add(name)
        has_rate = "rate" in entry
        has_cap = "cap" in entry
        if has_rate == has_cap:
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_BOUNDARY,
                f"boundary {name!r} must have exactly one of rate or cap",
            )
        if has_rate:
            rate = _exact(entry["rate"], f"boundary rate for {name!r}", CatalogDemandReason.INVALID_BOUNDARY)
            if rate <= 0:
                raise CatalogDemandError(
                    CatalogDemandReason.INVALID_BOUNDARY,
                    f"boundary rate for {name!r} must be positive",
                )
            boundaries.append(InputBoundary(name=name, rate=rate))
        else:
            cap = _exact(entry["cap"], f"boundary cap for {name!r}", CatalogDemandReason.INVALID_BOUNDARY)
            if cap < 0:
                raise CatalogDemandError(
                    CatalogDemandReason.INVALID_BOUNDARY,
                    f"boundary cap for {name!r} must not be negative",
                )
            boundaries.append(InputBoundary(name=name, cap=cap))
    return tuple(sorted(boundaries, key=lambda boundary: boundary.name))


def _normalize_preferences(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_PREFERENCE,
            "preferences must be a mapping of material names to recipe names",
        )
    preferences: dict[str, str] = {}
    for material, recipe in value.items():
        if not isinstance(material, str) or not material:
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_PREFERENCE,
                "preference keys must be nonempty material names",
            )
        if not isinstance(recipe, str) or not recipe:
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_PREFERENCE,
                f"preference for {material!r} must name a nonempty recipe",
            )
        preferences[material] = recipe
    return preferences


def _index_recipes(catalog: Mapping) -> dict[str, Mapping]:
    index: dict[str, Mapping] = {}
    for position, recipe in enumerate(catalog["recipes"]):
        name = recipe["name"]
        if name in index:
            raise CatalogDemandError(
                CatalogDemandReason.DUPLICATE_NAME,
                f"catalog defines ambiguous duplicate recipe {name!r}",
            )
        for side in ("ingredients", "products"):
            seen: set[str] = set()
            for part in recipe[side]:
                if part["name"] in seen:
                    raise CatalogDemandError(
                        CatalogDemandReason.DUPLICATE_NAME,
                        f"recipe {name!r} repeats {part['name']!r} in {side}",
                    )
                seen.add(part["name"])
        index[name] = recipe
    return index


def _producer_map(index: Mapping[str, Mapping]) -> dict[str, list[Mapping]]:
    producers: dict[str, list[Mapping]] = defaultdict(list)
    for recipe in index.values():
        for product in recipe["products"]:
            producers[product["name"]].append(recipe)
    return producers


def _single_output(recipe: Mapping, material: str) -> None:
    products = recipe["products"]
    if len(products) != 1 or products[0]["name"] != material:
        names = ", ".join(product["name"] for product in products)
        raise CatalogDemandError(
            CatalogDemandReason.COPRODUCT,
            f"recipe {recipe['name']!r} is a coproduct recipe ({names}); "
            f"single-output selection for {material!r} is unsupported",
        )


def _require_usable(recipe: Mapping, material: str) -> None:
    if not recipe["enabled"]:
        raise CatalogDemandError(
            CatalogDemandReason.DISABLED_PRODUCER,
            f"producer {recipe['name']!r} for {material!r} is disabled",
        )
    if not recipe["supported"]:
        detail = recipe.get("unsupported_reason")
        suffix = f": {detail}" if isinstance(detail, str) and detail else ""
        raise CatalogDemandError(
            CatalogDemandReason.UNSUPPORTED_PRODUCER,
            f"producer {recipe['name']!r} for {material!r} is unsupported{suffix}",
        )
    _single_output(recipe, material)


def _choose_recipe(
    material: str,
    *,
    index: Mapping[str, Mapping],
    producers: Mapping[str, list[Mapping]],
    preferences: Mapping[str, str],
    used_preferences: set[tuple[str, str]],
) -> Mapping:
    preference = preferences.get(material)
    if preference is not None:
        recipe = index.get(preference)
        if recipe is None or material not in {product["name"] for product in recipe["products"]}:
            raise CatalogDemandError(
                CatalogDemandReason.INVALID_PREFERENCE,
                f"preferred recipe {preference!r} does not produce {material!r}",
            )
        _require_usable(recipe, material)
        used_preferences.add((material, preference))
        return recipe

    candidates = producers.get(material, [])
    if not candidates:
        raise CatalogDemandError(
            CatalogDemandReason.MISSING_PRODUCER,
            f"no recipe produces {material!r} and no explicit input boundary names it",
        )
    usable = [recipe for recipe in candidates if recipe["enabled"] and recipe["supported"]]
    if not usable:
        if not any(recipe["enabled"] for recipe in candidates):
            raise CatalogDemandError(
                CatalogDemandReason.DISABLED_PRODUCER,
                f"all producers for {material!r} are disabled: "
                + ", ".join(sorted(recipe["name"] for recipe in candidates)),
            )
        raise CatalogDemandError(
            CatalogDemandReason.UNSUPPORTED_PRODUCER,
            f"all enabled producers for {material!r} are unsupported: "
            + ", ".join(sorted(recipe["name"] for recipe in candidates)),
        )
    if len(usable) > 1:
        raise CatalogDemandError(
            CatalogDemandReason.AMBIGUITY,
            f"ambiguous recipes for {material!r}: "
            + ", ".join(sorted(recipe["name"] for recipe in usable))
            + "; an explicit preference is required",
        )
    recipe = usable[0]
    _require_usable(recipe, material)
    return recipe


def _select_recipes(
    *,
    index: Mapping[str, Mapping],
    producers: Mapping[str, list[Mapping]],
    demand: Mapping[str, Fraction],
    boundaries: Sequence[InputBoundary],
    preferences: Mapping[str, str],
) -> tuple[tuple[str, ...], tuple[RecipeSelection, ...]]:
    boundary_names = {boundary.name for boundary in boundaries}
    selected: dict[str, Mapping] = {}
    selected_product: dict[str, str] = {}
    selections: list[RecipeSelection] = []
    used_preferences: set[tuple[str, str]] = set()
    stack: list[str] = []

    def expand(material: str) -> None:
        if material in stack:
            cycle = " -> ".join(stack + [material])
            raise CatalogDemandError(
                CatalogDemandReason.CYCLE,
                f"recipe selection cycle: {cycle}",
            )
        if material in selected_product:
            return
        if material in boundary_names:
            return

        recipe = _choose_recipe(
            material,
            index=index,
            producers=producers,
            preferences=preferences,
            used_preferences=used_preferences,
        )
        name = recipe["name"]
        if name in selected:
            raise CatalogDemandError(
                CatalogDemandReason.IDENTITY_COLLISION,
                f"recipe {name!r} was selected for more than one material",
            )
        selected[name] = recipe
        selected_product[material] = name
        basis = "preference" if material in preferences else "unique-producer"
        selections.append(RecipeSelection(material=material, recipe=name, basis=basis))
        stack.append(material)
        for ingredient in recipe["ingredients"]:
            expand(ingredient["name"])
        stack.pop()

    for material in sorted(demand):
        expand(material)

    for material, recipe in preferences.items():
        if (material, recipe) not in used_preferences:
            raise CatalogDemandError(
                CatalogDemandReason.UNSELECTED_PREFERENCE,
                f"preference {recipe!r} for {material!r} was not selected; "
                "unused preferences are rejected",
            )

    kinds: dict[str, str] = {}
    for recipe in selected.values():
        for side in ("ingredients", "products"):
            for part in recipe[side]:
                previous = kinds.setdefault(part["name"], part["type"])
                if previous != part["type"]:
                    raise CatalogDemandError(
                        CatalogDemandReason.IDENTITY_COLLISION,
                        f"material {part['name']!r} is declared as both {previous} and "
                        f"{part['type']} by selected recipes",
                    )

    consumed = {part["name"] for recipe in selected.values() for part in recipe["ingredients"]}
    produced = {part["name"] for recipe in selected.values() for part in recipe["products"]}
    for boundary in boundaries:
        if boundary.name not in consumed:
            raise CatalogDemandError(
                CatalogDemandReason.BOUNDARY_NOT_CONSUMED,
                f"input boundary {boundary.name!r} is not consumed by any selected recipe",
            )
    for material in demand:
        if material not in produced:
            raise CatalogDemandError(
                CatalogDemandReason.BALANCE_REJECTED,
                f"requested net demand {material!r} is not produced by any selected recipe",
            )

    return (
        tuple(sorted(selected)),
        tuple(sorted(selections, key=lambda selection: (selection.material, selection.recipe))),
    )


def plan_catalog_demand(
    catalog_bytes: bytes,
    *,
    provenance: object,
    demand: object,
    boundaries: object = (),
    preferences: object = None,
) -> CatalogDemandResult:
    """Plan net demand using an explicitly provenanced, schema-valid catalog.

    ``catalog_bytes`` must be the exact bytes described by ``provenance``. The
    caller must supply the catalog SHA-256, game version, force, surface, export
    tick, export command, mod set, and mod-set hash; the adapter never derives
    provenance from a historical catalog's embedded metadata.

    ``demand`` maps materials to positive net external output rates.
    ``boundaries`` declares explicit external inputs as either a fixed ``rate``
    or a solved ``cap``. Raw resources in the catalog are never boundaries unless
    the caller declares them. Recipe choice is an explicit preference or the one
    unique enabled, supported single-output producer; otherwise this fails closed.

    The returned balance is exact material accounting only. It does not claim
    machine, transport, power, or runtime feasibility.
    """

    if not isinstance(catalog_bytes, (bytes, bytearray)):
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_CATALOG_BYTES,
            "catalog_bytes must be the exact bytes supplied by the caller",
        )
    data = bytes(catalog_bytes)
    normalized_provenance = _normalize_provenance(provenance)
    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash != normalized_provenance.catalog_sha256:
        raise CatalogDemandError(
            CatalogDemandReason.CATALOG_HASH_MISMATCH,
            f"catalog bytes hash to {actual_hash}, not the supplied "
            f"{normalized_provenance.catalog_sha256}",
        )
    try:
        catalog = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogDemandError(
            CatalogDemandReason.INVALID_CATALOG_BYTES,
            f"catalog bytes are not valid UTF-8 JSON: {exc}",
        ) from exc
    if not isinstance(catalog, Mapping):
        raise CatalogDemandError(
            CatalogDemandReason.SCHEMA_INVALID,
            "catalog JSON must be an object",
        )
    try:
        validate_recipe_catalog(catalog)
    except ValueError as exc:
        raise CatalogDemandError(
            CatalogDemandReason.SCHEMA_INVALID,
            f"catalog does not match recipe_catalog.schema.json: {exc}",
        ) from exc
    if normalized_provenance.force != catalog["force"]:
        raise CatalogDemandError(
            CatalogDemandReason.PROVENANCE_MISMATCH,
            f"provenance force {normalized_provenance.force!r} does not match "
            f"catalog force {catalog['force']!r}",
        )
    if normalized_provenance.export_tick != catalog["tick"]:
        raise CatalogDemandError(
            CatalogDemandReason.PROVENANCE_MISMATCH,
            f"provenance export_tick {normalized_provenance.export_tick} does not match "
            f"catalog tick {catalog['tick']}",
        )

    demand_rates = _normalize_demand(demand)
    normalized_boundaries = _normalize_boundaries(boundaries)
    normalized_preferences = _normalize_preferences(preferences)
    boundary_names = {boundary.name for boundary in normalized_boundaries}
    overlap = sorted(set(demand_rates) & boundary_names)
    if overlap:
        raise CatalogDemandError(
            CatalogDemandReason.DEMAND_BOUNDARY_CONFLICT,
            "net demand and input boundaries name the same materials: " + ", ".join(overlap),
        )

    index = _index_recipes(catalog)
    producers = _producer_map(index)
    selected_names, selections = _select_recipes(
        index=index,
        producers=producers,
        demand=demand_rates,
        boundaries=normalized_boundaries,
        preferences=normalized_preferences,
    )

    sources = {
        boundary.name: boundary.rate
        for boundary in normalized_boundaries
        if boundary.rate is not None
    }
    allowed_sources = tuple(
        boundary.name
        for boundary in normalized_boundaries
        if boundary.cap is not None
    )
    source_caps = {
        boundary.name: boundary.cap
        for boundary in normalized_boundaries
        if boundary.cap is not None
    }
    try:
        balance = solve_fixed_recipe_balance(
            catalog,
            recipes=selected_names,
            demand=demand_rates,
            sources=sources,
            allowed_sources=allowed_sources,
            source_caps=source_caps,
        )
    except FixedRecipeBalanceError as exc:
        raise CatalogDemandError(
            CatalogDemandReason.BALANCE_REJECTED,
            f"selected fixed recipe set has no acceptable material balance: {exc}",
        ) from exc

    return CatalogDemandResult(
        provenance=normalized_provenance,
        demand=tuple(sorted(demand_rates.items())),
        boundaries=normalized_boundaries,
        selections=selections,
        balance=balance,
        notes=NON_CLAIMS,
    )
