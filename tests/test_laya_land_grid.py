# Path: tests/test_laya_land_grid.py | Purpose: Protect the grid land model: anchored parcels, corridor lattice, ore/smelt/mall assignment.

from training.laya_land_grid import CORRIDOR, PITCH, allocate, grid_plan_options, grid_origin, in_corridor, interior, parcel_of
from training.laya_site_plan import reservation_reason


def facts():
    # Base roboport at (3,-1); iron under the base and in the parcel to the east, copper further east.
    res = [{"item": "iron-ore", "position": {"x": 10.5 + 4 * i, "y": -10.5 + 4 * j}, "remaining": 5000}
           for i in range(18) for j in range(8)]
    res += [{"item": "copper-ore", "position": {"x": 110.5 + 4 * i, "y": -10.5 + 4 * j}, "remaining": 5000}
            for i in range(10) for j in range(8)]
    return {"resources": res, "ports": [{"position": {"x": 3, "y": -1}, "energy": 1, "network": "1",
                                         "construction_radius": 55, "logistic_radius": 25}],
            "poles": [{"name": "substation", "generated": True, "position": {"x": 0, "y": 0}}], "machines": []}


def test_grid_is_anchored_on_the_base_and_corridors_form_a_lattice():
    origin = grid_origin((3, -1))
    assert parcel_of(origin, 3, -1) == (0, 0)
    x0, y0, x1, y1 = interior(origin, (0, 0))
    assert x1 - x0 == y1 - y0 == PITCH - CORRIDOR
    grid = {"origin": origin}
    assert in_corridor(grid, (x0 - 2, y0 + 5, x0 - 1, y0 + 6))          # west corridor of the base parcel
    assert in_corridor(grid, (x0 + 5, y0 + PITCH - 7, x0 + 6, y0 + PITCH - 5))  # next parcel's north corridor
    assert not in_corridor(grid, (x0 + 1, y0 + 1, x0 + 4, y0 + 4))
    assert in_corridor(grid, (origin[0] + 50 * PITCH + 1, 0, origin[0] + 50 * PITCH + 2, 1))  # works far away


def test_plan_assigns_ore_and_smelting_parcels_and_keeps_the_base_free():
    options = grid_plan_options(facts())
    assert options
    plan = options[0]["site_plan"]
    purposes = {(z["purpose"], tuple(z.get("parcel") or ())) for z in plan["zones"]}
    assert ("ore:iron-ore", (1, 0)) in purposes and ("ore:copper-ore", (2, 0)) in purposes
    assert not any(z.get("parcel") == [0, 0] for z in plan["zones"])  # the base parcel stays the base
    assert any(z["zone_id"] == "base-ore:iron-ore" for z in plan["zones"])  # starters may mine under the base
    smelt = {z["purpose"]: tuple(z["parcel"]) for z in plan["zones"] if z["purpose"].startswith("smelt:")}
    # Edge-adjacent to its ore parcel: belts cross one corridor, never a third parcel.
    assert abs(smelt["smelt:iron-plate"][0] - 1) + abs(smelt["smelt:iron-plate"][1]) == 1


def test_reservations_keep_machines_out_of_corridors_but_let_belts_cross():
    plan = grid_plan_options(facts())[0]["site_plan"]
    x0, y0, *_ = interior(tuple(plan["grid"]["origin"]), (3, 3))
    build = lambda entity: {"family": "smelt", "product": None, "plan": {"phases": [{"actions": [
        {"entity": entity, "position": {"x": x0 - 3, "y": y0 + 10.5}}]}]}}
    assert reservation_reason(plan, build("electric-furnace")) == "reserved:grid-corridor"
    assert reservation_reason(plan, build("transport-belt")) is None


def test_a_purpose_grows_into_the_parcel_next_to_its_own():
    plan = grid_plan_options(facts())[0]["site_plan"]
    first = next(tuple(z["parcel"]) for z in plan["zones"] if z["purpose"] == "smelt:iron-plate")
    second = allocate(plan, "smelt:iron-plate", [(1, 0)])
    assert abs(second["parcel"][0] - first[0]) + abs(second["parcel"][1] - first[1]) == 1


def test_wide_survey_merges_chunked_results_and_plans_oil_parcels():
    import json
    from training.laya_land_grid import survey, survey_ore_parcels
    calls = []

    def command(lua):  # Two survey boxes see parts of the same parcel.
        calls.append(lua)
        return json.dumps({"-4:-1": {"crude-oil": {"tiles": 3, "amount": 900}},
                           "1:0": {"iron-ore": {"tiles": 40, "amount": 4000}}})
    found = survey(command, (-29, -33), 256)
    assert len(calls) == 4 and "find_entities_filtered" in calls[0]
    assert found["-4:-1"]["crude-oil"] == {"tiles": 12, "amount": 3600}
    assert survey_ore_parcels(found, (0, 0)) == {(-4, -1): "crude-oil", (1, 0): "iron-ore"}  # 160 iron tiles
    assert survey_ore_parcels({"9:9": {"iron-ore": {"tiles": 500, "amount": 1}}}, (0, 0)) == {}  # beyond PLAN_RADIUS


def test_built_parcels_stay_inside_the_action_area():
    from training.laya_land_grid import actionable
    plan = grid_plan_options(facts())[0]["site_plan"]
    assert all(actionable(z["rect"]) for z in plan["zones"] if z["purpose"].startswith("smelt:"))
    assert not actionable([105, -27, 163, 31])  # crosses x = 126
