# Path: tests/test_science_recipe_graph.py
# Purpose: Verify complete structural science knowledge and the current Nauvis-only execution boundary.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.recipe_dag import compile_recipe_dag
from core.science_recipe_graph import (
    ALL_SCIENCE_PACKS,
    NAUVIS_SCIENCE_PACKS,
    ScienceLocation,
    compile_all_science_chains,
    compile_science_chain,
    validate_nauvis_execution_target,
)
from orchestrator.autonomous_builder import StuckError, run

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads(
    (ROOT / "tests" / "fixtures" / "player_recipe_catalog.json").read_text(
        encoding="utf-8"
    )
)


def test_science_registry_covers_the_twelve_space_age_packs() -> None:
    assert NAUVIS_SCIENCE_PACKS == (
        "automation-science-pack",
        "logistic-science-pack",
        "chemical-science-pack",
        "production-science-pack",
        "utility-science-pack",
        "military-science-pack",
    )
    assert ALL_SCIENCE_PACKS == (
        *NAUVIS_SCIENCE_PACKS,
        "space-science-pack",
        "metallurgic-science-pack",
        "agricultural-science-pack",
        "electromagnetic-science-pack",
        "cryogenic-science-pack",
        "promethium-science-pack",
    )


@pytest.mark.parametrize("science_pack", ALL_SCIENCE_PACKS)
def test_every_science_pack_has_a_complete_structural_chain(
    science_pack: str,
) -> None:
    chain = compile_science_chain(science_pack, CATALOG)

    assert chain["target"] == science_pack
    assert science_pack in {recipe["name"] for recipe in chain["recipes"]}
    assert chain["unresolved_inputs"] == []
    assert chain["ungrounded_inputs"] == []
    assert chain["complete"] is True


def test_all_science_compilation_is_deterministic_and_preserves_locations() -> None:
    first = compile_all_science_chains(CATALOG)
    second = compile_all_science_chains(CATALOG)

    assert first == second
    assert first["automation-science-pack"]["location"] == ScienceLocation.NAUVIS.value
    assert first["space-science-pack"]["location"] == ScienceLocation.ORBIT.value
    assert (
        first["metallurgic-science-pack"]["location"] == ScienceLocation.VULCANUS.value
    )
    assert first["agricultural-science-pack"]["location"] == ScienceLocation.GLEBA.value
    assert (
        first["electromagnetic-science-pack"]["location"]
        == ScienceLocation.FULGORA.value
    )
    assert first["cryogenic-science-pack"]["location"] == ScienceLocation.AQUILO.value
    assert (
        first["promethium-science-pack"]["location"] == ScienceLocation.DEEP_SPACE.value
    )


def test_structural_chains_keep_real_alternatives_but_drop_recovery_loops() -> None:
    chemical = compile_science_chain("chemical-science-pack", CATALOG)
    agricultural = compile_science_chain("agricultural-science-pack", CATALOG)
    automation = compile_science_chain("automation-science-pack", CATALOG)
    electromagnetic = compile_science_chain("electromagnetic-science-pack", CATALOG)

    petroleum_routes = chemical["producer_choices"]["petroleum-gas"]
    assert "advanced-oil-processing" in petroleum_routes
    assert "basic-oil-processing" in petroleum_routes
    assert "empty-petroleum-gas-barrel" not in petroleum_routes
    assert agricultural["producer_choices"]["jelly"] == ["jellynut-processing"]
    assert electromagnetic["producer_choices"]["holmium-ore"] == ["scrap-recycling"]
    assert automation["producer_choices"]["copper-plate"] == [
        "casting-copper",
        "copper-plate",
    ]
    assert automation["primary_producers"]["copper-plate"] == "copper-plate"


def test_structural_chain_keeps_coproduct_and_probabilistic_knowledge() -> None:
    cryogenic = compile_science_chain("cryogenic-science-pack", CATALOG)
    agricultural = compile_science_chain("agricultural-science-pack", CATALOG)

    cryogenic_recipe = next(
        recipe
        for recipe in cryogenic["recipes"]
        if recipe["name"] == "cryogenic-science-pack"
    )
    jelly_recipe = next(
        recipe
        for recipe in agricultural["recipes"]
        if recipe["name"] == "jellynut-processing"
    )
    assert {product["name"] for product in cryogenic_recipe["products"]} == {
        "cryogenic-science-pack",
        "fluoroketone-hot",
    }
    assert jelly_recipe["supported"] is False
    assert "probabilistic product" in jelly_recipe["unsupported_reason"]


def test_ungrounded_recipe_cycle_is_not_complete() -> None:
    catalog = {
        "version": "1.0.0",
        "force": "player",
        "tick": 0,
        "raw_resources": [],
        "recipes": [
            {
                "name": "automation-science-pack",
                "enabled": False,
                "category": "crafting",
                "energy_ticks": 60,
                "ingredients": [{"name": "loop", "type": "item", "amount": 1}],
                "products": [
                    {"name": "automation-science-pack", "type": "item", "amount": 1}
                ],
                "supported": True,
            },
            {
                "name": "loop",
                "enabled": False,
                "category": "crafting",
                "energy_ticks": 60,
                "ingredients": [{"name": "loop", "type": "item", "amount": 1}],
                "products": [{"name": "loop", "type": "item", "amount": 2}],
                "supported": True,
            },
        ],
    }

    chain = compile_science_chain("automation-science-pack", catalog)

    assert chain["complete"] is False
    assert chain["ungrounded_inputs"] == ["automation-science-pack", "loop"]
    assert chain["cycles"] == [["loop", "loop"]]


def test_exact_rate_dag_prefers_the_same_named_primary_recipe() -> None:
    goal = {
        "version": "1.0.0",
        "target": "automation-science-pack",
        "delta": {"rate": 1, "unit": "per_second"},
    }

    graph = compile_recipe_dag(goal, CATALOG)

    assert graph["topological_order"][-1] == "automation-science-pack"
    assert "copper-plate" in graph["topological_order"]
    assert not any(name.endswith("-recycling") for name in graph["topological_order"])


@pytest.mark.parametrize("science_pack", NAUVIS_SCIENCE_PACKS)
def test_six_nauvis_sciences_are_inside_the_current_execution_scope(
    science_pack: str,
) -> None:
    validate_nauvis_execution_target(science_pack, "nauvis")


@pytest.mark.parametrize(
    "science_pack",
    tuple(pack for pack in ALL_SCIENCE_PACKS if pack not in NAUVIS_SCIENCE_PACKS),
)
def test_six_non_nauvis_sciences_are_knowledge_only(science_pack: str) -> None:
    with pytest.raises(ValueError, match="knowledge-only"):
        validate_nauvis_execution_target(science_pack, "nauvis")


def test_live_builder_rejects_out_of_scope_work_before_connecting() -> None:
    with pytest.raises(StuckError, match="knowledge-only"):
        run("space-science-pack", surface="nauvis")
    with pytest.raises(StuckError, match="Nauvis-only"):
        run("automation-science-pack", surface="gleba")
    with pytest.raises(StuckError, match="No executable recipe knowledge"):
        run("chemical-science-pack", surface="nauvis")


def test_builder_preflights_the_complete_chain_before_connecting(monkeypatch) -> None:
    from orchestrator import autonomous_builder

    monkeypatch.setitem(
        autonomous_builder.LINE_RECIPES,
        "incomplete-science-pack",
        {
            "machine": "assembling-machine-2",
            "ingredients": ["missing-intermediate"],
            "amounts": [1],
            "product_amount": 1,
            "craft_time": 1.0,
        },
    )

    with pytest.raises(StuckError, match="missing-intermediate"):
        run("incomplete-science-pack", surface="nauvis")


def test_lua_catalog_exports_locked_recipes_and_environmental_sources() -> None:
    source = (ROOT / "factorio_mod" / "recipe_catalog.lua").read_text(encoding="utf-8")

    assert "enabled = recipe.enabled" in source
    assert "recipe.enabled and" not in source
    assert "environmental_source_types" in source
    assert "environmental_source_types[prototype.type]" in source
    assert "prototypes.asteroid_chunk" in source
    assert '["simple-entity"]' not in source
