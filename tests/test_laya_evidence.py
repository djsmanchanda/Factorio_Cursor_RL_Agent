# Path: tests/test_laya_evidence.py | Purpose: Protect ordered reach, held-at-end, recorded versions and the baselined C6 in tools/laya_evidence.py.
from __future__ import annotations

import pytest

from tools.laya_evidence import arm_summary, milestone_evidence

pytestmark = pytest.mark.fast


def _c6(tick, deltas, produced=True, status="passed"):
    return {"at": f"decision {tick}", "tick": tick, "predicate_evidence": {"C6": {
        "predicate_version": "c6-v2", "status": status,
        "evidence": {"checks": {"advanced_circuits_newly_produced": produced}, "consumer_deltas": deltas}}}}


def _c86c_reference_s83039():
    # Trimmed from C86c job C0-reference-s83039: a re-recipe'd mall cell's lifetime counter
    # (1,902) appears as a consumer delta at 625,125; real consumption starts at 633,668.
    return {"arm": "reference", "status": "completed",
            "strict_reached": {"C0": 238553, "C2": 329851, "C3": 353023, "C4": 518250, "C5": 527795, "C6": 625125},
            "strict_current_at_end": {"C0": True, "C1": False, "C2": True, "C3": True, "C4": True, "C5": False, "C6": True},
            "strict_current": [
                _c6(238553, {"electric-furnace": 0}, produced=False, status="failed"),
                _c6(615453, {"electric-furnace": 0}, status="failed"),
                _c6(625125, {"electric-furnace": 0, "productivity-module": 1902}),
                _c6(633668, {"electric-furnace": 0, "productivity-module": 1904}),
            ]}


def test_c6_baselines_a_consumer_key_first_seen_mid_run():
    c6 = milestone_evidence(_c86c_reference_s83039())["C6"]
    assert c6 == {"reached_any": True, "reached_ordered": True, "held_at_end": True,
                  "first_tick": 633668, "ordered_tick": 633668, "predicate_version": "c6-v2"}


def test_c6_relabel_without_later_consumption_is_not_reached():
    job = _c86c_reference_s83039()
    job["strict_current"] = job["strict_current"][:3]
    c6 = milestone_evidence(job)["C6"]
    assert (c6["reached_any"], c6["reached_ordered"], c6["first_tick"]) == (True, False, None)


def test_ordered_reach_requires_the_prerequisite_chain_but_not_c1():
    job = {"status": "completed", "strict_reached": {"C2": 100, "C4": 300}, "strict_current_at_end": {"C4": False}}
    evidence = milestone_evidence(job)
    assert evidence["C2"]["reached_ordered"]  # C1 is transient, not a prerequisite
    assert evidence["C4"]["reached_any"] and not evidence["C4"]["reached_ordered"]
    assert evidence["C4"]["held_at_end"] is False


def test_a_milestone_before_its_prerequisite_counts_when_the_chain_completes():
    evidence = milestone_evidence({"status": "completed", "strict_reached": {"C2": 100, "C3": 500, "C4": 300}})
    assert evidence["C4"]["reached_ordered"]
    assert (evidence["C4"]["first_tick"], evidence["C4"]["ordered_tick"]) == (300, 500)


def test_legacy_rows_fall_back_to_accepted_ever():
    evidence = milestone_evidence({"status": "completed", "accepted_ever": ["C0", "C2", "C3"],
                                   "c5_predicate_version": "c5-v1"})
    assert evidence["C3"]["reached_ordered"] and evidence["C3"]["first_tick"] is None
    assert evidence["C5"]["predicate_version"] == "c5-v1"


def test_arm_summary_counts_completed_jobs_over_planned():
    arms = arm_summary([_c86c_reference_s83039(), {"arm": "reference", "status": "failed"}])
    reference = arms["reference"]
    assert (reference["planned"], reference["completed"]) == (2, 1)
    assert (reference["any"]["C5"], reference["held"]["C5"]) == (1, 0)
    assert reference["versions"] == {"c6-v2": 1}


def test_c6a_plastic_used_is_reported_after_c4():
    # Live tip-c3-120 shape: C6a (plastic used, c6a-v1) reached and held; C6 not reached.
    job = {"arm": "control", "status": "completed",
           "strict_reached": {"C0": 238550, "C2": 365472, "C3": 504347, "C4": 555048, "C6a": 640000},
           "strict_current_at_end": {"C0": True, "C2": True, "C3": True, "C4": True, "C5": True, "C6a": True, "C6": False},
           "strict_current": []}
    c6a = milestone_evidence(job)["C6a"]
    assert c6a["reached_ordered"] is True and c6a["held_at_end"] is True and c6a["first_tick"] == 640000
    job["strict_reached"].pop("C4")
    assert milestone_evidence(job)["C6a"]["reached_ordered"] is False
