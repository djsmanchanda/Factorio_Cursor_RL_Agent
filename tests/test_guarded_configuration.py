# Path: tests/test_guarded_configuration.py | Purpose: Prevent configuration of unrelated or missing robot-built entities.
import copy

import pytest

from orchestrator.guarded_configuration import guard_configuration, require_configuration_baseline

TAGS = {"optimizer_episode": "episode", "optimizer_project": "project",
        "optimizer_transaction": "a" * 32, "optimizer_plan_hash": "b" * 64}
ACTION = {"action_type": "configure_entity", "entity": "assembling-machine-2",
          "position": {"x": 1.5, "y": 2.5}, "recipe": "processing-unit"}


def report():
    return {"version": "optimizer-build-evidence-v2", "source": "robot_built_entity_tags",
            "request_id": "req", "tick": 20, "ok": True, "ownership_claimed": False,
            "dispatch_authorized": False, "surface": "nauvis", "force": "player", "tags": dict(TAGS),
            "entities": [{"entity": ACTION["entity"], "position": dict(ACTION["position"]),
                          "unit_number": 42, "tick": 12, "current": {"state": "present",
                          "direction": {"state": "available", "value": 0},
                          "quality": {"state": "available", "value": "normal"}, "recipe": {"state": "unset"}}}]}


def test_configuration_targets_observed_unit_without_mutating_source():
    original = copy.deepcopy(ACTION)
    guarded = guard_configuration([ACTION], report(), tags=TAGS, surface="nauvis", force="player", submission_tick=10)
    assert guarded == [{**ACTION, "action_type": "configure_entity_if_unit_number", "expected_unit_number": 42}]
    assert ACTION == original


@pytest.mark.parametrize("mutation", ["foreign_force", "foreign_tags", "old_event", "missing", "not_found",
                                    "duplicate", "duplicate_not_found", "duplicate_stale", "duplicate_unit", "claim"])
def test_unverified_configuration_targets_fail_closed(mutation):
    evidence = report()
    if mutation == "foreign_force": evidence["force"] = "other"
    elif mutation == "foreign_tags": evidence["tags"]["optimizer_transaction"] = "c" * 32
    elif mutation == "old_event": evidence["entities"][0]["tick"] = 9
    elif mutation == "missing": evidence["entities"] = []
    elif mutation == "not_found": evidence["entities"][0]["current"] = {"state": "not_found"}
    elif mutation == "duplicate": evidence["entities"].append(copy.deepcopy(evidence["entities"][0]))
    elif mutation in {"duplicate_not_found", "duplicate_stale", "duplicate_unit"}:
        duplicate = copy.deepcopy(evidence["entities"][0])
        if mutation == "duplicate_not_found":
            duplicate["current"] = {"state": "not_found"}
            duplicate["unit_number"] += 1
        elif mutation == "duplicate_stale":
            duplicate["tick"] = 9
            duplicate["unit_number"] += 1
        else:
            duplicate["position"]["x"] += 10
        evidence["entities"].append(duplicate)
    else: evidence["ownership_claimed"] = True
    with pytest.raises(ValueError):
        guard_configuration([ACTION], evidence, tags=TAGS, surface="nauvis", force="player", submission_tick=10)


def test_unidentified_v3_event_cannot_authorize_configuration():
    evidence = report()
    evidence["version"] = "optimizer-build-evidence-v3"
    evidence["entities"][0].pop("unit_number")
    evidence["entities"][0]["current"] = {"state": "identity_unavailable"}
    with pytest.raises(ValueError, match="unique current"):
        guard_configuration([ACTION], evidence, tags=TAGS, surface="nauvis", force="player", submission_tick=10)


def test_v3_allows_identified_target_but_rejects_unitless_duplicate():
    evidence = report()
    evidence["version"] = "optimizer-build-evidence-v3"
    guarded = guard_configuration([ACTION], evidence, tags=TAGS, surface="nauvis", force="player", submission_tick=10)
    assert guarded[0]["expected_unit_number"] == 42
    unitless = copy.deepcopy(evidence["entities"][0])
    unitless.pop("unit_number")
    unitless["current"] = {"state": "identity_unavailable"}
    evidence["entities"].append(unitless)
    with pytest.raises(ValueError, match="unique current"):
        guard_configuration([ACTION], evidence, tags=TAGS, surface="nauvis", force="player", submission_tick=10)
    unitless["position"]["x"] += 5
    guarded = guard_configuration([ACTION], evidence, tags=TAGS, surface="nauvis", force="player", submission_tick=10)
    assert guarded[0]["expected_unit_number"] == 42


@pytest.mark.parametrize("change", ["legacy", "nonempty", "late", "foreign", "missing"])
def test_v3_pre_submission_baseline_rejects_incomplete_or_late_evidence(change):
    baseline = report()
    baseline.update(version="optimizer-build-evidence-v3", entities=[], tick=9)
    if change == "legacy": baseline["version"] = "optimizer-build-evidence-v2"
    elif change == "nonempty": baseline["entities"] = report()["entities"]
    elif change == "late": baseline["tick"] = 11
    elif change == "foreign": baseline["tags"]["optimizer_project"] = "different"
    else: baseline.pop("tick")
    with pytest.raises(ValueError, match="baseline"):
        require_configuration_baseline(baseline, tags=TAGS, surface="nauvis", force="player", submission_tick=10)


def test_v3_empty_baseline_precedes_submission():
    baseline = report()
    baseline.update(version="optimizer-build-evidence-v3", entities=[], tick=9)
    assert require_configuration_baseline(baseline, tags=TAGS, surface="nauvis", force="player", submission_tick=10) == 9


def test_preexisting_target_binds_to_its_pre_submission_unit_number_without_a_build_record():
    evidence = report()
    evidence["entities"] = []  # no robot-build record: this transaction did not create the entity
    key = (ACTION["entity"], ACTION["position"]["x"], ACTION["position"]["y"])
    guarded = guard_configuration([ACTION], evidence, tags=TAGS, surface="nauvis", force="player",
                                  submission_tick=10, preexisting={key: 77})
    assert guarded == [{**ACTION, "action_type": "configure_entity_if_unit_number", "expected_unit_number": 77}]


def test_target_outside_preexisting_map_still_requires_a_build_record():
    evidence = report()
    evidence["entities"] = []
    with pytest.raises(ValueError, match="unique current robot-build record"):
        guard_configuration([ACTION], evidence, tags=TAGS, surface="nauvis", force="player",
                            submission_tick=10, preexisting={("other", 0, 0): 5})


def test_preexisting_unit_numbers_must_be_valid_and_unique():
    key = (ACTION["entity"], ACTION["position"]["x"], ACTION["position"]["y"])
    for bad in (0, True, "7"):
        with pytest.raises(ValueError, match="unit number"):
            guard_configuration([ACTION], report(), tags=TAGS, surface="nauvis", force="player",
                                submission_tick=10, preexisting={key: bad})
    with pytest.raises(ValueError, match="unit number"):
        guard_configuration([ACTION, {**ACTION, "position": {"x": 3.5, "y": 2.5}}], report(), tags=TAGS,
                            surface="nauvis", force="player", submission_tick=10,
                            preexisting={key: 7, (ACTION["entity"], 3.5, 2.5): 7})
