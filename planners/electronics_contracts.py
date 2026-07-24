# Path: planners/electronics_contracts.py
# Purpose: Declare and validate versioned endpoint and throughput contracts for electronics.

from __future__ import annotations

from planners.fluid_layouts import header_attachment
from planners.recipe_data import BELT_TIERS, FEED_HEADROOM, LINE_RECIPES, MACHINE_SPEEDS

CONTRACT_VERSION = "1.0.0"
ADVANCED_CIRCUIT_RATE = 0.6
PROCESSING_UNIT_RATE = 0.1
BELT_CAPACITY_PER_SECOND = float(BELT_TIERS["fast-transport-belt"])
FLUID_STAGE_COUNTS = {
    "refinery": 2, "plastic": 2, "sulfur": 2,
    "sulfuric_acid": 2, "processing_unit": 2,
}
SOLID_STAGE_COUNTS = {
    "iron_plate": 6,
    "copper_plate_ec": 12,
    "cable_ec": 4,
    "electronic_circuit": 2,
    "copper_plate_ac": 6,
    "cable_ac": 2,
    "iron_plate_acid": 4,
    "copper_plate_pu": 18,
    "iron_plate_pu": 9,
    "cable_pu": 6,
    "electronic_circuit_pu": 3,
    "advanced_circuit": 6,
}

_ITEM_RATES_2A = {
    "copper_to_cable_ec": 1.8,
    "cable_to_ec": 3.6,
    "iron_to_ec": 1.2,
    "copper_to_cable_ac": 1.2,
    "cable_to_ac": 2.4,
    "ec_to_ac": 1.2,
    "coal_to_plastic": 0.6,
    "plastic_to_ac": 1.2,
    "reserve_ac_output": ADVANCED_CIRCUIT_RATE,
}
_ITEM_RATES_2B = {
    **_ITEM_RATES_2A,
    "iron_to_acid": 0.01,
    "sulfur_to_acid": 0.05,
    "copper_to_cable_pu": 3.0,
    "cable_to_ec_pu": 6.0,
    "iron_to_ec_pu": 2.0,
    "ec_to_pu": 2.0,
    "ac_to_pu": 0.2,
}
_ITEM_PRODUCERS = {
    "copper_to_cable_ec": "copper_plate_ec",
    "cable_to_ec": "cable_ec",
    "iron_to_ec": "iron_plate",
    "copper_to_cable_ac": "copper_plate_ac",
    "cable_to_ac": "cable_ac",
    "ec_to_ac": "electronic_circuit",
    "coal_to_plastic": "coal_source",
    "plastic_to_ac": "plastic",
    "reserve_ac_output": "advanced_circuit",
    "iron_to_acid": "iron_plate_acid",
    "sulfur_to_acid": "sulfur",
    "copper_to_cable_pu": "copper_plate_pu",
    "cable_to_ec_pu": "cable_pu",
    "iron_to_ec_pu": "iron_plate_pu",
    "ec_to_pu": "electronic_circuit_pu",
    "ac_to_pu": "advanced_circuit",
}


def _recipe_capacity(recipe: str, machines: int) -> float:
    spec = LINE_RECIPES[recipe]
    return (
        machines * MACHINE_SPEEDS[spec["machine"]]
        * spec["product_amount"] / spec["craft_time"]
    )


def _stage_capacities(world) -> dict[str, float]:
    return {
        "iron_plate": world.ore_capacity("iron_plate"),
        "copper_plate_ec": world.ore_capacity("copper_plate_ec"),
        "cable_ec": _recipe_capacity("copper-cable", SOLID_STAGE_COUNTS["cable_ec"]),
        "electronic_circuit": _recipe_capacity(
            "electronic-circuit", SOLID_STAGE_COUNTS["electronic_circuit"],
        ),
        "copper_plate_ac": world.ore_capacity("copper_plate_ac"),
        "cable_ac": _recipe_capacity("copper-cable", SOLID_STAGE_COUNTS["cable_ac"]),
        "coal_source": float(world.coal_capacity_per_second),
        "plastic": 4.0,
        "advanced_circuit": _recipe_capacity(
            "advanced-circuit", SOLID_STAGE_COUNTS["advanced_circuit"],
        ),
        "iron_plate_acid": world.ore_capacity("iron_plate_acid"),
        "sulfur": 4.0,
        "copper_plate_pu": world.ore_capacity("copper_plate_pu"),
        "cable_pu": _recipe_capacity("copper-cable", SOLID_STAGE_COUNTS["cable_pu"]),
        "iron_plate_pu": world.ore_capacity("iron_plate_pu"),
        "electronic_circuit_pu": _recipe_capacity(
            "electronic-circuit", SOLID_STAGE_COUNTS["electronic_circuit_pu"],
        ),
        "processing_unit": _recipe_capacity("processing-unit", 2),
    }


def _endpoint_payload(endpoint) -> dict:
    return {
        "name": endpoint.name,
        "position": {"x": endpoint.position[0], "y": endpoint.position[1]},
        "direction": endpoint.direction,
    }


def _item_interfaces(endpoints, routes, include_processing: bool, capacities: dict) -> list[dict]:
    by_name = {endpoint.name: endpoint for endpoint in endpoints}
    rates = _ITEM_RATES_2B if include_processing else _ITEM_RATES_2A
    result = []
    for route in routes:
        rate = rates[route.name]
        capacity = min(BELT_CAPACITY_PER_SECOND, capacities[_ITEM_PRODUCERS[route.name]])
        required = rate * FEED_HEADROOM
        if capacity + 1e-9 < required:
            raise ValueError(
                f"Item route {route.name} capacity {capacity:.6g}/s is below "
                f"headroom requirement {required:.6g}/s"
            )
        result.append({
            "name": route.name,
            "kind": "item",
            "resource": route.item,
            "producer": _endpoint_payload(by_name[route.producer]),
            "consumer": _endpoint_payload(by_name[route.consumer]),
            "required_rate_per_second": rate,
            "required_with_headroom_per_second": required,
            "capacity_per_second": capacity,
        })
    return result


def _fluid_interfaces(include_processing: bool, world) -> list[dict]:
    petroleum_rate = ADVANCED_CIRCUIT_RATE * 20
    if include_processing:
        petroleum_rate += 0.75
    crude_rate = petroleum_rate / 0.45
    specs = [
        ("crude_to_refinery", "crude-oil", crude_rate, world.crude_capacity_per_second,
         tuple(world.pumpjack_sites[0]["output"]),
         header_attachment("basic-oil-processing", "crude-oil", 2, 40, -30)["attach"]),
        ("petroleum_to_plastic", "petroleum-gas", ADVANCED_CIRCUIT_RATE * 20, 18.0,
         header_attachment("basic-oil-processing", "petroleum-gas", 2, 40, -30)["attach"],
         header_attachment("plastic-bar", "petroleum-gas", 2, 90, 20)["attach"]),
    ]
    if include_processing:
        specs.extend([
            ("petroleum_to_sulfur", "petroleum-gas", 0.75, 18.0,
             specs[1][4], header_attachment("sulfur", "petroleum-gas", 2, 90, 108)["attach"]),
            ("water_to_sulfur", "water", 0.75, world.water_capacity_per_second,
             tuple(world.offshore_pump_sites[0]["output"]),
             header_attachment("sulfur", "water", 2, 90, 108)["attach"]),
            ("water_to_acid", "water", 1.0, world.water_capacity_per_second,
             tuple(world.offshore_pump_sites[0]["output"]),
             header_attachment("sulfuric-acid", "water", 2, 150, 171)["attach"]),
            ("acid_to_processing", "sulfuric-acid", 0.5, 100.0,
             header_attachment("sulfuric-acid", "sulfuric-acid", 2, 150, 171)["attach"],
             header_attachment("processing-unit", "sulfuric-acid", 2, 220, 220)["attach"]),
        ])
    petroleum_rate = sum(rate for _, fluid, rate, _, _, _ in specs if fluid == "petroleum-gas")
    if petroleum_rate * FEED_HEADROOM > 18.0:
        raise ValueError("Refinery row lacks petroleum-gas headroom")
    water_rate = sum(rate for _, fluid, rate, _, _, _ in specs if fluid == "water")
    if water_rate * FEED_HEADROOM > world.water_capacity_per_second:
        raise ValueError("Surveyed water source lacks headroom")
    result = []
    for name, fluid, rate, capacity, producer, consumer in specs:
        required = rate * FEED_HEADROOM
        if capacity + 1e-9 < required:
            raise ValueError(f"Fluid route {name} lacks declared source headroom")
        result.append({
            "name": name,
            "kind": "fluid",
            "resource": fluid,
            "producer": {"position": {"x": producer[0], "y": producer[1]}},
            "consumer": {"position": {"x": consumer[0], "y": consumer[1]}},
            "required_rate_per_second": rate,
            "required_with_headroom_per_second": required,
            "capacity_per_second": capacity,
        })
    return result


def build_electronics_contract(endpoints, routes, include_processing: bool, world) -> dict:
    capacities = _stage_capacities(world)
    targets = {"advanced-circuit": ADVANCED_CIRCUIT_RATE}
    if include_processing:
        targets["processing-unit"] = PROCESSING_UNIT_RATE
    if capacities["advanced_circuit"] < ADVANCED_CIRCUIT_RATE * FEED_HEADROOM:
        raise ValueError("Advanced-circuit row is undersized for the declared target")
    if include_processing and capacities["processing_unit"] < PROCESSING_UNIT_RATE * FEED_HEADROOM:
        raise ValueError("Processing-unit row is undersized for the declared target")
    return {
        "version": CONTRACT_VERSION,
        "rate_unit": "items_or_fluid_per_second",
        "headroom_factor": FEED_HEADROOM,
        "targets": targets,
        "stage_capacities_per_second": capacities,
        "interfaces": [
            *_item_interfaces(endpoints, routes, include_processing, capacities),
            *_fluid_interfaces(include_processing, world),
        ],
    }