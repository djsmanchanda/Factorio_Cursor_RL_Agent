# Path: planners/silo_component_link.py | Purpose: Compose one offline component-to-silo ghost belt link.
"""Construct a bounded candidate, not a delivery or execution authorization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections import defaultdict
import copy
from fractions import Fraction
import json

from core.block_flow import Block, GlobalBalance, Material, Port, Provenance, SharedCapacity, check_block_flow
from core.catalog_demand import CatalogDemandResult
from core.fixed_recipe_machines import compute_fixed_recipe_machines
from core.fluid_systems import validate_network_purity
from core.fluid_topology_intent import fluid_topology_intent
from core.science_line_candidate import build_science_line_candidate
from planners.fluid_item_block import _positive_rate, _recheck_demand, build_fluid_item_block
from planners.fluid_underground import validate_planned_underground_pairs
from planners.ghost_line_artifact import derive_ghost_artifact
from planners.item_routing import ItemEndpoint, ItemRoute, route_declared_items
from planners.plan_validation import actions, occupied_tile_indices, validate_build_plan
from planners.rocket_silo_artifact import build_rocket_silo_artifact
from planners.recipe_data import LINE_RECIPES


def _tile(position: Mapping[str, object]) -> tuple[int, int]:
    values = (position.get("x"), position.get("y"))
    if any(type(value) not in (int, float) or value % 1 != 0.5 for value in values):
        raise ValueError("port must lie at a tile center")
    return tuple(int(value - 0.5) for value in values)


def _bounded_waypoints(waypoints: Sequence[Sequence[int]], start: tuple[int, int],
                       finish: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    if isinstance(waypoints, (str, bytes)) or not isinstance(waypoints, Sequence) or len(waypoints) > 32:
        raise ValueError("route requires at most 32 waypoints")
    points = []
    for point in waypoints:
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or any(type(value) is not int or abs(value) > 1_000_000 for value in point)):
            raise ValueError("route waypoints must be bounded integer tiles")
        points.append(tuple(point))
    anchors = [start, *points, finish]
    length = 1
    for previous, current in zip(anchors, anchors[1:]):
        dx, dy = abs(current[0] - previous[0]), abs(current[1] - previous[1])
        if (dx and dy) or not (dx or dy):
            raise ValueError("route segments must be nonempty and axis-aligned")
        length += dx + dy
    if length > 256:
        raise ValueError("route exceeds 256 tiles")
    return tuple(points)


def _action_at(plan: dict, position: Mapping[str, object]) -> dict:
    matches = [action for action in actions(plan) if action.get("position") == position]
    if len(matches) != 1:
        raise ValueError("port must name exactly one planned entity")
    return matches[0]


def _check_local_ports(component: dict, silo: dict, component_port: dict, silo_port: dict,
                       belt_type: str, ingredients: list[dict]) -> None:
    source = _action_at(component["plan"], component_port["belt_position"])
    target = _action_at(silo["plan"], silo_port["belt_position"])
    for belt in (source, target):
        if (belt.get("action_type") != "place_ghost" or belt.get("entity") != belt_type
                or belt.get("direction") != "east"):
            raise ValueError("component and silo ports must be matching eastbound ghost belts")
    if component_port["item"] != silo_port["item"]:
        raise ValueError("component output does not match silo ingredient")
    recipe_part = next((part for part in ingredients if part["name"] == silo_port["item"]), None)
    if recipe_part is None or recipe_part["amount"] != silo_port["amount_per_part"]:
        raise ValueError("silo port does not match the pinned recipe")
    position = silo_port["belt_position"]
    inserter = _action_at(silo["plan"], {"x": position["x"], "y": position["y"] + 1})
    if (inserter.get("action_type") != "place_ghost"
            or inserter.get("entity") not in {"inserter", "fast-inserter"}
            or inserter.get("direction") != "north"):
        raise ValueError("silo port lacks its expected inserter ghost")
    silo_action = next(action for action in actions(silo["plan"]) if action.get("entity") == "rocket-silo")
    if not (abs(position["x"] - silo_action["position"]["x"]) <= 4
            and position["y"] + 2 == silo_action["position"]["y"] - 4):
        raise ValueError("silo inserter drop is not on the silo footprint")


def _reject_incoming_foreign_belts(route_plan: dict, existing_plans: list[dict], source_tile: tuple[int, int],
                                   target_tile: tuple[int, int] | None = None) -> None:
    route_tiles = {_tile(action["position"]) for action in actions(route_plan)}
    vectors = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}
    foreign_belts = set()
    for plan in existing_plans:
        for action in actions(plan):
            if not action.get("entity", "").endswith("transport-belt"):
                continue
            tile = _tile(action["position"])
            foreign_belts.add(tile)
            if tile == source_tile:
                continue
            direction = vectors.get(action.get("direction"))
            if direction and (tile[0] + direction[0], tile[1] + direction[1]) in route_tiles:
                raise ValueError("another planned belt feeds into the connection")
    for action in actions(route_plan):
        tile = _tile(action["position"])
        direction = vectors[action["direction"]]
        destination = (tile[0] + direction[0], tile[1] + direction[1])
        if destination in foreign_belts and destination != target_tile:
            raise ValueError("connection feeds another planned belt")


def _solid_component(demand: CatalogDemandResult, options: Mapping[str, object]) -> dict:
    if set(options) != {"catalog_bytes", "recipe", "machine_by_recipe", "line_spec", "item_mapping"}:
        raise ValueError("solid component requires catalog, recipe, machines, line spec and item mapping")
    catalog = _recheck_demand(demand, options["catalog_bytes"])
    recipe, line_spec = options["recipe"], options["line_spec"]
    if (not isinstance(recipe, str) or recipe not in LINE_RECIPES or not isinstance(line_spec, Mapping)
            or LINE_RECIPES[recipe].get("fluid_ingredients")
            or line_spec.get("feed_style") != "chained" or line_spec.get("mining_feed") is not False
            or line_spec.get("terminal_collector") is not False
            or sorted(line_spec.get("chained_ingredients", [])) != list(range(len(LINE_RECIPES[recipe]["ingredients"])))):
        raise ValueError("solid component requires a fully chained item-only line without a collector")
    origin_x, origin_y = line_spec.get("origin_x"), line_spec.get("origin_y")
    if any(type(value) is not int or abs(value) > 1_000_000 for value in (origin_x, origin_y)):
        raise ValueError("solid component origin must be bounded integer tiles")
    candidate = build_science_line_candidate(
        demand, catalog, recipe=recipe, line_spec=line_spec, machine_by_recipe=options["machine_by_recipe"],
        catalog_bytes=options["catalog_bytes"],
    )
    if candidate.count_status != "adequate":
        raise ValueError("solid component has fewer machines than its demand requires")
    artifact = derive_ghost_artifact(candidate.geometry["plan"], item_mapping=options["item_mapping"])
    output = {"x": origin_x + line_spec["machine_count"] * 3 - 0.5, "y": origin_y + 6.5}
    output_belt = _action_at(artifact["plan"], output)
    if (output_belt.get("entity") != line_spec["belt_type"] or output_belt.get("direction") != "east"
            or output_belt.get("action_type") != "place_ghost"):
        raise ValueError("solid component output belt does not match its line geometry")
    port = {"origin": [origin_x, origin_y], "item": recipe, "belt_position": output, "direction": "east",
            "nominal_items_per_second": str(candidate.machine_fact.crafting_speed
                                            / candidate.machine_fact.base_craft_seconds
                                            * line_spec["machine_count"]
                                            * LINE_RECIPES[recipe]["product_amount"]), "lane": "unverified"}
    artifact["pending_checks"].append("item_lane_supply")
    artifact["unknowns"]["item_lane_supply"] = (
        "Inputs, belt lane identity, inserter throughput, spare supply and output delivery are unverified."
    )
    artifact["planning_context"] = {"version": "planner-context-v1", "trust": "unverified", "data": {
        "kind": "solid-item-row-v1", "catalog_sha256": demand.provenance.catalog_sha256,
        "recipe": recipe, "machine": candidate.machine, "required_machines": candidate.required_machine_count,
        "planned_machines": line_spec["machine_count"], "item_output_ports": [port],
    }}
    return {"artifact": artifact, "item_output_ports": [port]}


def build_silo_component_link(
    demand: CatalogDemandResult, *, component_options: Mapping[str, object],
    silo_origin: Sequence[int], placement_capture: Mapping[str, object],
    row_index: int, waypoints: Sequence[Sequence[int]], component_kind: str = "fluid",
) -> dict:
    """Join one freshly derived fluid or solid item row to its silo ingredient stub.

    Only a single surface-belt route is supported. Other rows and silo inputs
    remain unconnected; no splitter, capacity reservation, surveyed terrain,
    live recipe identity, construction or delivery is established.
    """
    if not isinstance(demand, CatalogDemandResult) or not isinstance(component_options, Mapping):
        raise ValueError("catalog demand and explicit component options are required")
    if not isinstance(placement_capture, Mapping):
        raise ValueError("silo placement capture is required")
    if (placement_capture.get("force") != demand.provenance.force
            or placement_capture.get("surface") != demand.provenance.surface
            or placement_capture.get("active_mods") != {mod.name: mod.version for mod in demand.provenance.mods}):
        raise ValueError("component and silo capture scope or mod assertions differ")
    if component_kind == "fluid":
        component = build_fluid_item_block(demand, **component_options)
        belt_type = component_options["belt_type"]
    elif component_kind == "solid":
        component = _solid_component(demand, component_options)
        belt_type = component_options["line_spec"]["belt_type"]
    else:
        raise ValueError("unsupported component kind")
    if type(row_index) is not int or not 0 <= row_index < len(component["item_output_ports"]):
        raise ValueError("row_index must select an existing component output")
    catalog_bytes = component_options["catalog_bytes"]
    silo = build_rocket_silo_artifact(
        origin=silo_origin, placement_capture=placement_capture, recipe_bytes=catalog_bytes,
        recipe_sha256=demand.provenance.catalog_sha256, belt_type=belt_type,
    )
    component_artifact = component["artifact"]
    component_port = component["item_output_ports"][row_index]
    silo_ports = silo["planning_context"]["data"]["input_ports"]
    matches = [port for port in silo_ports if port["item"] == component_port["item"]]
    if len(matches) != 1:
        raise ValueError("component product must match exactly one silo ingredient")
    silo_port = matches[0]
    recipe = next(entry for entry in json.loads(catalog_bytes)["recipes"] if entry["name"] == "rocket-part")
    _check_local_ports(component_artifact, silo, component_port, silo_port, belt_type, recipe["ingredients"])
    source_tile, target_tile = _tile(component_port["belt_position"]), _tile(silo_port["belt_position"])
    start, finish = (source_tile[0] + 1, source_tile[1]), (target_tile[0] - 1, target_tile[1])
    route_waypoints = _bounded_waypoints(waypoints, start, finish)
    endpoints = [ItemEndpoint("source", component_port["item"], "producer", start, "east"),
                 ItemEndpoint("silo", component_port["item"], "consumer", finish, "east")]
    named = [("component", component_artifact["plan"]), ("silo", silo["plan"])]
    route = ItemRoute("component_to_silo", component_port["item"], "source", "silo", route_waypoints)
    route_plan = route_declared_items(endpoints, [route], belt_type=belt_type,
                                     occupied_tiles=occupied_tile_indices(named))[0][1]
    route_actions = list(actions(route_plan))
    if len(route_actions) < 2 or route_actions[0]["direction"] != "east" or route_actions[-1]["direction"] != "east":
        raise ValueError("route must start and finish with an eastbound belt")
    _reject_incoming_foreign_belts(route_plan, [plan for _, plan in named], source_tile, target_tile)
    combined = {"phases": [phase for _, plan in named for phase in plan["phases"]] + route_plan["phases"]}
    validate_build_plan(combined)
    component_mapping = component_artifact["item_mapping"]
    silo_mapping = silo["item_mapping"]
    entities = dict(component_mapping["entities"])
    for name, item in silo_mapping["entities"].items():
        if name in entities and entities[name] != item:
            raise ValueError("component and silo placement item mappings differ")
        entities[name] = item
    artifact = derive_ghost_artifact(combined, item_mapping={
        "provenance": component_mapping["provenance"] + "; " + silo_mapping["provenance"],
        "entities": entities, "tiles": {},
    })
    for existing in (component_artifact, silo):
        for check in existing["pending_checks"]:
            if check not in artifact["pending_checks"]:
                artifact["pending_checks"].append(check)
        artifact["unknowns"].update(existing["unknowns"])
    artifact["unknowns"]["silo_input_delivery"] = (
        "One planned surface link is connected; lane behavior, supply, other inputs, inserter reach, "
        "power and delivery remain unverified."
    )
    artifact["planning_context"] = {"version": "planner-context-v1", "trust": "unverified", "data": {
        "kind": "silo-component-link-v1", "component": copy.deepcopy(component_artifact["planning_context"]["data"]),
        "silo": copy.deepcopy(silo["planning_context"]["data"]), "row_index": row_index,
        "item": component_port["item"], "source_tile": list(source_tile), "target_tile": list(target_tile),
        "route_waypoints": [list(point) for point in route_waypoints],
    }}
    if component_kind == "fluid":
        intent = component_artifact["planning_context"]["data"]["fluid_topology_intent"]
        artifact["planning_context"]["data"]["fluid_topology_intent"] = fluid_topology_intent(
            combined, intent["segments"], intent["permitted_attachments"])
    return artifact


def _bundle_capacity_accounting(components: Sequence[Mapping[str, object]],
                                capacities: Sequence[SharedCapacity]) -> dict:
    if isinstance(capacities, (str, bytes)) or not isinstance(capacities, Sequence):
        raise ValueError("capacities must be a sequence of shared capacity facts")
    by_material = {}
    for capacity in capacities:
        if not isinstance(capacity, SharedCapacity) or capacity.material in by_material:
            raise ValueError("at most one explicit shared source per material is supported")
        by_material[capacity.material] = capacity
    blocks, totals = [], defaultdict(Fraction)
    provenance = Provenance("offline-component-template", "full nominal planned machine input")
    for index, request in enumerate(components):
        options, demand = request["component_options"], request["demand"]
        recipe = options["recipe"]
        catalog = json.loads(options["catalog_bytes"])
        facts = compute_fixed_recipe_machines(catalog, balance=demand.balance,
                                               machine_by_recipe=options["machine_by_recipe"])
        fact = next(entry for entry in facts.facts if entry.recipe == recipe)
        count = (sum(row["machines"] for row in options["rows"]) if request["component_kind"] == "fluid"
                 else options["line_spec"]["machine_count"])
        signature = next(entry for entry in demand.balance.recipe_signatures if entry.recipe == recipe)
        crafts = fact.crafting_speed * count / fact.base_craft_seconds
        ports = []
        for ingredient in signature.ingredients:
            material = Material(ingredient.name, ingredient.type)
            rate = crafts * ingredient.amount
            capacity = by_material.get(material)
            port_id = f"component-{index}:{ingredient.type}:{ingredient.name}"
            ports.append(Port(port_id, f"component-{index}", material, "input", rate, None,
                              capacity.source_id if capacity else None))
            totals[material] -= rate
        blocks.append(Block(f"component-{index}", tuple(ports), provenance))
    if any(material not in totals for material in by_material):
        raise ValueError("shared capacity names an input unused by this bundle")
    result = check_block_flow(blocks, global_balance=GlobalBalance.from_mapping(totals, provenance=provenance),
                              capacities=capacities)
    return {
        "status": result.status.value,
        "basis": "full nominal planned machine input rates; no measured delivery or physical source identity",
        "inputs": [{"material": flow.material.name, "kind": flow.material.kind,
                    "nominal_rate_per_second": str(flow.input)} for flow in result.materials],
        "unaccounted_ports": [port.port_id for port in result.unaccounted_inputs],
        "sources": [{"source_id": source.source_id, "material": source.material.name,
                     "kind": source.material.kind, "measured_rate": None if source.measured_rate is None else str(source.measured_rate),
                     "existing_allocations": (None if source.existing_allocations is None else [
                         {"id": allocation.allocation_id, "rate": str(allocation.rate),
                          "provenance": {"source": allocation.provenance.source, "detail": allocation.provenance.detail}}
                         for allocation in source.existing_allocations]),
                     "proposed_demand": str(source.proposed_demand),
                     "shortage": None if source.shortage is None else str(source.shortage),
                     "minimum_shortage": str(source.minimum_shortage), "status": source.status.value,
                     "provenance": {"source": source.provenance.source, "detail": source.provenance.detail}}
                    for source in result.capacities],
    }


def _bundle_feed_accounting(contexts: Sequence[Mapping[str, object]], target: object | None) -> dict:
    target_rate = None if target is None else _positive_rate(target, "rocket-part target")
    feeds = []
    nominal_parts = []
    for context in contexts:
        ports = context["component"]["item_output_ports"]
        selected = context["row_index"]
        selected_rate = Fraction(ports[selected]["nominal_items_per_second"])
        other_rate = sum((Fraction(port["nominal_items_per_second"]) for index, port in enumerate(ports)
                          if index != selected), Fraction(0))
        silo_port = next(port for port in context["silo"]["input_ports"] if port["item"] == context["item"])
        per_part = silo_port["amount_per_part"]
        required = None if target_rate is None else target_rate * per_part
        shortfall = None if required is None else max(Fraction(0), required - selected_rate)
        nominal_parts.append(selected_rate / per_part)
        feeds.append({"item": context["item"], "amount_per_part": per_part,
                      "connected_row": selected, "connected_nominal_per_second": str(selected_rate),
                      "unconnected_row_count": len(ports) - 1, "unconnected_nominal_per_second": str(other_rate),
                      "target_input_per_second": None if required is None else str(required),
                      "nominal_shortfall_per_second": None if shortfall is None else str(shortfall)})
    return {
        "target_rocket_parts_per_second": None if target_rate is None else str(target_rate),
        "status": ("target_unknown" if target_rate is None else
                   "below_target" if any(Fraction(feed["nominal_shortfall_per_second"]) > 0 for feed in feeds)
                   else "nominal_meets_target"),
        "connected_nominal_part_rate": str(min(nominal_parts)),
        "basis": "template machine rates only; not measured throughput, a physical upper bound or delivery guarantee",
        "feeds": feeds,
    }


def build_silo_component_bundle(*, components: Sequence[Mapping[str, object]],
                                silo_origin: Sequence[int], placement_capture: Mapping[str, object],
                                capacities: Sequence[SharedCapacity] = (),
                                target_parts_per_second: object | None = None) -> dict:
    """Compose one independently planned row per silo ingredient around one silo.

    All requests use the same pinned catalog and scope. This builds ghost
    geometry only; separate demand balances do not prove shared supply,
    lane capacity, complete row delivery, commissioning or launch.
    """
    if isinstance(components, (str, bytes)) or not isinstance(components, Sequence) or not 1 <= len(components) <= 3:
        raise ValueError("components must contain one to three explicit requests")
    links = []
    provenance = None
    fluid_segments = []
    fluid_permits = []
    for request in components:
        if not isinstance(request, Mapping) or set(request) != {"demand", "component_options", "component_kind", "row_index", "waypoints"}:
            raise ValueError("component request fields must be explicit")
        demand = request["demand"]
        if not isinstance(demand, CatalogDemandResult):
            raise ValueError("component request needs catalog demand")
        identity = demand.provenance
        if provenance is None:
            provenance = identity
        elif identity != provenance:
            raise ValueError("component requests must share catalog provenance")
        options = request["component_options"]
        if request["component_kind"] == "fluid":
            if not isinstance(options, Mapping) or options.get("fluid_inlets") is not None:
                raise ValueError("bundle fluid inlet links are not supported")
        link = build_silo_component_link(
            demand, component_options=options, component_kind=request["component_kind"],
            silo_origin=silo_origin, placement_capture=placement_capture,
            row_index=request["row_index"], waypoints=request["waypoints"],
        )
        links.append(link)
        if request["component_kind"] == "fluid":
            intent = link["planning_context"]["data"]["fluid_topology_intent"]
            fluid_segments.extend(intent["segments"])
            fluid_permits.extend(intent["permitted_attachments"])
    contexts = [link["planning_context"]["data"] for link in links]
    names = [context["item"] for context in contexts]
    expected = {port["item"] for port in contexts[0]["silo"]["input_ports"]}
    if len(set(names)) != len(names) or set(names) != expected:
        raise ValueError("bundle requires exactly one component per silo ingredient")
    if any(context["silo"] != contexts[0]["silo"] for context in contexts):
        raise ValueError("bundle silo plans do not match")
    silo_phases = [phase for phase in links[0]["plan"]["phases"] if phase["name"] == "rocket_silo"]
    if len(silo_phases) != 1:
        raise ValueError("bundle requires exactly one silo phase")
    plans = []
    routes = []
    for index, link in enumerate(links):
        phases = [copy.deepcopy(phase) for phase in link["plan"]["phases"] if phase["name"] != "rocket_silo"]
        route_phases = [phase for phase in phases if phase["name"] == "item_route_component_to_silo"]
        if len(route_phases) != 1:
            raise ValueError("each component needs exactly one route")
        route_phases[0]["name"] = f"item_route_silo_component_{index}"
        routes.append({"phases": route_phases})
        plans.append({"phases": [phase for phase in phases if phase is not route_phases[0]]})
    combined = {"phases": copy.deepcopy(silo_phases) + [phase for plan in plans for phase in plan["phases"]]
                + [phase for route in routes for phase in route["phases"]]}
    validate_build_plan(combined)
    validate_network_purity(fluid_segments)
    validate_planned_underground_pairs(combined, fluid_segments)
    silo_plan = {"phases": silo_phases}
    for index, route in enumerate(routes):
        _reject_incoming_foreign_belts(route, [silo_plan, *plans, *(other for number, other in enumerate(routes)
                                                                     if number != index)],
                                      tuple(contexts[index]["source_tile"]), tuple(contexts[index]["target_tile"]))
    entities = {}
    for link in links:
        for name, item in link["item_mapping"]["entities"].items():
            if name in entities and entities[name] != item:
                raise ValueError("component placement item mappings differ")
            entities[name] = item
    artifact = derive_ghost_artifact(combined, item_mapping={
        "provenance": "; ".join(link["item_mapping"]["provenance"] for link in links),
        "entities": entities, "tiles": {},
    })
    for link in links:
        for check in link["pending_checks"]:
            if check not in artifact["pending_checks"]:
                artifact["pending_checks"].append(check)
        for key, description in link["unknowns"].items():
            if key not in artifact["unknowns"]:
                artifact["unknowns"][key] = description
    artifact["unknowns"]["silo_input_delivery"] = (
        "One row per ingredient has a planned link; other rows, inputs, shared capacity, lane behavior, "
        "inserter reach, power and actual delivery remain unverified."
    )
    flow = _bundle_capacity_accounting(components, capacities)
    artifact["pending_checks"].append("shared_input_capacity")
    artifact["unknowns"]["shared_input_capacity"] = (
        "Nominal full-speed requirements are checked against caller-declared source facts; source identity, "
        "prior allocations, physical connections and actual throughput remain unverified."
    )
    feed = _bundle_feed_accounting(contexts, target_parts_per_second)
    artifact["pending_checks"].append("silo_feed_throughput")
    artifact["unknowns"]["silo_feed_throughput"] = (
        "Template rates do not establish actual production, belt lane capacity, inserter delivery, or silo throughput."
    )
    artifact["planning_context"] = {"version": "planner-context-v1", "trust": "unverified", "data": {
        "kind": "silo-component-bundle-v1", "links": copy.deepcopy(contexts), "input_capacity": flow,
        "feed_rates": feed,
    }}
    if fluid_segments:
        artifact["planning_context"]["data"]["fluid_topology_intent"] = fluid_topology_intent(
            combined, fluid_segments, fluid_permits)
    return artifact
