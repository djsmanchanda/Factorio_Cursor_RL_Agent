# Path: planners/electronics_block.py
# Purpose: Compose the immutable raw-resource electronics block through processing units.

from __future__ import annotations

from core.fluid_systems import validate_network_purity
from planners.electronics_contracts import (
    FLUID_STAGE_COUNTS, SOLID_STAGE_COUNTS, build_electronics_contract,
)
from planners.fluid_layouts import (
    fluid_network_segments,
    generate_fluid_chain_link,
    generate_fluid_machine_row,
    header_attachment,
)
from planners.fluid_routing import fluid_chain_link_segments
from planners.item_routing import ItemEndpoint, ItemRoute, route_declared_items
from planners.local_layout_planner import LocalLayoutPlanner
from planners.plan_validation import (
    actions,
    assert_no_production_infinity,
    occupied_tile_indices,
    validate_no_collisions,
)
from planners.resource_layouts import (
    generate_coal_mine,
    generate_offshore_pump_source,
    generate_pumpjack_source,
    resource_fluid_segment,
)
from planners.electronics_world import ElectronicsWorldSpec
from planners.infrastructure import roboport_positions, strip_local_power
from planners.roboport_coverage import plan_coverage_roboports, roboport_ghost_targets
from planners.sandbox_infrastructure import compose_managed_sandbox

BELT = "express-transport-belt"
INSERTER = "stack-inserter"


DEPENDENCIES_2A = {
    ("iron-ore", "iron-plate"), ("copper-ore", "copper-plate"),
    ("copper-plate", "copper-cable"),
    ("copper-cable", "electronic-circuit"), ("iron-plate", "electronic-circuit"),
    ("crude-oil", "petroleum-gas"), ("coal", "plastic-bar"),
    ("petroleum-gas", "plastic-bar"), ("plastic-bar", "advanced-circuit"),
    ("copper-cable", "advanced-circuit"),
    ("electronic-circuit", "advanced-circuit"),
}
DEPENDENCIES_2B = DEPENDENCIES_2A | {
    ("water", "sulfur"), ("petroleum-gas", "sulfur"),
    ("sulfur", "sulfuric-acid"), ("iron-plate", "sulfuric-acid"),
    ("water", "sulfuric-acid"), ("sulfuric-acid", "processing-unit"),
    ("advanced-circuit", "processing-unit"),
    ("electronic-circuit", "processing-unit"),
}


def _line(planner, recipe, count, origin, *, mining=False, terminal=False):
    chained = set(range(len(planner_recipe(recipe)["ingredients"]))) if not mining else None
    return planner.generate_line_layout(
        recipe, count, *origin, mining_feed=mining, belt_type=BELT, inserter_type=INSERTER,
        feed_style="chained" if chained else "chest", chained_ingredients=chained,
        terminal_collector=terminal,
    )


def planner_recipe(recipe: str) -> dict:
    from planners.recipe_data import LINE_RECIPES
    return LINE_RECIPES[recipe]


def _surveyed_line(world, stage, recipe, count):
    resource = planner_recipe(recipe)["ingredients"][0]
    surveyed = world.ore_line(stage, resource, count)
    origin = world.ore_origin(stage, resource, count)
    plan = _line(LocalLayoutPlanner(), recipe, count, origin, mining=True)
    emitted = tuple(
        (action["position"]["x"], action["position"]["y"])
        for action in actions(plan) if action.get("entity") == "electric-mining-drill"
    )
    if emitted != surveyed["drill_positions"]:
        raise ValueError(f"Stage {stage} emitted drills differ from surveyed drill positions")
    return plan


def _solid_stages(include_processing: bool, world: ElectronicsWorldSpec) -> list[tuple[str, dict]]:
    planner = LocalLayoutPlanner()
    stages = [
        ("iron_plate", _surveyed_line(world, "iron_plate", "iron-plate", SOLID_STAGE_COUNTS["iron_plate"])),
        ("copper_plate_ec", _surveyed_line(world, "copper_plate_ec", "copper-plate", SOLID_STAGE_COUNTS["copper_plate_ec"])),
        ("cable_ec", _line(planner, "copper-cable", SOLID_STAGE_COUNTS["cable_ec"], (30, -150))),
        ("electronic_circuit", _line(planner, "electronic-circuit", SOLID_STAGE_COUNTS["electronic_circuit"], (70, -120))),
        ("copper_plate_ac", _surveyed_line(world, "copper_plate_ac", "copper-plate", SOLID_STAGE_COUNTS["copper_plate_ac"])),
        ("cable_ac", _line(planner, "copper-cable", SOLID_STAGE_COUNTS["cable_ac"], (30, -70))),
    ]
    if include_processing:
        stages.extend([
            ("iron_plate_acid", _surveyed_line(world, "iron_plate_acid", "iron-plate", SOLID_STAGE_COUNTS["iron_plate_acid"])),
            ("copper_plate_pu", _surveyed_line(world, "copper_plate_pu", "copper-plate", SOLID_STAGE_COUNTS["copper_plate_pu"])),
            ("iron_plate_pu", _surveyed_line(world, "iron_plate_pu", "iron-plate", SOLID_STAGE_COUNTS["iron_plate_pu"])),
            ("cable_pu", _line(planner, "copper-cable", SOLID_STAGE_COUNTS["cable_pu"], (80, 160))),
            ("electronic_circuit_pu", _line(planner, "electronic-circuit", SOLID_STAGE_COUNTS["electronic_circuit_pu"], (130, 190))),
        ])
    return stages

def _fluid_stages(include_processing: bool, world: ElectronicsWorldSpec) -> list[tuple[str, dict]]:
    stages = [
        ("crude_source", generate_pumpjack_source(
            list(world.pumpjack_sites), list(world.crude_pipe_tiles),
        )),
        ("refinery", generate_fluid_machine_row(
            "basic-oil-processing", FLUID_STAGE_COUNTS["refinery"], 40, -30, BELT, INSERTER,
            terminal_collector=False,
        )),
        ("coal_source", generate_coal_mine(
            list(world.coal_drill_positions), world.coal_output_y, world.coal_output_x, BELT,
        )),
        ("plastic", generate_fluid_machine_row(
            "plastic-bar", FLUID_STAGE_COUNTS["plastic"], 90, 20, BELT, INSERTER,
            chained_items={"coal"}, terminal_collector=False,
        )),
    ]
    if include_processing:
        stages.extend([
            ("water_source", generate_offshore_pump_source(
                list(world.offshore_pump_sites), list(world.water_pipe_tiles),
            )),
            ("sulfur", generate_fluid_machine_row(
                "sulfur", FLUID_STAGE_COUNTS["sulfur"], 90, 108, BELT, INSERTER, terminal_collector=False,
            )),
            ("sulfuric_acid", generate_fluid_machine_row(
                "sulfuric-acid", FLUID_STAGE_COUNTS["sulfuric_acid"], 150, 171, BELT, INSERTER,
                chained_items={"iron-plate", "sulfur"}, terminal_collector=False,
            )),
            ("processing_unit", generate_fluid_machine_row(
                "processing-unit", FLUID_STAGE_COUNTS["processing_unit"], 220, 220, BELT, INSERTER,
                chained_items={"electronic-circuit", "advanced-circuit"},
                terminal_collector=True,
            )),
        ])
    return stages


def _advanced_circuit_stage(include_processing: bool) -> tuple[str, dict]:
    planner = LocalLayoutPlanner()
    return ("advanced_circuit", _line(
        planner, "advanced-circuit", SOLID_STAGE_COUNTS["advanced_circuit"], (150, 70), terminal=False,
    ))


def _mining_output(world: ElectronicsWorldSpec, stage: str, resource: str, count: int):
    origin_x, origin_y = world.ore_origin(stage, resource, count)
    return origin_x + count * 3, origin_y + 6


def _item_routes(
    stages: list[tuple[str, dict]], include_processing: bool, world: ElectronicsWorldSpec,
) -> list[tuple[str, dict]]:
    iron_out = _mining_output(world, "iron_plate", "iron-ore", SOLID_STAGE_COUNTS["iron_plate"])
    copper_ec_out = _mining_output(world, "copper_plate_ec", "copper-ore", SOLID_STAGE_COUNTS["copper_plate_ec"])
    copper_ac_out = _mining_output(world, "copper_plate_ac", "copper-ore", SOLID_STAGE_COUNTS["copper_plate_ac"])
    copper_ec_corridor_x = copper_ec_out[0] + 9
    endpoints = [
        ItemEndpoint("iron_out", "iron-plate", "producer", iron_out, "east"),
        ItemEndpoint("copper_ec_out", "copper-plate", "producer", copper_ec_out, "east"),
        ItemEndpoint("cable_ec_in", "copper-plate", "consumer", (26, -150), "east"),
        ItemEndpoint("cable_ec_out", "copper-cable", "producer", (42, -144), "east"),
        ItemEndpoint("ec_cable_in", "copper-cable", "consumer", (69, -121), "south"),
        ItemEndpoint("ec_iron_in", "iron-plate", "consumer", (68, -119), "north"),
        ItemEndpoint("copper_ac_out", "copper-plate", "producer", copper_ac_out, "east"),
        ItemEndpoint("cable_ac_in", "copper-plate", "consumer", (26, -70), "east"),
        ItemEndpoint("cable_ac_out", "copper-cable", "producer", (36, -64), "east"),
        ItemEndpoint("ac_cable_in", "copper-cable", "consumer", (146, 77), "east"),
        ItemEndpoint("ec_out", "electronic-circuit", "producer", (76, -114), "east"),
        ItemEndpoint("ac_ec_in", "electronic-circuit", "consumer", (149, 69), "south"),
        ItemEndpoint("coal_out", "coal", "producer", (40, 2), "east"),
        ItemEndpoint("plastic_coal_in", "coal", "consumer", (86, 20), "east"),
        ItemEndpoint("plastic_out", "plastic-bar", "producer", (96, 26), "east"),
        ItemEndpoint("ac_plastic_in", "plastic-bar", "consumer", (148, 71), "north"),
        ItemEndpoint("ac_out", "advanced-circuit", "producer", (168, 76), "east"),
        ItemEndpoint("ac_reserved_in", "advanced-circuit", "consumer", (210, 76), "south"),
    ]
    routes = [
        ItemRoute("copper_to_cable_ec", "copper-plate", "copper_ec_out", "cable_ec_in",
                  ((copper_ec_corridor_x, copper_ec_out[1]),
                   (copper_ec_corridor_x, -165), (20, -165), (20, -150)),
                  tunnel_crossings=((20, -161), (20, -160))),
        ItemRoute("cable_to_ec", "copper-cable", "cable_ec_out", "ec_cable_in",
                  ((60, -144), (60, -121))),
        ItemRoute("iron_to_ec", "iron-plate", "iron_out", "ec_iron_in",
                  ((90, -214), (90, -110), (68, -110))),
        ItemRoute("copper_to_cable_ac", "copper-plate", "copper_ac_out", "cable_ac_in",
                  ((24, -94), (24, -70)), tunnel_crossings=((24, -87), (24, -86))),
        ItemRoute("cable_to_ac", "copper-cable", "cable_ac_out", "ac_cable_in",
                  ((85, -64), (85, 77)), tunnel_crossings=((85, 17),)),
        ItemRoute("ec_to_ac", "electronic-circuit", "ec_out", "ac_ec_in",
                  ((149, -114),), tunnel_crossings=((90, -114), (149, -105), (149, -104), (149, -77), (149, -76), (149, -49), (149, -48), (149, -21), (149, -20), (149, 7), (149, 8), (149, 18), (149, 19), (149, 20), (149, 21), (149, 35), (149, 36), (149, 63), (149, 64))),
        ItemRoute("coal_to_plastic", "coal", "coal_out", "plastic_coal_in",
                  ((86, 2),), tunnel_crossings=((85, 2), (86, 17))),
        ItemRoute("plastic_to_ac", "plastic-bar", "plastic_out", "ac_plastic_in",
                  ((134, 26), (134, 80), (148, 80)), tunnel_crossings=((134, 77), (148, 77), (148, 76))),
        ItemRoute("reserve_ac_output", "advanced-circuit", "ac_out", "ac_reserved_in"),
    ]
    if include_processing:
        iron_acid_out = _mining_output(world, "iron_plate_acid", "iron-ore", SOLID_STAGE_COUNTS["iron_plate_acid"])
        copper_pu_out = _mining_output(world, "copper_plate_pu", "copper-ore", SOLID_STAGE_COUNTS["copper_plate_pu"])
        iron_pu_out = _mining_output(world, "iron_plate_pu", "iron-ore", SOLID_STAGE_COUNTS["iron_plate_pu"])
        endpoints.extend([
            ItemEndpoint("iron_acid_out", "iron-plate", "producer", iron_acid_out, "east"),
            ItemEndpoint("acid_iron_in", "iron-plate", "consumer", (148, 172), "north"),
            ItemEndpoint("sulfur_out", "sulfur", "producer", (98, 114), "east"),
            ItemEndpoint("acid_sulfur_in", "sulfur", "consumer", (149, 170), "south"),
            ItemEndpoint("copper_pu_out", "copper-plate", "producer", copper_pu_out, "east"),
            ItemEndpoint("cable_pu_in", "copper-plate", "consumer", (76, 160), "east"),
            ItemEndpoint("cable_pu_out", "copper-cable", "producer", (98, 166), "east"),
            ItemEndpoint("ec_pu_cable_in", "copper-cable", "consumer", (129, 189), "south"),
            ItemEndpoint("iron_pu_out", "iron-plate", "producer", iron_pu_out, "east"),
            ItemEndpoint("ec_pu_iron_in", "iron-plate", "consumer", (128, 191), "north"),
            ItemEndpoint("ec_pu_out", "electronic-circuit", "producer", (139, 196), "east"),
            ItemEndpoint("pu_ec_in", "electronic-circuit", "consumer", (218, 219), "south"),
            ItemEndpoint("ac_reserved_out", "advanced-circuit", "producer", (210, 77), "south"),
            ItemEndpoint("pu_ac_in", "advanced-circuit", "consumer", (218, 221), "north"),
        ])
        routes.extend([
            ItemRoute("iron_to_acid", "iron-plate", "iron_acid_out", "acid_iron_in",
                      ((115, 101), (115, 135), (130, 135), (130, 172)),
                      tunnel_crossings=((130, 168),)),
            ItemRoute("sulfur_to_acid", "sulfur", "sulfur_out", "acid_sulfur_in",
                      ((139, 114), (139, 170)), tunnel_crossings=((115, 114), (136, 114), (137, 114), (139, 168), (139, 169), (141, 170), (142, 170))),
            ItemRoute("copper_to_cable_pu", "copper-plate", "copper_pu_out", "cable_pu_in",
                      ((75, 121), (75, 160)),
                      tunnel_crossings=((75, 128), (75, 129), (75, 130), (75, 131), (75, 148), (75, 149), (75, 150), (75, 151))),
            ItemRoute("cable_to_ec_pu", "copper-cable", "cable_pu_out", "ec_pu_cable_in",
                      ((120, 166), (120, 189)), tunnel_crossings=((120, 168), (120, 180))),
            ItemRoute("iron_to_ec_pu", "iron-plate", "iron_pu_out", "ec_pu_iron_in",
                      ((125, 146), (125, 191)), tunnel_crossings=((75, 146), (81, 146), (82, 146), (125, 168), (125, 180), (125, 189))),
            ItemRoute("ec_to_pu", "electronic-circuit", "ec_pu_out", "pu_ec_in",
                      ((200, 196), (200, 219)), tunnel_crossings=((163, 196), (164, 196), (165, 196), (166, 196), (200, 217), (206, 219), (207, 219))),
            ItemRoute("ac_to_pu", "advanced-circuit", "ac_reserved_out", "pu_ac_in",
                      ((210, 221),), tunnel_crossings=((210, 193), (210, 194), (210, 195), (210, 196), (210, 208), (210, 209), (210, 210), (210, 211), (210, 217), (210, 218), (210, 219))),
        ])
    stripped = [
        (name, plan if name.startswith("unified_") else strip_local_power(plan))
        for name, plan in stages
    ]
    plans = route_declared_items(
        endpoints, routes, occupied_tiles=occupied_tile_indices(stripped), belt_type=BELT,
    )
    return plans, endpoints, routes


def _fluid_routes(
    stages: list[tuple[str, dict]], include_processing: bool,
    world: ElectronicsWorldSpec, managed_tiles: set[tuple[int, int]],
):
    stage_segments = []

    for name, recipe, count, origin in [
        ("refinery", "basic-oil-processing", FLUID_STAGE_COUNTS["refinery"], (40, -30)),
        ("plastic", "plastic-bar", FLUID_STAGE_COUNTS["plastic"], (90, 20)),
    ]:
        stage_segments += fluid_network_segments(recipe, count, *origin)
    crude_resource = resource_fluid_segment("crude-oil", list(world.crude_pipe_tiles))
    stage_segments.append(crude_resource)
    crude_output = tuple(world.pumpjack_sites[0]["output"])
    crude_from = (crude_output[0] - 1, crude_output[1])
    crude_to = header_attachment("basic-oil-processing", "crude-oil", 2, 40, -30)["attach"]
    crude_plan = generate_fluid_chain_link(crude_from, [crude_to], "crude-oil", 15, stage_segments, obstacle_tiles=managed_tiles)
    crude_segments = fluid_chain_link_segments(crude_from, [crude_to], "crude-oil", 15, stage_segments, obstacle_tiles=managed_tiles)

    petroleum_to = [header_attachment("plastic-bar", "petroleum-gas", 2, 90, 20)["attach"]]
    if include_processing:
        stage_segments += fluid_network_segments("sulfur", FLUID_STAGE_COUNTS["sulfur"], 90, 108)
        stage_segments += fluid_network_segments("sulfuric-acid", FLUID_STAGE_COUNTS["sulfuric_acid"], 150, 171)
        stage_segments += fluid_network_segments("processing-unit", FLUID_STAGE_COUNTS["processing_unit"], 220, 220)
        petroleum_to.append(header_attachment("sulfur", "petroleum-gas", 2, 90, 108)["attach"])
    petroleum_from = header_attachment(
        "basic-oil-processing", "petroleum-gas", 2, 40, -30
    )["attach"]
    petroleum_foreign = stage_segments + crude_segments
    petroleum_plan = generate_fluid_chain_link(
        petroleum_from, petroleum_to, "petroleum-gas", 0, petroleum_foreign, obstacle_tiles=managed_tiles
    )
    petroleum_segments = fluid_chain_link_segments(
        petroleum_from, petroleum_to, "petroleum-gas", 0, petroleum_foreign, obstacle_tiles=managed_tiles
    )
    plans = [("link_crude_oil", crude_plan), ("link_petroleum_gas", petroleum_plan)]
    all_segments = stage_segments + crude_segments + petroleum_segments

    if include_processing:
        all_segments.append(resource_fluid_segment("water", list(world.water_pipe_tiles)))
        water_output = tuple(world.offshore_pump_sites[0]["output"])
        water_from = (water_output[0] - 1, water_output[1])
        water_to = [
            header_attachment("sulfur", "water", 2, 90, 108)["attach"],
            header_attachment("sulfuric-acid", "water", 2, 150, 171)["attach"],
        ]
        water_plan = generate_fluid_chain_link(
            water_from, water_to, "water", -60, all_segments, obstacle_tiles=managed_tiles
        )
        water_segments = fluid_chain_link_segments(
            water_from, water_to, "water", -60, all_segments, obstacle_tiles=managed_tiles
        )
        acid_from = header_attachment(
            "sulfuric-acid", "sulfuric-acid", FLUID_STAGE_COUNTS["sulfuric_acid"], 150, 171
        )["attach"]
        acid_to = [header_attachment(
            "processing-unit", "sulfuric-acid", FLUID_STAGE_COUNTS["sulfuric_acid"], 220, 220
        )["attach"]]
        acid_foreign = all_segments + water_segments
        acid_plan = generate_fluid_chain_link(
            acid_from, acid_to, "sulfuric-acid", 0, acid_foreign, obstacle_tiles=managed_tiles
        )
        acid_segments = fluid_chain_link_segments(
            acid_from, acid_to, "sulfuric-acid", 0, acid_foreign, obstacle_tiles=managed_tiles
        )
        plans += [("link_water", water_plan), ("link_sulfuric_acid", acid_plan)]
        all_segments += water_segments + acid_segments
    validate_network_purity(all_segments)
    return plans, all_segments


def _validate_surveyed_drills(
    stages: list[tuple[str, dict]], include_processing: bool, world: ElectronicsWorldSpec,
) -> None:
    ore_stages = ["iron_plate", "copper_plate_ec", "copper_plate_ac"]
    if include_processing:
        ore_stages += ["iron_plate_acid", "copper_plate_pu", "iron_plate_pu"]
    expected = set(world.coal_drill_positions)
    for stage in ore_stages:
        expected.update(world.ore_line(
            stage,
            "copper-ore" if "copper" in stage else "iron-ore",
            SOLID_STAGE_COUNTS[stage],
        )["drill_positions"])
    emitted = {
        (action["position"]["x"], action["position"]["y"])
        for _, plan in stages
        for action in actions(plan)
        if action.get("entity") == "electric-mining-drill"
    }
    if emitted != expected:
        missing = sorted(expected - emitted)
        unexpected = sorted(emitted - expected)
        raise ValueError(
            "Every mining drill must come from the surveyed WorldSpec; "
            f"missing={missing[:3]}, unexpected={unexpected[:3]}"
        )

def _block_anchors(include_processing: bool) -> list[dict]:
    anchors = [
        {"name": "raw_low", "x": 40, "y": -175, "extent": ((-10, -225), (90, -110))},
        {"name": "raw_mid", "x": 40, "y": -80, "extent": ((-10, -110), (90, -50))},
        {"name": "oil", "x": 55, "y": -15, "extent": ((10, -50), (100, 35))},
        {"name": "plastic", "x": 110, "y": 20, "extent": ((80, 0), (150, 35))},
        {"name": "advanced", "x": 185, "y": 90, "extent": ((130, 60), (215, 80))},
    ]
    if include_processing:
        anchors += [
            {"name": "chem_resource", "x": -20, "y": 110, "extent": ((-50, 75), (10, 145))},
            {"name": "chem_west", "x": 40, "y": 110, "extent": ((15, 75), (110, 145))},
            {"name": "chem_bridge", "x": 75, "y": 130},
            {"name": "chem_mid", "x": 120, "y": 150, "extent": ((85, 95), (175, 180))},
            {"name": "pu_feed", "x": 165, "y": 195, "extent": ((120, 155), (200, 225))},
            {"name": "pu", "x": 210, "y": 210, "extent": ((180, 190), (235, 235))},
        ]
    return anchors


def _cover_emitted_geometry(preview, infrastructure_stages, anchors, stripped):
    """Re-compose the backbone so it covers the routes, not just the anchors.

    The anchor-tree roboports have to exist before item and fluid routes can be
    solved (the routes dodge their tiles), but those routes then wander outside
    the tree's construction radii and their ghosts would never be built. So the
    tree is planned once, the routes are solved against it, and the backbone is
    then re-composed with the extra roboports the FINISHED geometry demands --
    each chained to the network and kept off every emitted tile, which is what
    makes the second pass collision-free rather than another guess.
    """
    existing = roboport_positions(dict(preview["infrastructure"])["unified_roboports"])
    extra = plan_coverage_roboports(
        existing, roboport_ghost_targets(stripped), occupied_tile_indices(stripped),
    )
    # Always recompose against the FINAL routes, even with no extra roboports:
    # the preview spine was planned blind to item/fluid routes (they don't exist
    # yet), so a relay pole can still land on a route tile the preview never
    # knew about. _resolve_spine_pole_overlaps (inside compose_managed_sandbox)
    # is what actually dodges obstacle_plans -- this is the only pass that ever
    # runs it against the true, finished route geometry.
    return compose_managed_sandbox(
        infrastructure_stages, anchors, {}, bots_per_roboport=50,
        extra_roboports=extra, obstacle_plans=stripped,
    )


def build_electronics_block(*, include_processing: bool, world: ElectronicsWorldSpec) -> dict:
    stages = _solid_stages(include_processing, world) + _fluid_stages(include_processing, world)
    stages.append(_advanced_circuit_stage(include_processing))
    _validate_surveyed_drills(stages, include_processing, world)
    infrastructure_stages = _solid_stages(True, world) + _fluid_stages(True, world)
    infrastructure_stages.append(_advanced_circuit_stage(True))
    anchors = _block_anchors(True)
    preview = compose_managed_sandbox(
        infrastructure_stages, anchors, {}, bots_per_roboport=50,
    )
    managed_tiles = occupied_tile_indices(preview["infrastructure"])
    fluid_routes, fluid_segments = _fluid_routes(
        stages, include_processing, world, managed_tiles,
    )
    item_routes, item_endpoints, item_route_specs = _item_routes(
        stages + fluid_routes, include_processing, world,
    )
    throughput_contract = build_electronics_contract(
        item_endpoints, item_route_specs, include_processing, world,
    )
    production = stages + fluid_routes + item_routes
    assert_no_production_infinity(production)
    if any(action["action_type"] == "remove_entity" for _, plan in production for action in actions(plan)):
        raise ValueError("Immutable electronics block cannot contain removal actions")

    stripped = [(name, strip_local_power(plan)) for name, plan in production]
    composed = _cover_emitted_geometry(
        preview, infrastructure_stages, anchors, stripped,
    )
    composition = {
        "infrastructure": composed["infrastructure"],
        "plans": stripped,
        "scaffolding": composed["scaffolding"],
    }
    complete = composition["infrastructure"] + composition["plans"]
    validate_no_collisions(complete)
    return {
        **composition,
        "production": production,
        "dependencies": sorted(DEPENDENCIES_2B if include_processing else DEPENDENCIES_2A),
        "fluid_segments": fluid_segments,
        "block_footprint": {"x1": -250, "y1": -250, "x2": 250, "y2": 250},
        "interfaces_reserved": True,
        "throughput_contract": throughput_contract,
        "world_spec": {
            "version": world.version, "surface": world.surface,
            "survey_tick": world.survey_tick, "map_bounds": world.map_bounds,
        },
    }
