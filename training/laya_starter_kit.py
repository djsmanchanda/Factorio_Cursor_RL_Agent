# Path: training/laya_starter_kit.py | Purpose: Describe the user-specified bootstrap budget without granting items or choosing a build order.

STARTER_ITEMS = {
    "electric-mining-drill": 5, "electric-furnace": 30,
    "medium-electric-pole": 50, "substation": 20, "inserter": 20,
    "assembling-machine-1": 6, "requester-chest": 6,
    "passive-provider-chest": 16, "roboport": 25,
}
FOUNDATION_FURNACES = {"iron-plate": 12, "copper-plate": 12, "stone-brick": 6}


def verify_starter_kit(facts):
    """Check accessible stock exactly; disconnected player inventory cannot top it up."""
    stock = {}
    for net in facts["networks"].values():
        for name, count in net["items"].items():
            stock[name] = stock.get(name, 0) + count
    differences = {name: {"expected": count, "observed": stock.get(name, 0)}
                   for name, count in STARTER_ITEMS.items() if stock.get(name, 0) != count}
    extras = {name: count for name, count in stock.items() if name not in STARTER_ITEMS and count}
    installed = facts.get("infrastructure_counts", {})
    expected = {"electric-energy-interface": 1, "substation": 4, "roboport": 1, "storage-chest": 1}
    infrastructure = {name: {"expected": count, "observed": installed.get(name, 0)}
                      for name, count in expected.items() if installed.get(name, 0) != count}
    bots = {kind: sum(n[kind] for n in facts["networks"].values())
            for kind in ("construction_robots", "logistic_robots")}
    return {"version": "mod-playground-starter-v1", "matches": not differences and not extras and not infrastructure
            and all(count == 50 for count in bots.values()), "stock": stock, "stock_differences": differences,
            "extra_provider_items": extras, "infrastructure_differences": infrastructure, "robots": bots,
            "player_inventory_usable": False, "extra_items_granted": 0,
            "furnace_allocation_reference": FOUNDATION_FURNACES, "mall_slots": 12,
            "supplied_assemblers": 6, "additional_assemblers_to_manufacture": 6}
