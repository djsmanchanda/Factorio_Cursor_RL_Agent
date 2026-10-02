# Path: planners/laya_quad_cell.py | Purpose: Realize one selected quad-mall slot using shared geometry, without a mall allocation policy.

from planners.quad_mall_layout import QUAD_MALL_MACHINE_OFFSETS, generate_quad_mall_layout


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
