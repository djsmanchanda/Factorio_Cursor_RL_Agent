# Path: tests/test_catalog_demand.py
# Purpose: Verify the offline catalog-to-demand adapter's provenance, recipe selection, and fixed balance delegation.

from __future__ import annotations

from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest

from core.catalog_demand import (
    NON_CLAIMS,
    CatalogDemandError,
    CatalogDemandReason,
    InputBoundary,
    ModEntry,
    RecipeSelection,
    mod_set_sha256,
    plan_catalog_demand,
)
from core.recipe_dag import compile_recipe_dag
from core.fixed_recipe_balance import SourceShortage

FIXTURES = Path(__file__).parent / "fixtures"

REAL_CATALOG = (
    Path(__file__).resolve().parents[1]
    / "data/factorio-2.1.21/probes/deterministic/server/script-output/factorio_mod"
    / "recipe_catalogs/recipe_catalog_243688.json"
)

# Only force, tick, and the exact catalog bytes/hash are observed from the
# export below. Game version, surface, export command, and the mod set are
# SYNTHETIC/ASSUMED test values: the export carries no mod or surface fields,
# so these are not verified and are not a claim about the real runtime.
ASSUMED_GAME_VERSION = "2.1.21"
SYNTHETIC_SURFACE = "synthetic-test-surface"
SYNTHETIC_EXPORT_COMMAND = "synthetic-test-export"
SYNTHETIC_MODS = [{"name": "synthetic-test-mod", "version": "0.0.0"}]

ADVANCED_CIRCUIT_PREFERENCES = {
    "advanced-circuit": "advanced-circuit",
    "copper-cable": "copper-cable",
    "copper-plate": "copper-plate",
    "electronic-circuit": "electronic-circuit",
    "iron-plate": "iron-plate",
    "plastic-bar": "plastic-bar",
}


def _real_provenance(data: bytes, catalog: dict) -> dict:
    return {
        "catalog_sha256": hashlib.sha256(data).hexdigest(),
        "game_version": ASSUMED_GAME_VERSION,
        "force": catalog["force"],
        "surface": SYNTHETIC_SURFACE,
        "export_tick": catalog["tick"],
        "export_command": SYNTHETIC_EXPORT_COMMAND,
        "mods": [dict(mod) for mod in SYNTHETIC_MODS],
        "mod_set_sha256": mod_set_sha256(SYNTHETIC_MODS),
    }


def _advanced_circuit_boundaries(copper_ore_cap: int = 5) -> list[dict]:
    return [
        {"name": "coal", "rate": 1},
        {"name": "petroleum-gas", "rate": 20},
        {"name": "copper-ore", "cap": copper_ore_cap},
        {"name": "iron-ore", "cap": 2},
    ]


def _part(name: str, amount: int | float, kind: str = "item") -> dict:
    return {"name": name, "type": kind, "amount": amount}


def _parts(entries: list[tuple]) -> list[dict]:
    parts: list[dict] = []
    for entry in entries:
        name, amount, *rest = entry
        parts.append(_part(name, amount, rest[0] if rest else "item"))
    return parts


def _recipe(
    name: str,
    ingredients: list[tuple],
    products: list[tuple],
    *,
    enabled: bool = True,
    supported: bool = True,
    unsupported_reason: str | None = None,
) -> dict:
    recipe = {
        "name": name,
        "enabled": enabled,
        "category": "crafting",
        "energy_ticks": 60,
        "ingredients": _parts(ingredients),
        "products": _parts(products),
        "supported": supported,
    }
    if unsupported_reason is not None:
        recipe["unsupported_reason"] = unsupported_reason
    return recipe


def _catalog(
    *recipes: dict,
    raw: tuple[str, ...] = (),
    force: str = "player",
    tick: int = 100,
) -> dict:
    return {
        "version": "1.0.0",
        "force": force,
        "tick": tick,
        "raw_resources": list(raw),
        "recipes": list(recipes),
    }


def _bytes(catalog: dict) -> bytes:
    return json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _provenance(
    data: bytes,
    catalog: dict,
    *,
    mods: list[dict] | None = None,
    **overrides: object,
) -> dict:
    declared_mods = [] if mods is None else [dict(mod) for mod in mods]
    provenance = {
        "catalog_sha256": hashlib.sha256(data).hexdigest(),
        "game_version": "0.0.0",
        "force": catalog["force"],
        "surface": "test-surface",
        "export_tick": catalog["tick"],
        "export_command": "/test-export",
        "mods": declared_mods,
        "mod_set_sha256": mod_set_sha256(declared_mods),
    }
    provenance.update(overrides)
    return provenance


def _plan(
    catalog: dict,
    *,
    demand: object,
    boundaries: object = (),
    preferences: object = None,
    mods: list[dict] | None = None,
    **provenance_overrides: object,
):
    data = _bytes(catalog)
    provenance = _provenance(data, catalog, mods=mods, **provenance_overrides)
    return plan_catalog_demand(
        data,
        provenance=provenance,
        demand=demand,
        boundaries=boundaries,
        preferences=preferences,
    )


def _error(catalog: dict, *, demand: object, **kwargs) -> CatalogDemandError:
    with pytest.raises(CatalogDemandError) as captured:
        _plan(catalog, demand=demand, **kwargs)
    return captured.value


def _rates(result) -> dict[str, Fraction]:
    assert result.balance.rates is not None
    return dict(zip(result.balance.recipe_names, result.balance.rates))


def _sources(result) -> dict[str, Fraction]:
    assert result.balance.source_rates is not None
    return dict(zip(result.balance.source_names, result.balance.source_rates))


def test_safe_chain_matches_existing_recipe_dag():
    data = (FIXTURES / "recipe_catalog.json").read_bytes()
    catalog = json.loads(data)
    provenance = _provenance(data, catalog)
    goal = {
        "version": "1.0.0",
        "target": "electronic-circuit",
        "delta": {"rate": 2, "unit": "per_second"},
    }
    graph = compile_recipe_dag(goal, catalog)

    result = plan_catalog_demand(
        data,
        provenance=provenance,
        demand={"electronic-circuit": 2},
        boundaries=[
            {"name": "copper-ore", "cap": 3},
            {"name": "iron-ore", "cap": 2},
        ],
    )

    assert result.balance.status == "unique"
    assert result.balance.feasible is True
    assert set(result.selected_recipes) == {
        "copper-plate",
        "copper-cable",
        "iron-plate",
        "electronic-circuit",
    }
    dag_rates = {node["recipe"]: node["crafts_per_second"] for node in graph["nodes"]}
    assert set(_rates(result)) == set(dag_rates)
    for name, rate in _rates(result).items():
        assert rate == Fraction(str(dag_rates[name]))
    assert _sources(result) == {"copper-ore": Fraction(3), "iron-ore": Fraction(2)}
    assert graph["raw_leaves_per_second"] == {"copper-ore": 3.0, "iron-ore": 2.0}
    assert result.provenance.catalog_sha256 == hashlib.sha256(data).hexdigest()
    assert result.notes == NON_CLAIMS


def test_explicit_preference_resolves_ambiguity():
    catalog = _catalog(
        _recipe("x-from-a", [("a", 1)], [("x", 1)]),
        _recipe("x-from-b", [("b", 1)], [("x", 1)]),
    )
    ambiguity = _error(catalog, demand={"x": 1}, boundaries=[{"name": "a", "rate": 1}])
    assert ambiguity.reason is CatalogDemandReason.AMBIGUITY
    assert "x-from-a" in str(ambiguity) and "x-from-b" in str(ambiguity)

    result = _plan(
        catalog,
        demand={"x": 1},
        boundaries=[{"name": "b", "rate": 1}],
        preferences={"x": "x-from-b"},
    )

    assert result.selected_recipes == ("x-from-b",)
    assert result.selections == (
        RecipeSelection(material="x", recipe="x-from-b", basis="preference"),
    )
    assert _rates(result) == {"x-from-b": Fraction(1)}


def test_exact_name_is_not_an_implicit_recipe_choice():
    catalog = _catalog(
        _recipe("x", [("a", 1)], [("x", 1)]),
        _recipe("x-alternate", [("a", 1)], [("x", 1)]),
    )

    ambiguity = _error(catalog, demand={"x": 1}, boundaries=[{"name": "a", "rate": 1}])
    assert ambiguity.reason is CatalogDemandReason.AMBIGUITY

    result = _plan(
        catalog,
        demand={"x": 1},
        boundaries=[{"name": "a", "rate": 1}],
        preferences={"x": "x"},
    )
    assert result.selected_recipes == ("x",)


@pytest.mark.parametrize(
    "recipe, reason",
    [
        (None, CatalogDemandReason.MISSING_PRODUCER),
        (_recipe("x", [("a", 1)], [("x", 1)], enabled=False), CatalogDemandReason.DISABLED_PRODUCER),
        (_recipe("x", [("a", 1)], [("x", 1)], supported=False), CatalogDemandReason.UNSUPPORTED_PRODUCER),
    ],
)
def test_missing_disabled_and_unsupported_producers_fail_closed(recipe, reason):
    catalog = _catalog(*(([recipe] if recipe else [])), raw=("x",))
    error = _error(catalog, demand={"x": 1}, boundaries=[{"name": "a", "rate": 1}])
    assert error.reason is reason


def test_raw_resources_are_not_automatic_input_boundaries():
    catalog = _catalog(raw=("ore",))
    error = _error(catalog, demand={"ore": 1})
    assert error.reason is CatalogDemandReason.MISSING_PRODUCER

    producing = _catalog(_recipe("plate-from-ore", [("ore", 1)], [("plate", 1)]), raw=("ore",))
    result = _plan(
        producing,
        demand={"plate": 1},
        boundaries=[{"name": "ore", "rate": 1}],
    )
    assert result.selected_recipes == ("plate-from-ore",)
    assert result.boundaries == (InputBoundary("ore", rate=Fraction(1)),)
    assert "ore" not in {selection.material for selection in result.selections}


def test_recipe_cycle_fails_closed():
    catalog = _catalog(
        _recipe("a-from-b", [("b", 1)], [("a", 1)]),
        _recipe("b-from-a", [("a", 1)], [("b", 1)]),
    )
    error = _error(catalog, demand={"a": 1})
    assert error.reason is CatalogDemandReason.CYCLE
    assert "a -> b -> a" in str(error) or "b -> a -> b" in str(error)


def test_coproduct_recipe_fails_closed():
    catalog = _catalog(
        _recipe("split", [("ore", 1)], [("a", 1), ("b", 1)]),
        raw=("ore",),
    )
    error = _error(catalog, demand={"a": 1}, boundaries=[{"name": "ore", "rate": 1}])
    assert error.reason is CatalogDemandReason.COPRODUCT


def test_item_and_fluid_identity_collision_fails_closed():
    catalog = _catalog(
        _recipe("make-x", [("ore", 1)], [("x", 1)]),
        _recipe("x-to-y", [("x", 1, "fluid")], [("y", 1)]),
    )
    error = _error(catalog, demand={"y": 1}, boundaries=[{"name": "ore", "rate": 1}])
    assert error.reason is CatalogDemandReason.IDENTITY_COLLISION
    assert "both" in str(error) and "item" in str(error) and "fluid" in str(error)


def test_duplicate_recipe_names_fail_closed():
    duplicate = _recipe("same", [("a", 1)], [("x", 1)])
    catalog = _catalog(duplicate, dict(duplicate))
    error = _error(catalog, demand={"x": 1}, boundaries=[{"name": "a", "rate": 1}])
    assert error.reason is CatalogDemandReason.DUPLICATE_NAME


def test_duplicate_part_names_fail_closed():
    catalog = _catalog(_recipe("plate", [("ore", 1), ("ore", 1)], [("plate", 1)]))
    error = _error(catalog, demand={"plate": 1}, boundaries=[{"name": "ore", "rate": 2}])
    assert error.reason is CatalogDemandReason.DUPLICATE_NAME


def test_duplicate_boundary_names_fail_closed():
    catalog = _catalog(_recipe("plate", [("ore", 1)], [("plate", 1)]))
    error = _error(
        catalog,
        demand={"plate": 1},
        boundaries=[{"name": "ore", "rate": 1}, {"name": "ore", "cap": 2}],
    )
    assert error.reason is CatalogDemandReason.DUPLICATE_NAME


def test_unselected_unused_preferences_fail_closed():
    catalog = _catalog(_recipe("x-from-ore", [("ore", 1)], [("x", 1)]))
    unused = _error(
        catalog,
        demand={"x": 1},
        boundaries=[{"name": "ore", "rate": 1}],
        preferences={"z": "x-from-ore"},
    )
    assert unused.reason is CatalogDemandReason.UNSELECTED_PREFERENCE

    boundary_preference = _error(
        catalog,
        demand={"x": 1},
        boundaries=[{"name": "ore", "rate": 1}],
        preferences={"ore": "x-from-ore"},
    )
    assert boundary_preference.reason is CatalogDemandReason.UNSELECTED_PREFERENCE


@pytest.mark.parametrize(
    "preference, reason",
    [
        ({"x": "missing"}, CatalogDemandReason.INVALID_PREFERENCE),
        ({"x": "wrong-product"}, CatalogDemandReason.INVALID_PREFERENCE),
        ({"x": "disabled"}, CatalogDemandReason.DISABLED_PRODUCER),
        ({"x": "unsupported"}, CatalogDemandReason.UNSUPPORTED_PRODUCER),
    ],
)
def test_invalid_preferences_fail_closed(preference, reason):
    catalog = _catalog(
        _recipe("x", [("ore", 1)], [("x", 1)]),
        _recipe("wrong-product", [("ore", 1)], [("y", 1)]),
        _recipe("disabled", [("ore", 1)], [("x", 1)], enabled=False),
        _recipe("unsupported", [("ore", 1)], [("x", 1)], supported=False),
    )
    error = _error(
        catalog,
        demand={"x": 1},
        boundaries=[{"name": "ore", "rate": 1}],
        preferences=preference,
    )
    assert error.reason is reason


def test_provenance_requires_every_caller_supplied_field():
    catalog = _catalog(_recipe("plate", [("ore", 1)], [("plate", 1)]))
    data = _bytes(catalog)
    provenance = _provenance(data, catalog)
    provenance.pop("mods")
    with pytest.raises(CatalogDemandError) as captured:
        plan_catalog_demand(
            data,
            provenance=provenance,
            demand={"plate": 1},
            boundaries=[{"name": "ore", "rate": 1}],
        )
    assert captured.value.reason is CatalogDemandReason.MISSING_PROVENANCE_FIELD
    assert "mods" in str(captured.value)


def test_provenance_hash_and_metadata_are_checked_against_actual_bytes():
    catalog = _catalog(_recipe("plate", [("ore", 1)], [("plate", 1)]))
    data = _bytes(catalog)

    bad_hash = _provenance(data, catalog, catalog_sha256="0" * 64)
    with pytest.raises(CatalogDemandError) as captured:
        plan_catalog_demand(
            data,
            provenance=bad_hash,
            demand={"plate": 1},
            boundaries=[{"name": "ore", "rate": 1}],
        )
    assert captured.value.reason is CatalogDemandReason.CATALOG_HASH_MISMATCH

    for field, value in (("force", "other"), ("export_tick", 101)):
        provenance = _provenance(data, catalog, **{field: value})
        with pytest.raises(CatalogDemandError) as mismatch:
            plan_catalog_demand(
                data,
                provenance=provenance,
                demand={"plate": 1},
                boundaries=[{"name": "ore", "rate": 1}],
            )
        assert mismatch.value.reason is CatalogDemandReason.PROVENANCE_MISMATCH


def test_provenance_mod_hash_must_match_the_declared_mod_set():
    catalog = _catalog(_recipe("plate", [("ore", 1)], [("plate", 1)]))
    data = _bytes(catalog)
    mods = [{"name": "base", "version": "2.0.77"}]
    provenance = _provenance(data, catalog, mods=mods, mod_set_sha256="0" * 64)
    with pytest.raises(CatalogDemandError) as captured:
        plan_catalog_demand(
            data,
            provenance=provenance,
            demand={"plate": 1},
            boundaries=[{"name": "ore", "rate": 1}],
        )
    assert captured.value.reason is CatalogDemandReason.INVALID_PROVENANCE

    result = _plan(
        catalog,
        demand={"plate": 1},
        boundaries=[{"name": "ore", "rate": 1}],
        mods=mods,
    )
    assert result.provenance.mods == (ModEntry("base", "2.0.77"),)


def test_mod_set_hash_is_canonical_and_rejects_duplicate_names():
    first = [{"name": "b", "version": "2"}, {"name": "a", "version": "1"}]
    second = [{"name": "a", "version": "1"}, {"name": "b", "version": "2"}]
    assert mod_set_sha256(first) == mod_set_sha256(second)

    catalog = _catalog(_recipe("plate", [("ore", 1)], [("plate", 1)]))
    error = _error(
        catalog,
        demand={"plate": 1},
        boundaries=[{"name": "ore", "rate": 1}],
        mods=[{"name": "a", "version": "1"}, {"name": "a", "version": "2"}],
    )
    assert error.reason is CatalogDemandReason.DUPLICATE_NAME


def test_invalid_bytes_and_schema_fail_closed():
    catalog = _catalog(_recipe("plate", [("ore", 1)], [("plate", 1)]))
    data = _bytes(catalog)
    provenance = _provenance(data, catalog)

    with pytest.raises(CatalogDemandError) as text:
        plan_catalog_demand(
            data.decode("utf-8"),
            provenance=provenance,
            demand={"plate": 1},
        )
    assert text.value.reason is CatalogDemandReason.INVALID_CATALOG_BYTES

    broken = b"{not json"
    broken_provenance = _provenance(broken, catalog)
    with pytest.raises(CatalogDemandError) as invalid_json:
        plan_catalog_demand(broken, provenance=broken_provenance, demand={"plate": 1})
    assert invalid_json.value.reason is CatalogDemandReason.INVALID_CATALOG_BYTES

    invalid_catalog = {"version": "1.0.0", "force": "player", "tick": 100}
    invalid_data = _bytes(invalid_catalog)
    invalid_provenance = _provenance(invalid_data, invalid_catalog)
    with pytest.raises(CatalogDemandError) as schema:
        plan_catalog_demand(
            invalid_data,
            provenance=invalid_provenance,
            demand={"plate": 1},
        )
    assert schema.value.reason is CatalogDemandReason.SCHEMA_INVALID


def test_historical_fixture_does_not_supply_its_own_provenance():
    path = FIXTURES / "player_recipe_catalog.json"
    if not path.exists():
        pytest.skip("historical recipe catalog fixture is unavailable")
    data = path.read_bytes()
    catalog = json.loads(data)
    embedded = catalog["fixture_provenance"]
    assert "mods" not in embedded
    assert "mod_set_sha256" not in embedded

    with pytest.raises(CatalogDemandError) as captured:
        plan_catalog_demand(
            data,
            provenance={"catalog_sha256": hashlib.sha256(data).hexdigest()},
            demand={"electronic-circuit": 1},
        )
    assert captured.value.reason is CatalogDemandReason.MISSING_PROVENANCE_FIELD
    message = str(captured.value)
    assert "mods" in message and "game_version" in message


def test_unused_boundaries_and_demand_overlap_fail_closed():
    catalog = _catalog(_recipe("x-from-ore", [("ore", 1)], [("x", 1)]))

    unused = _error(
        catalog,
        demand={"x": 1},
        boundaries=[{"name": "ore", "rate": 1}, {"name": "z", "rate": 1}],
    )
    assert unused.reason is CatalogDemandReason.BOUNDARY_NOT_CONSUMED

    overlap = _error(
        catalog,
        demand={"x": 1},
        boundaries=[{"name": "x", "rate": 1}],
    )
    assert overlap.reason is CatalogDemandReason.DEMAND_BOUNDARY_CONFLICT


@pytest.mark.parametrize(
    "boundaries",
    [
        [{"name": "ore"}],
        [{"name": "ore", "rate": 1, "cap": 1}],
        [{"name": "ore", "rate": 0}],
        [{"name": "ore", "rate": "1"}],
        [{"name": "ore", "cap": -1}],
        [{"name": "ore", "rate": 1, "extra": 2}],
    ],
)
def test_invalid_boundary_shapes_fail_closed(boundaries):
    catalog = _catalog(_recipe("plate", [("ore", 1)], [("plate", 1)]))
    error = _error(catalog, demand={"plate": 1}, boundaries=boundaries)
    assert error.reason is CatalogDemandReason.INVALID_BOUNDARY


@pytest.mark.parametrize("demand", [{}, {"plate": 0}, {"plate": -1}, {"": 1}, {"plate": True}])
def test_invalid_demand_fails_closed(demand):
    catalog = _catalog(_recipe("plate", [("ore", 1)], [("plate", 1)]))
    error = _error(catalog, demand=demand, boundaries=[{"name": "ore", "rate": 1}])
    assert error.reason is CatalogDemandReason.INVALID_DEMAND


def test_source_cap_shortage_is_reported_without_claiming_physical_feasibility():
    catalog = _catalog(
        _recipe("plate-from-ore", [("ore", 2)], [("plate", 1)]),
        raw=("ore",),
    )
    result = _plan(
        catalog,
        demand={"plate": 1},
        boundaries=[{"name": "ore", "cap": 0}],
    )

    assert result.balance.status == "unique"
    assert result.balance.feasible is False
    assert result.balance.shortages[0].material == "ore"
    assert result.balance.shortages[0].required == Fraction(2)
    assert result.balance.shortages[0].cap == Fraction(0)
    assert result.balance.source_rates == (Fraction(2),)
    assert any("no machine" in note for note in result.notes)
    assert any("not live-game verification" in note for note in result.notes)


def test_historical_advanced_circuit_balance_uses_explicit_oil_and_ore_inputs():
    if not REAL_CATALOG.exists():
        pytest.skip("historical recipe catalog export is unavailable")
    data = REAL_CATALOG.read_bytes()
    catalog = json.loads(data)

    # force, tick, and the hash are the only observed facts; the export has no
    # surface or mod fields, so the synthetic provenance above cannot be
    # upgraded to a verified mod set or surface claim.
    assert catalog["force"] == "player"
    assert catalog["tick"] == 243688
    assert "surface" not in catalog
    assert "mods" not in catalog
    assert "fixture_provenance" not in catalog

    provenance = _real_provenance(data, catalog)
    with pytest.raises(CatalogDemandError) as captured:
        plan_catalog_demand(
            data,
            provenance=provenance,
            demand={"advanced-circuit": 1},
            boundaries=_advanced_circuit_boundaries(),
        )
    assert captured.value.reason is CatalogDemandReason.AMBIGUITY
    assert "advanced-circuit" in str(captured.value)

    result = plan_catalog_demand(
        data,
        provenance=provenance,
        demand={"advanced-circuit": 1},
        boundaries=_advanced_circuit_boundaries(),
        preferences=ADVANCED_CIRCUIT_PREFERENCES,
    )

    assert result.balance.status == "unique"
    assert result.balance.feasible is True
    assert set(result.selected_recipes) == {
        "advanced-circuit",
        "copper-cable",
        "copper-plate",
        "electronic-circuit",
        "iron-plate",
        "plastic-bar",
    }
    assert _rates(result) == {
        "advanced-circuit": Fraction(1),
        "copper-cable": Fraction(5),
        "copper-plate": Fraction(5),
        "electronic-circuit": Fraction(2),
        "iron-plate": Fraction(2),
        "plastic-bar": Fraction(1),
    }
    assert _sources(result) == {
        "copper-ore": Fraction(5),
        "iron-ore": Fraction(2),
    }
    assert {
        boundary.name: (boundary.rate, boundary.cap)
        for boundary in result.boundaries
    } == {
        "coal": (Fraction(1), None),
        "copper-ore": (None, Fraction(5)),
        "iron-ore": (None, Fraction(2)),
        "petroleum-gas": (Fraction(20), None),
    }
    assert {selection.basis for selection in result.selections} == {"preference"}

    # The oil products and coal come from the explicit boundaries; no selected
    # recipe fabricates an oil-processing or cracking chain.
    index = {recipe["name"]: recipe for recipe in catalog["recipes"]}
    produced = {
        part["name"]
        for name in result.selected_recipes
        for part in index[name]["products"]
    }
    consumed = {
        part["name"]
        for name in result.selected_recipes
        for part in index[name]["ingredients"]
    }
    assert "petroleum-gas" in consumed and "petroleum-gas" not in produced
    assert "coal" in consumed and "coal" not in produced
    assert "crude-oil" not in consumed
    assert not any("oil" in name for name in result.selected_recipes)

    assert result.provenance.force == "player"
    assert result.provenance.export_tick == 243688
    assert result.provenance.catalog_sha256 == hashlib.sha256(data).hexdigest()
    assert result.provenance.game_version == ASSUMED_GAME_VERSION
    assert result.provenance.surface == SYNTHETIC_SURFACE
    assert result.provenance.export_command == SYNTHETIC_EXPORT_COMMAND
    assert result.provenance.mods == (ModEntry("synthetic-test-mod", "0.0.0"),)
    assert result.notes == NON_CLAIMS


def test_historical_advanced_circuit_reports_copper_ore_cap_shortage():
    if not REAL_CATALOG.exists():
        pytest.skip("historical recipe catalog export is unavailable")
    data = REAL_CATALOG.read_bytes()
    catalog = json.loads(data)

    result = plan_catalog_demand(
        data,
        provenance=_real_provenance(data, catalog),
        demand={"advanced-circuit": 1},
        boundaries=_advanced_circuit_boundaries(copper_ore_cap=4),
        preferences=ADVANCED_CIRCUIT_PREFERENCES,
    )

    assert result.balance.status == "unique"
    assert result.balance.feasible is False
    assert result.balance.shortages == (
        SourceShortage(material="copper-ore", required=Fraction(5), cap=Fraction(4)),
    )
    assert "copper-ore" in result.balance.reason
    assert "above cap" in result.balance.reason
    # The cap only reports the shortfall; the solved recipe and source rates are
    # unchanged from the feasible case.
    assert _rates(result)["copper-plate"] == Fraction(5)
    assert _sources(result) == {
        "copper-ore": Fraction(5),
        "iron-ore": Fraction(2),
    }
