# Path: tests/test_laya_refused_expand.py | Purpose: A footprint the live check refused is not offered again by coverage hops or power relays.

"""Evidence: data/laya-saved-world/optblocks-20261105f0 (decisions 11-50).

Five roboport hop sites toward copper (and two relay poles) were refused as ``collision:*``,
recorded in blocked-footprints.json, and then offered again on every later decision because the
ring and generator coverage hops, and the power relays, never received the obstacles the line and
starter generators got.
"""

from training.laya_clean_runner import compile_menu
from training.laya_factory_status import compile_status
from training.laya_mission import phase


def recipe(ingredients, ticks, amount=1):
    return {"ingredients": [{"name": n, "amount": a} for n, a in ingredients], "product_amount": amount, "energy_ticks": ticks}


def world():
    return {"tick": 100, "pending_ghosts": 0, "machines": [], "production": {}, "consumption": {},
            "ports": [{"position": {"x": 0, "y": 0}, "network": "1", "energy": 100,
                       "construction_radius": 55, "logistic_radius": 25}],
            "poles": [{"name": "substation", "generated": True, "position": {"x": 2, "y": 2}, "wire": 18, "supply": 9,
                       "grid": "1"}],
            "networks": {"1": {"items": {"electric-mining-drill": 5, "inserter": 20}, "reserved": {},
                               "construction_robots": 50}},
            "resources": [{"item": "iron-ore", "position": {"x": 20, "y": 20}},
                          {"item": "copper-ore", "position": {"x": 90, "y": 0}}],
            "recipes": {"iron-plate": recipe([("iron-ore", 1)], 192), "copper-plate": recipe([("copper-ore", 1)], 192)}}


def validator():
    def validate(candidates):
        return {c["option_id"]: {"reason": None, "bill": {"roboport": 1}} for c in candidates}
    return validate


def hop_sites(menu):
    return {(a["position"]["x"], a["position"]["y"]) for o in menu["options"] if o["family"] == "expand"
            for a in o["plan"]["phases"][0]["actions"] if a["entity"] == "roboport"}


def build(placed=()):
    facts = world()
    return compile_menu(facts, compile_status(facts, phase("C1")["targets"]), validator(), seed=3, placed=placed)


def test_a_refused_roboport_site_is_not_offered_as_a_coverage_hop_again():
    before = hop_sites(build())
    assert before
    refused = sorted(before)[:2]
    after = hop_sites(build([{"entity": "roboport", "position": {"x": x, "y": y}} for x, y in refused]))
    assert not set(refused) & after  # Neither the ring nor the generator's fixed hops return them.
    assert after  # Other hop sites remain: only the refused footprints are removed.


def relay_poles(menu):
    return {(a["position"]["x"], a["position"]["y"]) for o in menu["options"] if o["family"] == "expand"
            for a in o["plan"]["phases"][0]["actions"] if a["entity"] == "medium-electric-pole"}


def test_relay_poles_route_around_a_refused_pole_tile():
    used = relay_poles(build())
    assert used
    refused = sorted(used)[0]
    after = relay_poles(build([{"entity": "medium-electric-pole", "position": {"x": refused[0], "y": refused[1]}}]))
    assert refused not in after
