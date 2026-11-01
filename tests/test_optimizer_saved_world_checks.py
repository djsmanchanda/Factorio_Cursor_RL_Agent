# Path: tests/test_optimizer_saved_world_checks.py | Purpose: Replay world facts through the optimizer fluid-purity and attachment checks.
import copy

import pytest

from planners.plan_validation import actions
from tests.test_fluid_topology import snapshot as fluid_snapshot
from tests.test_oil_block_artifact import oil_arguments  # noqa: F401  (fixture dependency)
from tests.test_optimizer_power_attachment import oil_case  # noqa: F401  (fixture dependency)
from tests.test_optimizer_power_topology import powered, sample  # noqa: F401
from tests.test_optimizer_saved_world import _network_facts, offer, world  # noqa: F401
from tools import optimizer_saved_world as integration
from training.laya_saved_world import SavedWorldAdapter, SavedWorldError

ANCHOR = {"x": 3, "y": -1}
TICK = 200


def survey_for(plan, fluid=0):
    n = len(list(actions(plan)))
    return {"actions": n, "with_fluidboxes": fluid, "without_fluidboxes": n - fluid, "unknown": 0}


def science_verifier(world):
    _adapter, bound, _ledger = world
    return integration.optimizer_check_verifier(bound, episode_id="episode-1", network_anchor=ANCHOR)


def oil_verifier(powered):
    bound, cap, _anchor = powered
    return integration.optimizer_check_verifier(bound, episode_id="episode-1", network_anchor=ANCHOR,
        fluid_capture={"sha256": cap["expected_sha256"], "json": cap["catalog_bytes"].decode()})


# --- candidate rebuild + plumbing (science block: no fluid boxes) -------------------------------------------------

def test_non_fluid_block_passes_fluid_purity_from_the_engine_survey_alone(world, monkeypatch):
    adapter, bound, _ledger = world
    candidate = offer(world)
    verifier = science_verifier(world)
    monkeypatch.setattr(integration.OptimizerCheckVerifier, "_attachment",
                        lambda self, entry, tick: {"reason": None, "facts": {"anchor_grid": "7"}})
    entry = {"bill": candidate["bill"], "fluid_prototype_survey": survey_for(bound.artifact["plan"])}
    result = verifier(adapter, candidate, entry, {"tick": TICK})
    assert result["fluid_purity"] == {"reason": None, "facts": {"fluid_entities": 0, "topology": "not_applicable"}}
    assert result["safe_infrastructure_attachment"]["reason"] is None
    # prepare() asks the engine query for the power snapshot; a caller flag is never needed.
    assert verifier.prepare(candidate)["inspect_power_topology"] is True
    assert "inspect_power_topology" not in candidate


@pytest.mark.parametrize("change,reason", [
    ("missing_survey", "fluid_survey_unverified"), ("partial_survey", "fluid_survey_unverified"),
    ("unknown_survey", "fluid_survey_unverified"), ("fluid_no_catalog", "fluid_topology_unpinned"),
    ("pipe_contact", "surface_pipe_world_contact"), ("underground", "underground_world_competitor"),
    ("pipe_unverified", "surface_pipe_world_unverified"),
])
def test_fluid_purity_reasons_for_unverified_or_present_fluids(world, change, reason):
    adapter, bound, _ledger = world
    candidate = offer(world)
    entry = {"fluid_prototype_survey": survey_for(bound.artifact["plan"])}
    if change == "missing_survey": entry.pop("fluid_prototype_survey")
    elif change == "partial_survey": entry["fluid_prototype_survey"]["actions"] -= 1
    elif change == "unknown_survey": entry["fluid_prototype_survey"].update(unknown=1, without_fluidboxes=entry["fluid_prototype_survey"]["without_fluidboxes"] - 1)
    elif change == "fluid_no_catalog": entry["fluid_prototype_survey"].update(with_fluidboxes=1, without_fluidboxes=entry["fluid_prototype_survey"]["without_fluidboxes"] - 1)
    else: entry["reason"] = reason
    result = science_verifier(world)(adapter, candidate, entry, {"tick": TICK})
    assert result["fluid_purity"]["reason"] == reason


def test_attachment_is_unpinned_without_a_bound_power_anchor_and_collision_is_named(world):
    adapter, _bound, _ledger = world
    candidate = offer(world)
    verifier = science_verifier(world)
    assert verifier(adapter, candidate, {}, {"tick": TICK})["safe_infrastructure_attachment"]["reason"] == "attachment_unpinned"
    collided = verifier(adapter, candidate, {"reason": "collision"}, {"tick": TICK})
    assert collided["safe_infrastructure_attachment"]["reason"] == "attachment_collision"


def test_changed_unbound_or_untimed_candidates_fail_every_check(world):
    adapter, bound, _ledger = world
    candidate = offer(world)
    verifier = science_verifier(world)
    entry = {"fluid_prototype_survey": survey_for(bound.artifact["plan"])}
    altered = copy.deepcopy(candidate)
    altered["plan"]["phases"][0]["actions"][0]["position"]["x"] += 1
    for case, reason in [(altered, "candidate_or_funding_changed"),
                         ({k: v for k, v in candidate.items() if k != "optimizer_binding"}, "candidate_not_bound")]:
        result = verifier(adapter, case, entry, {"tick": TICK})
        assert {r["reason"] for r in result.values()} == {reason}
    assert {r["reason"] for r in verifier(adapter, candidate, entry, {}).values()} == {"query_tick_unavailable"}


# --- attachment: the real same-query pole snapshot diagnostic (powered oil artifact) -------------------------------

def test_attachment_passes_only_for_the_observed_anchor_with_no_foreign_grid(powered):
    verifier = oil_verifier(powered)
    result = verifier._attachment({"power_topology_snapshot": sample(powered[2])}, TICK)
    assert result["reason"] is None
    assert result["facts"] == {"anchor_grid": "7", "anchor_unit": powered[2]["unit_number"],
                               "anchor_position": powered[2]["position"], "planned_poles": 57}


@pytest.mark.parametrize("damage,reason", [
    ("missing", "attachment_anchor_missing"), ("unit", "attachment_anchor_missing"),
    ("position", "attachment_anchor_mismatch"), ("quality", "attachment_anchor_mismatch"),
    ("grid", "attachment_anchor_network_unavailable"), ("incomplete", "attachment_unverified:snapshot_incomplete"),
    ("tick", "attachment_unverified:snapshot_tick_mismatch"), ("merge", "attachment_grid_merge"),
    ("absent", "attachment_snapshot_unavailable"), ("mods", "attachment_unverified"),
])
def test_attachment_failures_have_precise_reason_codes(powered, damage, reason):
    snap = sample(powered[2])
    pole = snap["poles"][0]
    if damage == "missing": snap["poles"] = {}
    elif damage == "unit": pole["unit_number"] += 1
    elif damage == "position": pole["position"]["x"] += 1
    elif damage == "quality": pole.pop("quality")
    elif damage == "grid": pole["grid"] = "nil"
    elif damage == "incomplete": snap["complete"] = False
    elif damage == "tick": snap["tick"] = TICK + 1
    elif damage == "mods": snap["mods"] = {"base": "other"}
    elif damage == "merge":
        snap["poles"].append({"name": "medium-electric-pole", "position": {"x": 5.5, "y": -5.5}, "unit_number": 11,
                              "quality": "normal", "wire": 9, "supply": 3.5, "grid": "8", "generated": False})
    entry = {} if damage == "absent" else {"power_topology_snapshot": snap}
    assert oil_verifier(powered)._attachment(entry, TICK)["reason"] == reason


# --- fluid topology when fluid-box entities are present ---------------------------------------------------------

def fluid_entry(powered, **overrides):
    bound = powered[0]
    entry = {"fluid_prototype_survey": survey_for(bound.artifact["plan"], fluid=10),
             "fluid_topology_snapshot": fluid_snapshot(tick=TICK), "power_topology_snapshot": sample(powered[2])}
    entry.update(overrides)
    return entry


def test_fluid_present_requires_a_clear_same_query_topology_diagnostic(powered, monkeypatch):
    verifier = oil_verifier(powered)
    # Real diagnostic on an empty snapshot cannot verify the oil geometry's undergrounds.
    assert verifier._fluid_purity(fluid_entry(powered), TICK)["reason"] == "fluid_topology_unverified"
    for overrides in ({"fluid_topology_snapshot": None}, {"fluid_topology_snapshot": fluid_snapshot(tick=TICK - 1)}):
        assert verifier._fluid_purity(fluid_entry(powered, **overrides), TICK)["reason"] == "fluid_topology_unverified"
    assert verifier._fluid_purity(fluid_entry(powered, power_topology_snapshot={"mods": {"x": "1"}}), TICK)["reason"] == "fluid_catalog_unverified"
    seen = {}
    def fake(status):
        def check(bound, **kwargs):
            seen.update(kwargs)
            return {"status": status, "networks": [1, 2], "conflicts": [], "unverified": []}
        return check
    import tools.optimizer_fluid_topology as topology
    monkeypatch.setattr(topology, "check_bound_fluid_topology", fake("rejected"))
    assert verifier._fluid_purity(fluid_entry(powered), TICK)["reason"] == "fluid_topology_conflict"
    monkeypatch.setattr(topology, "check_bound_fluid_topology", fake("consistent"))
    clear = verifier._fluid_purity(fluid_entry(powered), TICK)
    assert clear["reason"] is None and clear["facts"]["network_count"] == 2 and clear["facts"]["fluid_entities"] >= 10
    # Snapshot must be the same query's tick, with no allowed age.
    assert seen["reference_tick"] == TICK and seen["max_snapshot_age_ticks"] == 0


def test_catalog_fluid_ports_count_even_when_the_survey_reports_none(powered, monkeypatch):
    verifier = oil_verifier(powered)
    entry = fluid_entry(powered)
    entry["fluid_prototype_survey"] = survey_for(powered[0].artifact["plan"], fluid=0)
    import tools.optimizer_fluid_topology as topology
    monkeypatch.setattr(topology, "check_bound_fluid_topology",
                        lambda *_a, **_k: {"status": "rejected", "networks": [], "conflicts": [], "unverified": []})
    assert verifier._fluid_purity(entry, TICK)["reason"] == "fluid_topology_conflict"
