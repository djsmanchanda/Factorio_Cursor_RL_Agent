# Path: tests/test_ghost_line_artifact.py
# Purpose: Prove the offline ghost artifact preserves explicit science-line
# geometry and configuration, converts only ordinary terminal collectors,
# reports caller-declared mapped item counts (never prototype-name guesses),
# and fails closed on anything a bot cannot build.

from __future__ import annotations

import json

import pytest

from orchestrator.material_reservations import plan_material_bill
from planners.ghost_line_artifact import (
    ARTIFACT_STATUS,
    ARTIFACT_VERSION,
    derive_ghost_artifact,
)
from planners.plan_validation import validate_build_plan
from planners.science_block_geometry import build_science_block_geometry


def _spec(**overrides):
    spec = {
        "recipe": "iron-plate",
        "machine_count": 3,
        "origin_x": 10,
        "origin_y": 20,
        "belt_type": "transport-belt",
        "inserter_type": "fast-inserter",
        "mining_feed": True,
        "feed_style": "chest",
        "direct_bus_ingredients": [],
        "chained_ingredients": [],
        "terminal_collector": True,
    }
    spec.update(overrides)
    return spec


def _source_plan(**overrides):
    return build_science_block_geometry(_spec(**overrides))["plan"]


def _actions(plan):
    return [action for phase in plan["phases"] for action in phase["actions"]]


def _expected_ghost_plan(source):
    phases = []
    for phase in source["phases"]:
        actions = []
        for action in phase["actions"]:
            if action["action_type"] == "place_entity":
                ghost = {
                    "action_type": "place_ghost",
                    "entity": action["entity"],
                    "position": action["position"],
                }
                if "direction" in action:
                    ghost["direction"] = action["direction"]
                actions.append(ghost)
            else:
                actions.append(action)
        phases.append({"name": phase["name"], "actions": actions})
    return {"phases": phases}


def _single(action):
    return {"phases": [{"name": "source", "actions": [action]}]}


_SCIENCE_ENTITY_ITEMS = {
    "electric-furnace": "electric-furnace",
    "electric-mining-drill": "electric-mining-drill",
    "fast-inserter": "fast-inserter",
    "medium-electric-pole": "medium-electric-pole",
    "steel-chest": "steel-chest",
    "transport-belt": "transport-belt",
}


def _mapping(
    entities=None,
    tiles=None,
    provenance="test hand-authored factorio-data prototype table",
):
    return {
        "provenance": provenance,
        "entities": dict(
            _SCIENCE_ENTITY_ITEMS if entities is None else entities
        ),
        "tiles": {} if tiles is None else dict(tiles),
    }


def _empty_mapping(**overrides):
    mapping = {"provenance": "test explicit map", "entities": {}, "tiles": {}}
    mapping.update(overrides)
    return mapping


def test_explicit_science_geometry_becomes_the_same_ghost_geometry():
    source = _source_plan()
    artifact = derive_ghost_artifact(source, item_mapping=_mapping())

    assert artifact["plan"] == _expected_ghost_plan(source)
    assert artifact["counts"] == {
        "ghost_placements": 38,
        "direct_placements": 0,
        "removals": 0,
        "converted_direct_placements": 2,
    }
    assert [
        (record["entity"], record["action_index"], record["direction"])
        for record in artifact["converted_direct_actions"]
    ] == [
        ("steel-chest", 0, None),
        ("fast-inserter", 1, "west"),
    ]
    assert artifact["converted_direct_actions"][1]["position"] == {
        "x": 19.5, "y": 26.5,
    }

    validate_build_plan(artifact["plan"])
    assert {
        action["action_type"] for action in _actions(artifact["plan"])
    } == {"place_ghost"}


def test_normal_geometry_reports_explicitly_mapped_item_counts():
    source = _source_plan()
    artifact = derive_ghost_artifact(source, item_mapping=_mapping())

    assert artifact["declared_item_bill"] == {
        "electric-furnace": 3,
        "electric-mining-drill": 3,
        "fast-inserter": 7,
        "medium-electric-pole": 4,
        "steel-chest": 1,
        "transport-belt": 20,
    }
    assert artifact["prototype_action_counts"] == {
        "entity": dict(sorted({
            "electric-furnace": 3,
            "electric-mining-drill": 3,
            "fast-inserter": 7,
            "medium-electric-pole": 4,
            "steel-chest": 1,
            "transport-belt": 20,
        }.items())),
        "tile": {},
    }
    assert sum(artifact["declared_item_bill"].values()) == (
        artifact["counts"]["ghost_placements"]
    )
    assert artifact["item_bill_basis"] == (
        "caller_declared_prototype_item_mapping"
    )


def test_a_ghost_only_source_is_preserved_unchanged():
    source = _source_plan(
        recipe="electronic-circuit",
        machine_count=2,
        origin_x=0,
        origin_y=0,
        belt_type="express-transport-belt",
        feed_style="chained",
        mining_feed=False,
        chained_ingredients=[0, 1],
        terminal_collector=False,
    )
    assert not [
        action for action in _actions(source)
        if action["action_type"] != "place_ghost"
    ]

    artifact = derive_ghost_artifact(
        source,
        item_mapping=_mapping(entities={
            "assembling-machine-2": "assembling-machine-2",
            "express-transport-belt": "express-transport-belt",
            "fast-inserter": "fast-inserter",
            "medium-electric-pole": "medium-electric-pole",
        }),
    )

    assert artifact["plan"] == source
    assert artifact["counts"]["converted_direct_placements"] == 0
    assert artifact["declared_item_bill"] == {
        "assembling-machine-2": 2,
        "express-transport-belt": 15,
        "fast-inserter": 4,
        "medium-electric-pole": 4,
    }


def test_tile_ghosts_are_preserved_and_mapped():
    source = _single(
        {"action_type": "place_tile_ghost", "tile": "landfill",
         "position": {"x": 2, "y": 2}},
    )

    artifact = derive_ghost_artifact(
        source,
        item_mapping=_empty_mapping(tiles={"landfill": "landfill"}),
    )

    assert artifact["plan"] == source
    assert artifact["declared_item_bill"] == {"landfill": 1}
    assert artifact["prototype_action_counts"] == {
        "entity": {}, "tile": {"landfill": 1},
    }
    assert artifact["counts"]["ghost_placements"] == 1


def test_entity_prototype_name_is_not_used_as_the_item_name():
    source = _single(
        {"action_type": "place_ghost", "entity": "straight-rail",
         "position": {"x": 1, "y": 1}},
    )
    source["phases"][0]["actions"].append(
        {"action_type": "place_ghost", "entity": "straight-rail",
         "position": {"x": 3, "y": 1}},
    )

    artifact = derive_ghost_artifact(
        source,
        item_mapping=_empty_mapping(entities={"straight-rail": "rail"}),
    )

    assert artifact["declared_item_bill"] == {"rail": 2}
    assert artifact["prototype_action_counts"]["entity"] == {
        "straight-rail": 2,
    }
    assert "straight-rail" not in artifact["declared_item_bill"]
    assert plan_material_bill(artifact["plan"]) == {"straight-rail": 2}


def test_tile_prototype_name_is_not_used_as_the_item_name():
    source = _single(
        {"action_type": "place_tile_ghost", "tile": "hazard-concrete-left",
         "position": {"x": 0, "y": 0}},
    )

    artifact = derive_ghost_artifact(
        source,
        item_mapping=_empty_mapping(
            tiles={"hazard-concrete-left": "hazard-concrete"},
        ),
    )

    assert artifact["declared_item_bill"] == {"hazard-concrete": 1}
    assert artifact["prototype_action_counts"]["tile"] == {
        "hazard-concrete-left": 1,
    }
    assert plan_material_bill(artifact["plan"]) == {
        "hazard-concrete-left": 1,
    }


def test_multiple_prototypes_aggregate_into_one_mapped_item():
    source = {"phases": [{"name": "source", "actions": [
        {"action_type": "place_ghost", "entity": "straight-rail",
         "position": {"x": 1, "y": 1}},
        {"action_type": "place_ghost", "entity": "curved-rail",
         "position": {"x": 5, "y": 5}},
        {"action_type": "place_ghost", "entity": "straight-rail",
         "position": {"x": 9, "y": 9}},
    ]}]}

    artifact = derive_ghost_artifact(
        source,
        item_mapping=_empty_mapping(
            entities={"straight-rail": "rail", "curved-rail": "rail"},
        ),
    )

    assert artifact["declared_item_bill"] == {"rail": 3}
    assert artifact["prototype_action_counts"]["entity"] == {
        "curved-rail": 1, "straight-rail": 2,
    }
    assert artifact["counts"]["ghost_placements"] == 3


def test_same_named_prototype_mapping_is_used_without_name_fallback():
    source = _single(
        {"action_type": "place_ghost", "entity": "steel-chest",
         "position": {"x": 2.5, "y": 2.5}},
    )

    artifact = derive_ghost_artifact(
        source,
        item_mapping=_empty_mapping(entities={"steel-chest": "wooden-chest"}),
    )

    assert artifact["declared_item_bill"] == {"wooden-chest": 1}
    assert plan_material_bill(artifact["plan"]) == {"steel-chest": 1}


def test_missing_entity_mapping_is_rejected():
    with pytest.raises(
        ValueError, match="No item mapping for ghost entity prototype",
    ):
        derive_ghost_artifact(
            _single(
                {"action_type": "place_ghost", "entity": "steel-chest",
                 "position": {"x": 1.5, "y": 1.5}},
            ),
            item_mapping=_empty_mapping(),
        )


def test_missing_tile_mapping_is_rejected():
    with pytest.raises(
        ValueError, match="No item mapping for tile ghost prototype",
    ):
        derive_ghost_artifact(
            _single(
                {"action_type": "place_tile_ghost", "tile": "landfill",
                 "position": {"x": 1, "y": 1}},
            ),
            item_mapping=_empty_mapping(),
        )


@pytest.mark.parametrize(
    "item_mapping, match",
    [
        (None, "item_mapping must be a mapping"),
        ({"entities": {}, "tiles": {}}, "exactly 'provenance'"),
        ({"provenance": "p", "tiles": {}}, "exactly 'provenance'"),
        ({"provenance": "p", "entities": {}}, "exactly 'provenance'"),
        (_empty_mapping(provenance=""), "provenance must be a non-empty"),
        (_empty_mapping(provenance="   "), "provenance must be a non-empty"),
        (_empty_mapping(provenance=None), "provenance must be a non-empty"),
        (_empty_mapping(entities=[]), "entities must be a mapping"),
        (_empty_mapping(tiles=[]), "tiles must be a mapping"),
        (_empty_mapping(entities={"": "iron-plate"}), "non-empty strings"),
        (_empty_mapping(entities={"steel-chest": ""}), "non-empty item"),
        (
            _empty_mapping(entities={"steel-chest": ["steel-chest"]}),
            "non-empty item",
        ),
        (
            _empty_mapping(
                entities={"steel-chest": "steel-chest"},
                tiles={"steel-chest": "steel-chest"},
            ),
            "ambiguous mapping",
        ),
    ],
)
def test_invalid_mapping_data_is_rejected(item_mapping, match):
    with pytest.raises(ValueError, match=match):
        derive_ghost_artifact(_source_plan(), item_mapping=item_mapping)


def test_mapping_provenance_is_preserved_in_the_artifact():
    artifact = derive_ghost_artifact(
        _source_plan(),
        item_mapping=_mapping(
            provenance="factorio-data item prototype table, hand checked",
        ),
    )

    assert artifact["item_mapping"] == {
        "provenance": "factorio-data item prototype table, hand checked",
        "entities": dict(sorted(_SCIENCE_ENTITY_ITEMS.items())),
        "tiles": {},
    }


def test_removals_are_rejected_instead_of_dropped():
    with pytest.raises(ValueError, match="removal"):
        derive_ghost_artifact(
            _single(
                {"action_type": "remove_entity", "entity": "steel-chest",
                 "position": {"x": 1.5, "y": 1.5}},
            ),
            item_mapping=_empty_mapping(),
        )


@pytest.mark.parametrize("action_type", ["place_entity", "place_ghost"])
def test_scripted_sources_are_rejected(action_type):
    with pytest.raises(ValueError, match="scripted source"):
        derive_ghost_artifact(
            _single(
                {"action_type": action_type, "entity": "infinity-chest",
                 "position": {"x": 1.5, "y": 1.5},
                 "infinity_filter": "iron-plate"},
            ),
            item_mapping=_empty_mapping(),
        )


@pytest.mark.parametrize(
    "entity", ["substation", "electric-energy-interface", "roboport"],
)
def test_unsupported_direct_actions_are_rejected(entity):
    with pytest.raises(ValueError, match="Unsupported direct action entity"):
        derive_ghost_artifact(
            _single(
                {"action_type": "place_entity", "entity": entity,
                 "position": {"x": 1.5, "y": 1.5}},
            ),
            item_mapping=_empty_mapping(),
        )


def test_configuration_actions_are_rejected_not_guessed():
    with pytest.raises(ValueError, match="configure_entity"):
        derive_ghost_artifact(
            _single(
                {"action_type": "configure_entity", "entity": "steel-chest",
                 "position": {"x": 1.5, "y": 1.5}, "recipe": "iron-plate"},
            ),
            item_mapping=_empty_mapping(),
        )


def test_unknown_settings_are_rejected():
    with pytest.raises(ValueError, match="unknown setting"):
        derive_ghost_artifact(
            _single(
                {"action_type": "place_ghost", "entity": "steel-chest",
                 "position": {"x": 1.5, "y": 1.5}, "mystery": True},
            ),
            item_mapping=_empty_mapping(),
        )


@pytest.mark.parametrize(
    "setting",
    [
        {"recipe": "iron-plate"},
        {"inventory_limit": {
            "name": "iron-plate", "count": 10,
            "minimum_stacks_by_group": {
                "production": 1, "logistics": 1, "intermediate-products": 1,
            },
        }},
    ],
)
def test_direct_settings_that_a_ghost_cannot_represent_are_rejected(setting):
    with pytest.raises(ValueError, match="cannot be represented identically"):
        derive_ghost_artifact(
            _single(
                {"action_type": "place_entity", "entity": "steel-chest",
                 "position": {"x": 1.5, "y": 1.5}, **setting},
            ),
            item_mapping=_empty_mapping(),
        )


def test_source_collisions_are_rejected_before_conversion():
    source = _single(
        {"action_type": "place_ghost", "entity": "electric-furnace",
         "position": {"x": 1.5, "y": 3.5}},
    )
    source["phases"][0]["actions"].append(
        {"action_type": "place_ghost", "entity": "electric-furnace",
         "position": {"x": 1.5, "y": 3.5}},
    )

    with pytest.raises(ValueError, match="collision"):
        derive_ghost_artifact(source, item_mapping=_empty_mapping())


def test_unknown_plan_scope_is_rejected():
    source = _source_plan()
    source["force"] = "player"

    with pytest.raises(ValueError, match="unknown top-level field"):
        derive_ghost_artifact(source, item_mapping=_mapping())


def test_artifact_claims_no_execution_or_world_safety():
    artifact = derive_ghost_artifact(_source_plan(), item_mapping=_mapping())

    assert artifact["artifact_version"] == ARTIFACT_VERSION
    assert artifact["status"] == ARTIFACT_STATUS
    assert artifact["executable"] is False
    assert artifact["player_force_build_claim"] is False
    assert artifact["pending_checks"] == [
        "survey", "coverage", "power", "inventory_reservation", "runtime",
    ]
    assert artifact["claims"] == {
        "materials_available": False,
        "construction_bots_available": False,
        "sustained_rate_verified": False,
        "world_safety_verified": False,
    }
    assert set(artifact["unknowns"]) == {
        "survey", "coverage", "power", "inventory_reservation", "runtime",
        "world_safety",
    }
    assert all(artifact["unknowns"].values())


def test_output_is_deterministic_for_the_same_source():
    source = _source_plan()

    first = derive_ghost_artifact(source, item_mapping=_mapping())
    second = derive_ghost_artifact(source, item_mapping=_mapping())

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert source == _source_plan()
