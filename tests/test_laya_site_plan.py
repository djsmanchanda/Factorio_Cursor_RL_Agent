# Path: tests/test_laya_site_plan.py | Purpose: Protect Laya land planning: patch survey, mall-site alternatives and hard land reservations.

from training.laya_saved_world import ghost
from training.laya_site_plan import plan_options, reservation_reason, survey_patches


def facts():
    iron = [{"item": "iron-ore", "position": {"x": 40 + 4 * i, "y": 0 + 4 * j}} for i in range(3) for j in range(3)]
    copper = [{"item": "copper-ore", "position": {"x": -60 + 4 * i, "y": 0}} for i in range(2)]
    return {"resources": iron + copper, "ports": [{"position": {"x": 0, "y": 0}, "energy": 1}]}


def build(family, product, entities):
    return {"family": family, "product": product,
            "plan": {"phases": [{"actions": [ghost(e, x, y) for e, x, y in entities]}]}}


def test_patches_cluster_on_the_survey_grid():
    patches = survey_patches(facts())
    assert [(p["resource"], p["samples"]) for p in patches] == [("copper-ore", 2), ("iron-ore", 9)]
    assert patches[1]["rect"] == [38.0, -2.0, 50.0, 10.0]


def test_mall_site_is_a_choice_and_reservations_protect_land():
    options = plan_options(facts())
    assert len(options) >= 2 and all(o["family"] == "plan" for o in options)
    plan = options[0]["site_plan"]
    purposes = {z["purpose"] for z in plan["zones"]}
    assert {"ore:iron-ore", "ore:copper-ore", "smelt:iron-plate", "smelt:copper-plate", "mall"} <= purposes
    strip = next(z for z in plan["zones"] if z["purpose"] == "smelt:iron-plate")
    assert strip["rect"][2] <= 37  # Iron's strip faces the base (west of the patch).
    mx, my = plan["mall_site"]
    # Ore is for mining that ore only; poles and other builds may not take it.
    assert reservation_reason(plan, build("mine", "iron-ore", [("electric-mining-drill", 44.5, 4.5)])) is None
    # Cheap relay poles may cross ore (they are relocatable); substations and machines may not.
    assert reservation_reason(plan, build("smelt", "iron-plate", [("medium-electric-pole", 44.5, 4.5)])) is None
    assert reservation_reason(plan, build("smelt", "iron-plate", [("substation", 44, 4)])) == "reserved:ore:iron-ore"
    assert reservation_reason(plan, build("expand", None, [("roboport", 44, 4)])) == "reserved:ore:iron-ore"
    # The smelting strip is kept for that plate's line (and its mine's output side).
    sx = (strip["rect"][0] + strip["rect"][2]) / 2
    assert reservation_reason(plan, build("smelt", "iron-plate", [("electric-furnace", sx, 4)])) is None
    assert reservation_reason(plan, build("smelt", "copper-plate", [("electric-furnace", sx, 4)])) == "reserved:smelt:iron-plate"
    # Mall builds go in the mall zone; nothing else may.
    assert reservation_reason(plan, build("assemble", "iron-gear-wheel", [("assembling-machine-1", mx, my)])) is None
    assert reservation_reason(plan, build("assemble", "iron-gear-wheel", [("assembling-machine-1", mx + 40, my + 40)])) == "outside_mall_zone"
    assert reservation_reason(plan, build("smelt", "iron-plate", [("electric-furnace", mx, my)])) == "reserved:mall"


def test_furnace_sites_come_from_the_plates_smelting_strip():
    from training.laya_site_plan import zone_smelt_candidates
    world = {**facts(), "ports": [{"position": {"x": 0, "y": 0}, "energy": 1, "network": "1", "construction_radius": 55}]}
    plan = plan_options(world)[0]["site_plan"]
    candidates = zone_smelt_candidates(world, plan, {"iron-plate"})
    assert candidates and {c["product"] for c in candidates} == {"iron-plate"}
    for c in candidates:
        assert reservation_reason(plan, c) is None  # Every generated cell sits inside its own strip.
        assert c["plan"]["phases"][0]["actions"][0]["logistic_requests"] == [{"name": "iron-ore", "count": 20}]
    assert zone_smelt_candidates(world, plan, set()) == []  # Only plates a need asks for get cells.


def test_ring_hops_cover_every_direction_and_respect_reservations():
    from training.laya_site_plan import ring_expand_candidates
    world = {**facts(), "ports": [{"position": {"x": 0, "y": 0}, "energy": 1, "network": "1", "construction_radius": 55}]}
    plan = plan_options(world)[0]["site_plan"]
    hops = ring_expand_candidates(world, plan)
    spots = {(a["position"]["x"], a["position"]["y"]) for h in hops for a in h["plan"]["phases"][0]["actions"]}
    assert {x > 0 for x, _ in spots} == {True, False} and {y > 0 for _, y in spots} == {True, False}
    assert all(reservation_reason(plan, h) is None for h in hops)
    assert len(hops) < len(ring_expand_candidates(world, None))  # Some ring spots fall on reserved land.


def test_mine_in_its_own_patch_is_allowed_where_another_patch_box_overlaps():
    plan = {"zones": [{"zone_id": "a", "purpose": "ore:iron-ore", "rect": [16.5, -27.5, 60.5, 24.5]},
                      {"zone_id": "b", "purpose": "ore:copper-ore", "rect": [56.5, 20.5, 72.5, 36.5]}]}
    copper_drill = build("mine", "copper-ore", [("electric-mining-drill", 58.5, 22.5)])
    assert reservation_reason(plan, copper_drill) is None
    detail = []
    wrong = build("mine", "copper-ore", [("electric-mining-drill", 30.5, 0.5)])  # Only on iron land.
    assert reservation_reason(plan, wrong, detail) == "reserved:ore:iron-ore"
    assert detail == [{"reason": "reserved:ore:iron-ore", "entity": "electric-mining-drill",
                       "position": {"x": 30.5, "y": 0.5}}]
