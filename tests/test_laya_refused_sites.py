# Path: tests/test_laya_refused_sites.py | Purpose: A tile the live check refuses is learned, so line generators stop re-offering the same nearest sites.

"""Evidence: data/laya-saved-world/optblocks-20261105e0 (seed 0, decision 9 on).

After the copper starter and a coverage hop, every copper line candidate the menu checked was
refused as ``collision:transport-belt`` for 37 decisions: the generator offers only the 20 nearest
sites, all sharing one mine and haul corridor, and nothing fed the refusal back.
"""

from training.laya_clean_runner import _submitted_entities, remember_refused_sites, split_refused_sites
from training.laya_lines import line_candidates

# Minimal copper world rebuilt from that run at decision 9.
PORTS = [(3, -1), (26, 22), (26, 54), (43, 5), (60, -12), (83, -35), (75, 5)]
COPPER = [(86.5, -33.5), (90.5, -33.5), (94.5, -33.5), (94.5, -29.5), (98.5, -33.5), (98.5, -29.5), (102.5, -33.5),
          (102.5, -29.5), (106.5, -33.5), (106.5, -29.5), (110.5, -33.5), (114.5, -33.5), (90.5, -37.5), (94.5, -37.5),
          (98.5, -37.5), (102.5, -37.5), (106.5, -37.5), (110.5, -37.5), (114.5, -37.5), (78.5, -45.5), (82.5, -37.5)]
PLACED = [("medium-electric-pole", 71.5, -22.5), ("medium-electric-pole", 76.5, -27.5), ("medium-electric-pole", 79.5, -31.5),
          ("roboport", 83, -35), ("medium-electric-pole", 80.5, -37.5), ("medium-electric-pole", 80.5, -41.5),
          ("electric-mining-drill", 78.5, -45.5), ("electric-furnace", 78.5, -42.5), ("inserter", 78.5, -40.5),
          ("passive-provider-chest", 78.5, -39.5), ("roboport", 75, 5), ("roboport", 60, -12)]
PLAN = {"zones": [{"purpose": "ore:copper-ore", "rect": [76.5, -63.5, 120.5, -23.5]},
                  {"purpose": "smelt:copper-plate", "rect": [73.5, -13.5, 123.5, 36.5]}]}
REFUSED = {"entity": "transport-belt", "position": {"x": 82.5, "y": -32.5}}  # On every nearest candidate's haul.


def world():
    return {"ports": [{"position": {"x": x, "y": y}, "network": "1", "energy": 1e8, "construction_radius": 55,
                       "logistic_radius": 25} for x, y in PORTS],
            "networks": {"1": {"items": {"passive-provider-chest": 16}}},
            "resources": [{"item": "copper-ore", "position": {"x": x, "y": y}} for x, y in COPPER]}


def lines(extra=()):
    placed = [{"entity": e, "position": {"x": x, "y": y}} for e, x, y in PLACED] + list(extra)
    return line_candidates(world(), PLAN, {"copper-plate"}, obstacles=placed)


def belts(candidate):
    return {(a["position"]["x"], a["position"]["y"]) for phase in candidate["plan"]["phases"]
            for a in phase["actions"] if a["entity"] == "transport-belt"}


def test_a_refused_tile_removes_every_candidate_that_used_it():
    refused = (REFUSED["position"]["x"], REFUSED["position"]["y"])
    before = lines()
    assert before and all(refused in belts(c) for c in before)  # One shared corridor, so one refusal kills them all.
    after = lines([REFUSED])
    assert not any(refused in belts(c) for c in after)
    assert {c["option_id"] for c in after}.isdisjoint(c["option_id"] for c in before)


def test_live_collision_reasons_become_blocked_footprints(tmp_path):
    results = {"line.a": {"reason": "collision:transport-belt@82.5,-32.5", "bill": None},
               "line.b": {"reason": "collision:medium-electric-pole@-3,10.5"},
               "line.c": {"reason": "item_shortfall:inserter"}, "line.d": {"reason": None, "bill": {"inserter": 1}}}
    plain, refused = split_refused_sites(results)
    assert [r["reason"] for r in plain.values()] == ["collision:transport-belt", "collision:medium-electric-pole",
                                                     "item_shortfall:inserter", None]
    assert refused == [REFUSED, {"entity": "medium-electric-pole", "position": {"x": -3.0, "y": 10.5}}]
    remember_refused_sites(tmp_path, refused)
    remember_refused_sites(tmp_path, refused[:1])  # Repeats are not stored twice.
    adapter = type("Adapter", (), {"root": tmp_path})()
    assert _submitted_entities(adapter) == refused
    remember_refused_sites(None, refused)  # An adapter without a root keeps nothing and does not fail.


def test_the_live_refusal_names_what_is_on_the_tile():
    plain, refused = split_refused_sites({"x": {"reason": "collision:roboport@58,-24#water"},
                                          "y": {"reason": "collision:transport-belt@81.5,-22.5#grass-1|tree"}})
    assert [r["reason"] for r in plain.values()] == ["collision:roboport", "collision:transport-belt"]
    assert [r.get("blocker") for r in refused] == ["water", "grass-1|tree"]
    assert refused[0]["position"] == {"x": 58.0, "y": -24.0}
