# Path: tests/test_laya_power.py | Purpose: Protect the user's power rules: reuse coverage, medium-pole covers, big-pole long relays, substations only in mine/mall areas.

import math

from planners.plan_validation import validate_build_plan
from training.laya_power import POLES, POWERED, _covers, power_variants
from training.laya_saved_world import ghost


def world(pole=(0.5, 0.5), name="medium-electric-pole"):
    supply, wire, _ = POLES[name]
    return {"poles": [{"name": name, "generated": True, "position": {"x": pole[0], "y": pole[1]},
                       "wire": wire, "supply": supply, "grid": "1"}]}


def candidate(family, build, relay=()):
    actions = [ghost("substation", x, y) for x, y in relay] + build
    return {"family": family, "option_id": f"{family}.x", "summary": f"{family} build", "network_anchor": {"x": 0, "y": 0},
            "plan": {"surface": "nauvis", "force": "player", "atomic": True,
                     "phases": [{"name": "saved-world", "actions": actions}]}}


SMELT = [ghost("requester-chest", 37, 30), ghost("inserter", 38, 30, "west"), ghost("electric-furnace", 40, 30),
         ghost("inserter", 42, 30, "west"), ghost("passive-provider-chest", 43, 30)]
MINE = [ghost("electric-mining-drill", 40.5, 30.5), ghost("passive-provider-chest", 40.5, 28.5)]


def poles_of(variant):
    return [(a["entity"], (a["position"]["x"], a["position"]["y"])) for a in variant["plan"]["phases"][0]["actions"]
            if a["entity"] in POLES]


def check_powered_and_wired(variant, facts):
    validate_build_plan(variant["plan"])
    actions = variant["plan"]["phases"][0]["actions"]
    poles = poles_of(variant)
    supplies = [(p, POLES[n][0]) for n, p in poles] + [((q["position"]["x"], q["position"]["y"]), q["supply"]) for q in facts["poles"]]
    assert all(any(_covers(p, s, a) for p, s in supplies) for a in actions if a["entity"] in POWERED)
    placed = [((q["position"]["x"], q["position"]["y"]), q["wire"]) for q in facts["poles"]]
    for name, point in poles:  # Each new pole reaches an earlier pole within the smaller wire reach.
        assert any(math.dist(point, p) <= min(POLES[name][1], w) for p, w in placed)
        placed.append((point, POLES[name][1]))


def test_substations_only_for_mine_and_mall_builds():
    facts = world()
    smelt = power_variants(candidate("smelt", SMELT, relay=[(12, 9), (23, 16), (34, 23), (40, 34)]), facts)
    assert smelt and not any(n == "substation" for v in smelt for n, _ in poles_of(v))
    mine = power_variants(candidate("mine", MINE, relay=[(12, 9), (23, 16), (34, 23), (44, 30)]), facts)
    assert {v["power"] for v in mine} >= {"m", "s"}
    shared = next(v for v in mine if v["power"] == "s")
    assert [n for n, _ in poles_of(shared)].count("substation") == 1  # One shared substation, no substation chain.
    for variant in smelt + mine:
        check_powered_and_wired(variant, facts)


def test_build_inside_existing_coverage_needs_no_new_power():
    facts = world(pole=(40, 26), name="substation")
    [variant] = power_variants(candidate("mine", MINE, relay=[(12, 9), (44, 30)]), facts)
    assert variant["power"] == "e" and poles_of(variant) == []
    check_powered_and_wired(variant, facts)


def test_long_distance_offers_a_big_pole_relay_and_poles_never_overlap_the_build():
    facts = world()
    variants = power_variants(candidate("smelt", SMELT, relay=[(12, 9), (23, 16), (34, 23), (40, 34)]), facts)
    big = next(v for v in variants if v["power"] == "b")
    assert any(n == "big-electric-pole" for n, _ in poles_of(big))
    for variant in variants:
        check_powered_and_wired(variant, facts)
    # Live regression 2026-10-03: a relay pole landed on the plan's own roboport.
    roboport = power_variants(candidate("expand", [ghost("roboport", 35, -33)], relay=[(12, -10), (24, -21), (35, -32)]), facts)
    assert roboport
    for variant in roboport:
        check_powered_and_wired(variant, facts)


def test_relays_avoid_placed_world_entities():
    facts = world()
    facts["poles"].append({"name": "substation", "generated": True, "position": {"x": 7, "y": 7}, "wire": 18,
                           "supply": 9, "grid": "1"})  # Sits where a straight relay would put a hop.
    facts["ports"] = [{"position": {"x": 20, "y": 15}, "energy": 1}]
    for variant in power_variants(candidate("smelt", SMELT, relay=[(12, 9), (23, 16), (34, 23), (40, 34)]), facts):
        for name, (x, y) in poles_of(variant):
            assert not any(abs(x - ox) < (POLES[name][2] + os) / 2 and abs(y - oy) < (POLES[name][2] + os) / 2
                           for ox, oy, os in [(7, 7, 2), (20, 15, 4)])


def test_internal_power_is_rejected_when_a_link_hop_cannot_be_placed(monkeypatch):
    import training.laya_power as power
    facts = {"poles": [{"name": "substation", "generated": True, "position": {"x": 0, "y": 0}, "wire": 18,
                        "supply": 9, "grid": "1"}], "ports": [], "machines": []}
    line = {"family": "line", "summary": "s", "plan": {"surface": "nauvis", "force": "player", "atomic": True, "phases": [
        {"name": "p", "actions": [{"action_type": "place_ghost", "entity": "substation", "position": {"x": 40, "y": 0}},
                                  {"action_type": "place_ghost", "entity": "electric-mining-drill",
                                   "position": {"x": 43.5, "y": 0.5}, "direction": "north"}]}]}}
    assert power.internal_power_variant(line, facts) is not None
    monkeypatch.setattr(power, "_place", lambda point, size, build: None)  # every hop blocked
    assert power.internal_power_variant(line, facts) is None
