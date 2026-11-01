# Path: tests/test_science_line_candidate.py
# Purpose: Prove the offline science-line integration preserves exact recipe
# provenance, compares explicit counts honestly, rejects incompatible machine
# and graph selections, and never upgrades sandbox actions into a build claim.

from __future__ import annotations

from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest

import core.science_line_candidate as science_line_candidate_module
from core.catalog_demand import mod_set_sha256, plan_catalog_demand
from core.science_line_candidate import (
    NON_CLAIMS,
    ScienceLineCandidateError,
    ScienceLineCandidateReason,
    build_science_line_candidate,
)
from planners.science_block_geometry import build_science_block_geometry

FIXTURES = Path(__file__).parent / "fixtures"
PLAYER_CATALOG = json.loads((FIXTURES / "player_recipe_catalog.json").read_text())

_GEAR_PREFERENCES = {
    "iron-gear-wheel": "iron-gear-wheel",
    "iron-plate": "iron-plate",
}
_CIRCUIT_PREFERENCES = {
    "copper-cable": "copper-cable",
    "copper-plate": "copper-plate",
    "electronic-circuit": "electronic-circuit",
    "iron-plate": "iron-plate",
}
_PACK_PREFERENCES = {
    "automation-science-pack": "automation-science-pack",
    "copper-plate": "copper-plate",
    "iron-gear-wheel": "iron-gear-wheel",
    "iron-plate": "iron-plate",
}
_ORE_CAPS = [{"name": "copper-ore", "cap": 5}, {"name": "iron-ore", "cap": 5}]
_MACHINE_CHOICES = {
    "automation-science-pack": "assembling-machine-2",
    "copper-cable": "assembling-machine-2",
    "copper-plate": "electric-furnace",
    "electronic-circuit": "assembling-machine-2",
    "iron-gear-wheel": "assembling-machine-2",
    "iron-plate": "electric-furnace",
}


def _catalog() -> dict:
    """The tracked player export plus explicit, existing prototype machines."""

    catalog = json.loads(json.dumps(PLAYER_CATALOG))
    catalog["machines"] = [
        {
            "name": "assembling-machine-2",
            "type": "assembling-machine",
            "ingredient_count": 65535,
            "crafting_speed": 0.75,
            "categories": [
                "advanced-crafting",
                "crafting",
                "crafting-with-fluid",
                "electronics",
                "electronics-with-fluid",
                "parameters",
                "pressing",
            ],
        },
        {
            "name": "assembling-machine-3",
            "type": "assembling-machine",
            "ingredient_count": 65535,
            "crafting_speed": 1.25,
            "categories": [
                "advanced-crafting",
                "crafting",
                "crafting-with-fluid",
                "electronics",
                "electronics-with-fluid",
                "parameters",
                "pressing",
            ],
        },
        {
            "name": "electric-furnace",
            "type": "furnace",
            "ingredient_count": 1,
            "crafting_speed": 2,
            "categories": ["smelting"],
        },
    ]
    return catalog


def _bytes(catalog: dict) -> bytes:
    return json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _provenance(data: bytes, catalog: dict) -> dict:
    mods: list[dict] = []
    return {
        "catalog_sha256": hashlib.sha256(data).hexdigest(),
        "game_version": "2.1.21",
        "force": catalog["force"],
        "surface": "synthetic-test-surface",
        "export_tick": catalog["tick"],
        "export_command": "synthetic-test-export",
        "mods": mods,
        "mod_set_sha256": mod_set_sha256(mods),
    }


def _demand(
    catalog: dict,
    *,
    demand: dict,
    boundaries: list[dict],
    preferences: dict,
):
    data = _bytes(catalog)
    result = plan_catalog_demand(
        data,
        provenance=_provenance(data, catalog),
        demand=demand,
        boundaries=boundaries,
        preferences=preferences,
    )
    return data, result


def _line_spec(recipe: str, machine_count: int, **overrides) -> dict:
    spec = {
        "recipe": recipe,
        "machine_count": machine_count,
        "origin_x": 0,
        "origin_y": 0,
        "belt_type": "transport-belt",
        "inserter_type": "fast-inserter",
        "mining_feed": False,
        "feed_style": "chained",
        "direct_bus_ingredients": [],
        "chained_ingredients": [],
        "terminal_collector": False,
    }
    spec.update(overrides)
    return spec


def _machine_choices(result, **overrides) -> dict[str, str]:
    choices = {**_MACHINE_CHOICES, **overrides}
    return {name: choices[name] for name in result.selected_recipes}


def test_low_density_structure_historical_three_input_row_is_catalog_checked():
    path = Path("data/factorio-2.1.21/probes/deterministic/server/script-output/factorio_mod/recipe_catalogs/recipe_catalog_243688.json")
    if not path.is_file():
        pytest.skip("historical catalog unavailable")
    data = path.read_bytes()
    catalog = json.loads(data)
    result = plan_catalog_demand(
        data, provenance=_provenance(data, catalog), demand={"low-density-structure": Fraction(1, 4)},
        boundaries=[{"name": name, "cap": 100} for name in ("copper-plate", "plastic-bar", "steel-plate")],
        preferences={"low-density-structure": "low-density-structure"},
    )
    candidate = build_science_line_candidate(
        result, catalog, recipe="low-density-structure",
        line_spec=_line_spec("low-density-structure", 5, chained_ingredients=[0, 1, 2]),
        machine_by_recipe={"low-density-structure": "assembling-machine-2"}, catalog_bytes=data,
    )
    assert candidate.required_machine_count == 5
    assert candidate.count_status == "adequate"
    assert candidate.ghost_entities["assembling-machine-2"] == 5
    assert candidate.ghost_entities["long-handed-inserter"] == 5
    assert candidate.player_force_build_claim is False


@pytest.mark.parametrize("mutation", ["ingredient", "product", "duration", "speed"])
def test_line_rejects_recomputed_catalog_that_disagrees_with_geometry(mutation):
    catalog = _catalog()
    recipe = next(entry for entry in catalog["recipes"] if entry["name"] == "iron-gear-wheel")
    if mutation == "ingredient":
        recipe["ingredients"][0]["amount"] += 1
    elif mutation == "product":
        recipe["products"][0]["amount"] += 1
    elif mutation == "duration":
        recipe["energy_ticks"] += 60
    else:
        next(entry for entry in catalog["machines"] if entry["name"] == "assembling-machine-2")["crafting_speed"] = 1.5
    data, demand = _demand(catalog, demand={"iron-gear-wheel": Fraction(1, 4)},
                           boundaries=[{"name": "iron-plate", "cap": 100}],
                           preferences={"iron-gear-wheel": "iron-gear-wheel"})
    with pytest.raises(ScienceLineCandidateError, match="line geometry assumptions"):
        build_science_line_candidate(
            demand, catalog, recipe="iron-gear-wheel", line_spec=_line_spec("iron-gear-wheel", 1, chained_ingredients=[0]),
            machine_by_recipe={"iron-gear-wheel": "assembling-machine-2"}, catalog_bytes=data,
        )


@pytest.mark.parametrize(
    "recipe, demand, boundaries, preferences, chained, required, exact",
    [
        (
            "iron-gear-wheel",
            {"iron-gear-wheel": 1},
            [{"name": "iron-ore", "cap": 5}],
            _GEAR_PREFERENCES,
            [0],
            1,
            Fraction(2, 3),
        ),
        (
            "electronic-circuit",
            {"electronic-circuit": 1},
            _ORE_CAPS,
            _CIRCUIT_PREFERENCES,
            [0, 1],
            1,
            Fraction(2, 3),
        ),
        (
            "automation-science-pack",
            {"automation-science-pack": 1},
            _ORE_CAPS,
            _PACK_PREFERENCES,
            [0, 1],
            7,
            Fraction(20, 3),
        ),
    ],
)
def test_explicit_gear_circuit_and_pack_lines_select_the_geometry_machine(
    recipe, demand, boundaries, preferences, chained, required, exact
):
    catalog = _catalog()
    data, result = _demand(
        catalog,
        demand=demand,
        boundaries=boundaries,
        preferences=preferences,
    )
    spec = _line_spec(recipe, required, chained_ingredients=chained)

    candidate = build_science_line_candidate(
        result,
        catalog,
        recipe=recipe,
        line_spec=spec,
        machine_by_recipe=_machine_choices(result),
        catalog_bytes=data,
    )

    assert candidate.recipe == recipe
    assert candidate.machine == "assembling-machine-2"
    assert candidate.machine_fact.recipe == recipe
    assert candidate.requested_machine_count == required
    assert candidate.required_machine_count == required
    assert candidate.exact_machines == exact
    assert candidate.count_status == "adequate"
    assert candidate.machine_shortfall == 0
    assert candidate.machine_spare == 0
    assert candidate.geometry["ghost_entities"][candidate.machine] == required
    assert candidate.counts["direct_placements"] == 0
    assert candidate.deployable is False
    assert candidate.player_force_build_claim is False
    assert candidate.notes == NON_CLAIMS
    assert set(candidate.unknowns) >= {
        "site", "power", "capacity", "transport", "bot_funding", "runtime_throughput",
    }


def test_an_undersized_explicit_count_is_reported_without_changing_geometry():
    catalog = _catalog()
    data, result = _demand(
        catalog,
        demand={"automation-science-pack": 1},
        boundaries=_ORE_CAPS,
        preferences=_PACK_PREFERENCES,
    )
    spec = _line_spec("automation-science-pack", 3, chained_ingredients=[0, 1])

    candidate = build_science_line_candidate(
        result,
        catalog,
        recipe="automation-science-pack",
        line_spec=spec,
        machine_by_recipe=_machine_choices(result),
        catalog_bytes=data,
    )

    assert candidate.required_machine_count == 7
    assert candidate.requested_machine_count == 3
    assert candidate.count_status == "undersized"
    assert candidate.machine_shortfall == 4
    assert candidate.machine_spare == 0
    # The geometry is left exactly as explicitly requested.
    assert candidate.geometry["spec"]["machine_count"] == 3
    assert candidate.geometry["ghost_entities"]["assembling-machine-2"] == 3
    assert candidate.machine_fact.machine_count == 7


def test_a_machine_tier_that_disagrees_with_the_geometry_is_rejected():
    catalog = _catalog()
    data, result = _demand(
        catalog,
        demand={"automation-science-pack": 1},
        boundaries=_ORE_CAPS,
        preferences=_PACK_PREFERENCES,
    )
    spec = _line_spec("automation-science-pack", 7, chained_ingredients=[0, 1])

    with pytest.raises(ScienceLineCandidateError) as captured:
        build_science_line_candidate(
            result,
            catalog,
            recipe="automation-science-pack",
            line_spec=spec,
            machine_by_recipe=_machine_choices(
                result, **{"automation-science-pack": "assembling-machine-3"}
            ),
            catalog_bytes=data,
        )

    assert captured.value.reason is ScienceLineCandidateReason.MACHINE_GEOMETRY_MISMATCH
    assert "assembling-machine-3" in str(captured.value)
    assert "assembling-machine-2" in str(captured.value)


def test_a_catalog_that_is_not_the_provenanced_one_is_rejected():
    catalog = _catalog()
    data, result = _demand(
        catalog,
        demand={"automation-science-pack": 1},
        boundaries=_ORE_CAPS,
        preferences=_PACK_PREFERENCES,
    )
    spec = _line_spec("automation-science-pack", 7, chained_ingredients=[0, 1])

    altered = json.loads(json.dumps(catalog))
    recipe = next(
        item for item in altered["recipes"] if item["name"] == "automation-science-pack"
    )
    recipe["ingredients"][1]["amount"] = 2

    # The passed mapping no longer matches the provenanced bytes.
    with pytest.raises(ScienceLineCandidateError) as bytes_mismatch:
        build_science_line_candidate(
            result,
            altered,
            recipe="automation-science-pack",
            line_spec=spec,
            machine_by_recipe=_machine_choices(result),
            catalog_bytes=data,
        )
    assert bytes_mismatch.value.reason is ScienceLineCandidateReason.CATALOG_MISMATCH
    assert "mapping differs" in str(bytes_mismatch.value)

    # Altered bytes with the original provenance are rejected by the hash even
    # when force, tick and every recipe part are unchanged.
    with pytest.raises(ScienceLineCandidateError) as hash_mismatch:
        build_science_line_candidate(
            result,
            altered,
            recipe="automation-science-pack",
            line_spec=spec,
            machine_by_recipe=_machine_choices(result),
            catalog_bytes=_bytes(altered),
        )
    assert hash_mismatch.value.reason is ScienceLineCandidateReason.CATALOG_MISMATCH
    assert "hash" in str(hash_mismatch.value)


def test_an_unsupported_graph_is_rejected_before_inventing_machines():
    catalog = _catalog()
    preferences = {
        material: material
        for material in (
            "advanced-circuit",
            "copper-cable",
            "copper-plate",
            "electronic-circuit",
            "iron-plate",
            "plastic-bar",
            "processing-unit",
            "sulfur",
            "sulfuric-acid",
        )
    }
    data, result = _demand(
        catalog,
        demand={"processing-unit": 1},
        boundaries=[
            {"name": "coal", "cap": 100},
            {"name": "copper-ore", "cap": 200},
            {"name": "iron-ore", "cap": 100},
            {"name": "petroleum-gas", "cap": 1000},
            {"name": "water", "cap": 2000},
        ],
        preferences=preferences,
    )
    assert "sulfuric-acid" in result.selected_recipes
    spec = _line_spec("processing-unit", 1, chained_ingredients=[0, 1])

    with pytest.raises(ScienceLineCandidateError) as captured:
        build_science_line_candidate(
            result,
            catalog,
            recipe="processing-unit",
            line_spec=spec,
            machine_by_recipe={
                name: "assembling-machine-2" for name in result.selected_recipes
            },
            catalog_bytes=data,
        )

    assert captured.value.reason is ScienceLineCandidateReason.UNSUPPORTED_GRAPH
    assert "sulfuric-acid" in str(captured.value)


def test_sandbox_direct_actions_stay_gross_and_are_marked_non_executable():
    catalog = _catalog()
    data, result = _demand(
        catalog,
        demand={"electronic-circuit": 1},
        boundaries=_ORE_CAPS,
        preferences=_CIRCUIT_PREFERENCES,
    )
    spec = _line_spec(
        "electronic-circuit",
        1,
        mining_feed=True,
        feed_style="chest",
        terminal_collector=True,
    )

    candidate = build_science_line_candidate(
        result,
        catalog,
        recipe="electronic-circuit",
        line_spec=spec,
        machine_by_recipe=_machine_choices(result),
        catalog_bytes=data,
    )

    assert candidate.count_status == "adequate"
    assert candidate.has_direct_actions is True
    assert candidate.non_executable_marker.startswith("NON_EXECUTABLE_OFFLINE_GEOMETRY")
    assert "2 direct entity placement(s)" in candidate.non_executable_marker
    assert candidate.counts == candidate.geometry["counts"]
    assert candidate.counts["direct_placements"] == 2
    assert candidate.direct_entities == {"fast-inserter": 1, "steel-chest": 1}
    # Direct placements stay in place_entity actions; they are not re-billed as
    # construction ghosts by the integration.
    assert "steel-chest" not in candidate.ghost_entities
    assert sum(
        1
        for phase in candidate.geometry["plan"]["phases"]
        for action in phase["actions"]
        if action.get("action_type") == "place_entity"
    ) == 2
    assert candidate.removed_entities == {}
    assert candidate.deployable is False
    assert candidate.player_force_build_claim is False


def test_an_explicit_requested_count_that_disagrees_with_the_spec_is_rejected():
    catalog = _catalog()
    data, result = _demand(
        catalog,
        demand={"iron-gear-wheel": 1},
        boundaries=[{"name": "iron-ore", "cap": 5}],
        preferences=_GEAR_PREFERENCES,
    )
    spec = _line_spec("iron-gear-wheel", 1, chained_ingredients=[0])

    with pytest.raises(ScienceLineCandidateError) as captured:
        build_science_line_candidate(
            result,
            catalog,
            recipe="iron-gear-wheel",
            line_spec=spec,
            machine_by_recipe=_machine_choices(result),
            requested_machine_count=2,
            catalog_bytes=data,
        )

    assert captured.value.reason is ScienceLineCandidateReason.EXPLICIT_COUNT_DISAGREEMENT


def test_absent_catalog_bytes_are_rejected():
    catalog = _catalog()
    data, result = _demand(
        catalog,
        demand={"iron-gear-wheel": 1},
        boundaries=[{"name": "iron-ore", "cap": 5}],
        preferences=_GEAR_PREFERENCES,
    )
    spec = _line_spec("iron-gear-wheel", 1, chained_ingredients=[0])

    with pytest.raises(ScienceLineCandidateError) as captured:
        build_science_line_candidate(
            result,
            catalog,
            recipe="iron-gear-wheel",
            line_spec=spec,
            machine_by_recipe=_machine_choices(result),
            catalog_bytes=None,
        )

    assert captured.value.reason is ScienceLineCandidateReason.INVALID_CATALOG
    assert "catalog_bytes" in str(captured.value)


def test_a_catalog_that_changes_only_machine_speed_is_rejected():
    catalog = _catalog()
    data, result = _demand(
        catalog,
        demand={"automation-science-pack": 1},
        boundaries=_ORE_CAPS,
        preferences=_PACK_PREFERENCES,
    )
    spec = _line_spec("automation-science-pack", 7, chained_ingredients=[0, 1])

    altered = json.loads(json.dumps(catalog))
    machine = next(
        item for item in altered["machines"] if item["name"] == "assembling-machine-2"
    )
    machine["crafting_speed"] = 1.5

    assert altered["force"] == catalog["force"]
    assert altered["tick"] == catalog["tick"]
    assert altered["recipes"] == catalog["recipes"]

    with pytest.raises(ScienceLineCandidateError) as mapping_mismatch:
        build_science_line_candidate(
            result,
            altered,
            recipe="automation-science-pack",
            line_spec=spec,
            machine_by_recipe=_machine_choices(result),
            catalog_bytes=data,
        )
    assert mapping_mismatch.value.reason is ScienceLineCandidateReason.CATALOG_MISMATCH
    assert "mapping differs" in str(mapping_mismatch.value)

    with pytest.raises(ScienceLineCandidateError) as hash_mismatch:
        build_science_line_candidate(
            result,
            altered,
            recipe="automation-science-pack",
            line_spec=spec,
            machine_by_recipe=_machine_choices(result),
            catalog_bytes=_bytes(altered),
        )
    assert hash_mismatch.value.reason is ScienceLineCandidateReason.CATALOG_MISMATCH
    assert "hash" in str(hash_mismatch.value)


def test_machine_by_recipe_must_be_explicit_and_complete():
    catalog = _catalog()
    data, result = _demand(
        catalog,
        demand={"automation-science-pack": 1},
        boundaries=_ORE_CAPS,
        preferences=_PACK_PREFERENCES,
    )
    spec = _line_spec("automation-science-pack", 7, chained_ingredients=[0, 1])

    with pytest.raises(ScienceLineCandidateError) as missing:
        build_science_line_candidate(
            result,
            catalog,
            recipe="automation-science-pack",
            line_spec=spec,
            machine_by_recipe=None,
            catalog_bytes=data,
        )
    assert missing.value.reason is ScienceLineCandidateReason.INVALID_MACHINE_SELECTION
    assert "explicitly map" in str(missing.value)

    partial = _machine_choices(result)
    partial.pop("iron-plate")
    with pytest.raises(ScienceLineCandidateError) as incomplete:
        build_science_line_candidate(
            result,
            catalog,
            recipe="automation-science-pack",
            line_spec=spec,
            machine_by_recipe=partial,
            catalog_bytes=data,
        )
    assert (
        incomplete.value.reason
        is ScienceLineCandidateReason.INVALID_MACHINE_SELECTION
    )
    assert "iron-plate" in str(incomplete.value)


@pytest.mark.parametrize(
    "ghost, detail",
    [
        (
            {
                "action_type": "place_ghost",
                "entity": "assembling-machine-3",
                "position": {"x": 100.5, "y": 100.5},
                "recipe": "automation-science-pack",
            },
            "assembling-machine-3",
        ),
        (
            {
                "action_type": "place_ghost",
                "entity": "assembling-machine-2",
                "position": {"x": 100.5, "y": 100.5},
                "recipe": "iron-gear-wheel",
            },
            "iron-gear-wheel",
        ),
    ],
)
def test_all_geometry_production_machine_ghosts_must_match_tier_and_recipe(
    monkeypatch, ghost, detail
):
    catalog = _catalog()
    data, result = _demand(
        catalog,
        demand={"automation-science-pack": 1},
        boundaries=_ORE_CAPS,
        preferences=_PACK_PREFERENCES,
    )
    spec = _line_spec("automation-science-pack", 7, chained_ingredients=[0, 1])

    geometry = json.loads(json.dumps(build_science_block_geometry(spec)))
    geometry["plan"]["phases"][-1]["actions"].append(ghost)
    monkeypatch.setattr(
        science_line_candidate_module,
        "build_science_block_geometry",
        lambda _spec: geometry,
    )

    with pytest.raises(ScienceLineCandidateError) as captured:
        build_science_line_candidate(
            result,
            catalog,
            recipe="automation-science-pack",
            line_spec=spec,
            machine_by_recipe=_machine_choices(result),
            catalog_bytes=data,
        )

    assert captured.value.reason is ScienceLineCandidateReason.MACHINE_GEOMETRY_MISMATCH
    assert detail in str(captured.value)
