# Path: planners/laya_quad_cell.py | Purpose: Realize one selected quad-mall slot using shared geometry, without a mall allocation policy.

from planners.quad_mall_layout import QUAD_MALL_MACHINE_OFFSETS, generate_quad_mall_layout
from planners.plan_validation import actions, entity_footprint_tiles


def generate_quad_slot(recipe, spec, center, side):
    if side not in QUAD_MALL_MACHINE_OFFSETS:
        raise ValueError("quad side must be north, west, east or south")
    group = "top" if side in {"north", "west"} else "bottom"
    plan = generate_quad_mall_layout(
        recipe, "assembling-machine-1", [p["name"] for p in spec["ingredients"]],
        [p["amount"] for p in spec["ingredients"]], center, group,
        craft_time=spec["energy_ticks"] / 60, product_amount=spec["product_amount"],
        inserter_type="inserter", support_inserter_type="long-handed-inserter", include_substation=False,
    )
    dx, dy = QUAD_MALL_MACHINE_OFFSETS[side]
    machine_position = {"x": center[0] + dx, "y": center[1] + dy}
    if side in {"west", "east"}:
        x = -1 if side == "west" else 1
        transfers = {(center[0] + x, center[1]),
                     (center[0] + x, center[1] + (-1 if side == "west" else 1))}
    else:
        transfers = {(center[0], center[1] + (-3 if side == "north" else 3)),
                     (center[0], center[1] + (-2 if side == "north" else 2))}
    selected = []
    for action in plan["phases"][0]["actions"]:
        if action["entity"] == "assembling-machine-1" and action["position"] != machine_position:
            continue
        if action["entity"] in {"inserter", "long-handed-inserter"}:
            p = action["position"]
            if (p["x"], p["y"]) not in transfers:
                continue
        selected.append({**action, "action_type": "place_ghost"})
    # A stable slot owns its section, so recipe changes replace that slot's
    # requests while retaining requests for every other machine at the chest.
    if not any(a["entity"] == "requester-chest" for a in selected):
        top = generate_quad_mall_layout(
            recipe, "assembling-machine-1", [p["name"] for p in spec["ingredients"]],
            [p["amount"] for p in spec["ingredients"]], center, "top",
            craft_time=spec["energy_ticks"] / 60, product_amount=spec["product_amount"], include_substation=True,
        )
        requester = next(a for a in top["phases"][0]["actions"] if a["entity"] == "requester-chest")
        selected.append({**requester, "action_type": "place_ghost"})
    for action in selected:
        if action["entity"] == "requester-chest":
            action["logistic_sections"][0]["group"] = f"laya-quad:{center[0]:g}:{center[1]:g}:{side}"
    return selected


def quad_centers_from_plan(plan):
    centers = set()
    for action in actions(plan):
        if action.get("entity") != "requester-chest":
            continue
        p = action["position"]
        prefix = f"laya-quad:{p['x']:g}:{p['y']:g}:"
        if any(s.get("group") in {prefix + side for side in QUAD_MALL_MACHINE_OFFSETS}
               for s in action.get("logistic_sections", [])):
            centers.add((p["x"], p["y"]))
    return [{"x": x, "y": y} for x, y in sorted(centers)]


def quad_reservation_reason(plan, centers):
    """Protect future vertical slots, allowing only their complete long-arm geometry."""
    placements = list(actions(plan))
    signature = lambda a: (a["entity"], a["position"]["x"], a["position"]["y"], a.get("direction", "north"))
    submitted = {signature(a) for a in placements}
    spec = {"ingredients": [{"name": "iron-plate", "amount": 2}], "energy_ticks": 30, "product_amount": 1}
    for center in centers:
        for side in ("north", "south"):
            reserved = [a for a in generate_quad_slot("iron-gear-wheel", spec, (center["x"], center["y"]), side)
                        if a["entity"] in {"assembling-machine-1", "long-handed-inserter"}]
            allowed = {signature(a) for a in reserved}
            tiles = frozenset().union(*(entity_footprint_tiles(a) for a in reserved))
            for action in placements:
                if entity_footprint_tiles(action) & tiles and signature(action) not in allowed:
                    return "reserved_quad_expansion"
            machine = next(a for a in reserved if a["entity"] == "assembling-machine-1")
            if signature(machine) in submitted and not allowed <= submitted:
                return "quad_vertical_requires_long_handed_inserters"
    return None
