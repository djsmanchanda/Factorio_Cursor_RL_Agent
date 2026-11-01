# Path: planners/fluid_item_block.py
# Purpose: Build offline, capacity-checked fluid-consuming item rows from exact catalog demand.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from math import isfinite

from core.catalog_demand import CatalogDemandResult, plan_catalog_demand
from core.fixed_recipe_balance import RecipeSignature
from core.fixed_recipe_machines import compute_fixed_recipe_machines
from core.fluid_systems import validate_network_purity
from core.fluid_topology_intent import fluid_topology_intent
from planners.fluid_layouts import (
    FLUID_RECIPES, MACHINE_FOOTPRINTS, VERIFIED_PIPE_TILES, fluid_network_segments,
    generate_fluid_machine_row, header_attachment,
)
from planners.fluid_underground import validate_planned_underground_pairs
from planners.fluid_routing import plan_shortest_fluid_chain_link
from planners.ghost_line_artifact import derive_ghost_artifact
from planners.infrastructure import strip_local_power
from planners.plan_validation import assert_no_production_infinity, occupied_tile_indices, validate_build_plan
from planners.recipe_data import BELT_TIERS


def _positive_rate(value: object, label: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, Fraction)):
        raise ValueError(f"{label} must be a positive finite numeric rate")
    if isinstance(value, float) and not isfinite(value):
        raise ValueError(f"{label} must be a positive finite numeric rate")
    try:
        rate = Fraction(str(value)) if isinstance(value, (float, Decimal)) else Fraction(value)
    except (ValueError, OverflowError, ZeroDivisionError) as error:
        raise ValueError(f"{label} must be a positive finite numeric rate") from error
    if rate <= 0:
        raise ValueError(f"{label} must be a positive finite numeric rate")
    return rate


def _recheck_demand(result: CatalogDemandResult, catalog_bytes: bytes) -> dict:
    if not isinstance(result, CatalogDemandResult) or not isinstance(catalog_bytes, bytes):
        raise ValueError("a catalog demand result and its exact bytes are required")
    if hashlib.sha256(catalog_bytes).hexdigest() != result.provenance.catalog_sha256:
        raise ValueError("catalog bytes do not match demand provenance")
    catalog = json.loads(catalog_bytes)
    boundaries = [{"name": entry.name, **({"rate": entry.rate} if entry.rate is not None
                                           else {"cap": entry.cap})} for entry in result.boundaries]
    preferences = {entry.material: entry.recipe for entry in result.selections}
    checked = plan_catalog_demand(catalog_bytes, provenance=asdict(result.provenance),
                                  demand=dict(result.demand), boundaries=boundaries,
                                  preferences=preferences)
    if checked.balance != result.balance:
        raise ValueError("demand balance does not match independently recomputed catalog balance")
    if checked.balance.status != "unique" or checked.balance.feasible is not True or checked.balance.shortages:
        raise ValueError("demand balance must be unique, feasible and shortage-free")
    return catalog


def build_fluid_item_block(
    demand: CatalogDemandResult, *, catalog_bytes: bytes, recipe: str,
    machine_by_recipe: Mapping[str, str], rows: Sequence[Mapping[str, object]],
    belt_type: str, inserter_type: str, capacities: Mapping[str, object],
    item_mapping: Mapping[str, object], max_machines: int,
    fluid_inlets: Mapping[str, Sequence[int]] | None = None,
    permitted_fluid_attachments: Sequence[Mapping[str, object]] = (),
) -> dict:
    """Build north-facing rows, optionally routing fluid headers to declared inlets.

    Capacity inputs are caller assertions about one belt lane and one machine's
    input/output inserters, in items per game second. Rows are checked at their
    full nominal rounded machine capacity, not merely the requested average.
    Output ports identify the eastmost planned belt and nominal production,
    not measured or unallocated supply; lane identity remains unverified.
    These checks cannot establish measured delivery or a safe execution scope.
    """
    catalog = _recheck_demand(demand, catalog_bytes)
    if recipe not in FLUID_RECIPES or recipe not in VERIFIED_PIPE_TILES:
        raise ValueError("recipe has no verified fluid row geometry")
    spec = FLUID_RECIPES[recipe]
    items = spec["item_ingredients"]
    products = spec["item_products"]
    if not items or len(items) > 2 or len(products) != 1 or spec["fluid_products"]:
        raise ValueError("fluid row must have one item product and one or two item ingredients")
    if not isinstance(machine_by_recipe, Mapping):
        raise ValueError("machine_by_recipe must map every selected recipe")
    machine_facts = compute_fixed_recipe_machines(
        catalog, balance=demand.balance, machine_by_recipe=machine_by_recipe,
    )
    fact = next((item for item in machine_facts.facts if item.recipe == recipe), None)
    if fact is None or fact.machine != spec["machine"]:
        raise ValueError("selected recipe and machine do not match the fluid row geometry")
    expected = RecipeSignature.from_parts(
        recipe,
        [(name, "item", Fraction(str(amount))) for name, amount in zip(items, spec["item_amounts"])]
        + [(name, "fluid", Fraction(str(amount))) for name, amount in spec["fluid_ingredients"].items()],
        [(name, "item", Fraction(str(spec["item_product_amounts"][name]))) for name in products],
    )
    signature = next((entry for entry in demand.balance.recipe_signatures if entry.recipe == recipe), None)
    if signature != expected or fact.base_craft_seconds != Fraction(str(spec["craft_time"])):
        raise ValueError("catalog recipe differs from the fluid row geometry")
    if (not isinstance(capacities, Mapping) or set(capacities) != {
            "lane_items_per_second", "input_inserter_items_per_second", "output_inserter_items_per_second"}):
        raise ValueError("all three explicit lane and inserter capacities are required")
    lane = _positive_rate(capacities["lane_items_per_second"], "lane capacity")
    input_capacity = _positive_rate(capacities["input_inserter_items_per_second"], "input inserter capacity")
    output_capacity = _positive_rate(capacities["output_inserter_items_per_second"], "output inserter capacity")
    if belt_type not in BELT_TIERS or lane > Fraction(str(BELT_TIERS[belt_type])) / 2:
        raise ValueError("lane capacity exceeds the repository belt-tier model")
    if type(max_machines) is not int or not 1 <= max_machines <= 64:
        raise ValueError("max_machines must be an integer in 1..64")
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not 1 <= len(rows) <= 16:
        raise ValueError("rows must be a nonempty sequence with at most 16 rows")
    machine_crafts = fact.crafting_speed / fact.base_craft_seconds
    inputs = {name: Fraction(str(amount)) * machine_crafts
              for name, amount in zip(items, spec["item_amounts"])}
    output = Fraction(str(spec["item_product_amounts"][products[0]])) * machine_crafts
    if sum(inputs.values()) > input_capacity or output > output_capacity:
        raise ValueError("one machine exceeds the declared inserter capacity")

    plans, segments, ports, item_outputs, row_facts = [], [], [], [], []
    total = 0
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"origin", "machines"}:
            raise ValueError("each row requires origin and machines")
        origin, count = row["origin"], row["machines"]
        if (not isinstance(origin, (list, tuple)) or len(origin) != 2
                or any(type(value) is not int or abs(value) > 1_000_000 for value in origin)
                or type(count) is not int or count <= 0):
            raise ValueError("row origins and machine counts must be bounded integers")
        total += count
        if total > max_machines:
            raise ValueError("rows exceed max_machines")
        if any(rate * count > lane for rate in inputs.values()) or output * count > lane:
            raise ValueError("row exceeds the declared item lane capacity")
        plan = generate_fluid_machine_row(
            recipe, count, origin[0], origin[1], belt_type, inserter_type,
            chained_items=set(items), terminal_collector=False,
        )
        plan = strip_local_power(plan)
        plans.append(plan)
        output_y = origin[1] + MACHINE_FOOTPRINTS[fact.machine] + 3.5
        output_belts = [action for phase in plan["phases"] for action in phase["actions"]
                        if action.get("entity") == belt_type
                        and action.get("position", {}).get("y") == output_y]
        output_x = sorted(action["position"]["x"] for action in output_belts)
        input_x = [action["position"]["x"] for phase in plan["phases"] for action in phase["actions"]
                   if action.get("entity") == belt_type
                   and action.get("position", {}).get("y") == origin[1] + 0.5]
        if (not output_x or output_x[0] != origin[0] + 0.5
                or not input_x or output_x[-1] != max(input_x)
                or any(action.get("action_type") != "place_ghost" or action.get("direction") != "east"
                       for action in output_belts)
                or output_x != [output_x[0] + index for index in range(len(output_x))]):
            raise ValueError("fluid row output belt is not a contiguous eastbound ghost line")
        item_outputs.append({"origin": list(origin), "item": products[0],
                             "belt_position": {"x": output_x[-1], "y": output_y},
                             "direction": "east", "nominal_items_per_second": str(output * count),
                             "lane": "unverified"})
        segments.extend(fluid_network_segments(recipe, count, origin[0], origin[1]))
        ports.extend({"origin": list(origin), "fluid": fluid,
                      "attachment": header_attachment(recipe, fluid, count, origin[0], origin[1])}
                     for fluid in spec["fluid_ingredients"])
        row_facts.append({"origin": list(origin), "machines": count,
                          "input_lanes_per_second": {name: str(rate * count) for name, rate in inputs.items()},
                          "output_lane_per_second": str(output * count)})
    if total < fact.machine_count:
        raise ValueError("rows contain fewer machines than the exact demand requires")
    if fluid_inlets is not None:
        if not isinstance(fluid_inlets, Mapping) or set(fluid_inlets) != set(spec["fluid_ingredients"]):
            raise ValueError("fluid inlets must name every required fluid exactly once")
        for fluid in sorted(fluid_inlets):
            inlet = fluid_inlets[fluid]
            if (not isinstance(inlet, (tuple, list)) or len(inlet) != 2
                    or any(type(value) is not int or abs(value) > 1_000_000 for value in inlet)):
                raise ValueError("fluid inlet coordinates must be bounded integers")
            endpoints = [tuple(port["attachment"]["attach"]) for port in ports if port["fluid"] == fluid]
            points = [tuple(inlet), *endpoints]
            if any(max(point[axis] for point in points) - min(point[axis] for point in points) > 128
                   for axis in (0, 1)):
                raise ValueError("fluid inlet routing span exceeds 128 tiles")
            occupied = occupied_tile_indices([(str(index), plan) for index, plan in enumerate(plans)])
            if any(point in occupied for point in points):
                raise ValueError("fluid inlet or header attachment overlaps a planned entity")
            link, routed = plan_shortest_fluid_chain_link(
                tuple(inlet), endpoints, fluid, foreign=segments, hard_tiles=occupied,
                clearance=0, mixing_margin=True,
            )
            plans.append(link)
            segments.extend(routed)
    combined = {"phases": [phase for plan in plans for phase in plan["phases"]]}
    validate_build_plan(combined)
    validate_network_purity(segments)
    validate_planned_underground_pairs(combined, segments)
    assert_no_production_infinity([("fluid-item-block", combined)])
    artifact = derive_ghost_artifact(combined, item_mapping=item_mapping)
    artifact["pending_checks"].extend(["item_lane_supply", "inserter_throughput", "fluid_supply", "machine_fluid_connection"])
    artifact["unknowns"].update({
        "item_lane_supply": ("Item belts are unconnected; downstream clearance, lane identity, spare supply, "
                             "and actual capacity are unverified."),
        "inserter_throughput": "Inserter capacities are caller assertions, not measured performance.",
        "machine_fluid_connection": "Recipe-specific fluidbox assignment and actual machine connection are not observed.",
        "fluid_supply": ("Fluid links end at caller-declared inlets; source identity and capacity are unverified."
                         if fluid_inlets is not None else
                         "Fluid header attachments are unconnected and external source capacity is unverified."),
    })
    artifact["planning_context"] = {
        "version": "planner-context-v1", "trust": "unverified", "data": {
            "kind": "fluid-item-rows-v1", "catalog_sha256": demand.provenance.catalog_sha256,
            "recipe": recipe, "machine": fact.machine,
            "required_crafts_per_second": str(fact.required_crafts_per_second),
            "required_machines": fact.machine_count, "planned_machines": total,
            "lane_items_per_second": str(lane), "input_inserter_items_per_second": str(input_capacity),
            "output_inserter_items_per_second": str(output_capacity), "rows": row_facts,
            "fluid_ports": ports, "item_output_ports": item_outputs,
            "fluid_topology_intent": fluid_topology_intent(combined, segments, permitted_fluid_attachments),
            "fluid_inlets": ({name: list(point) for name, point in fluid_inlets.items()}
                             if fluid_inlets is not None else None),
        },
    }
    return {"artifact": artifact, "recipe": recipe, "machine_fact": fact, "rows": row_facts,
            "fluid_ports": ports, "item_output_ports": item_outputs, "non_claims": (
                "offline candidate; no terrain, power, logistics, source or runtime verification",
                "capacity declarations and item mapping are caller assertions, not delivery or dispatch authority",
            )}
