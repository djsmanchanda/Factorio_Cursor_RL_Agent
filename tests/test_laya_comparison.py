# Path: tests/test_laya_comparison.py
# Purpose: Reject easier-start, shadow, incomplete and regressing replacement evidence.

from copy import deepcopy

import pytest

from training.laya_comparison import LayaComparisonError, compare_matched_runs, conditions_hash


def _evidence():
    conditions = {
        "initial_state_hash": "sha256:" + "1" * 64,
        "starting_inventory": {"iron-plate": 20}, "research": [],
        "objective": {"item": "iron-plate", "rate": 1}, "budgets": {"max_ticks": 6000},
        "factorio_version": "2.1.19", "mod_set_hash": "sha256:" + "2" * 64,
        "bootstrap_profile": "reduced-v1",
    }
    suite = {"version": "1.0.0", "suite_id": "frozen-test", "cases": [
        {"case_id": str(i), "family": "production", "seed": i, "split": "held_out", "conditions": deepcopy(conditions)} for i in range(3)
    ]}
    baseline = [{
        "case_id": str(i), "controller_kind": "deterministic_workflow", "controller_hash": "sha256:" + "3" * 64,
        "execution_authority": "primary", "evidence_level": "observed_live",
        "artifact_hash": "sha256:" + f"{i + 10:064x}", "conditions_hash": conditions_hash(conditions),
        "execution_id": f"baseline-{i}", "seed": i, "family": "production",
        "runtime": {"kind": "disposable_training", "surface": "training/test", "force": "training-test",
                    "worker_id": "worker-test", "save_id": "save-test", "initial_state_hash": conditions["initial_state_hash"],
                    "mod_set_hash": conditions["mod_set_hash"], "factorio_version": "2.1.19"},
        "metrics": {"objective_completed": True, "safety_violations": 0, "elapsed_ticks": 100, "material_cost": 10, "sustained_rate": 1, "escalations": 0},
    } for i in range(3)]
    challenger = deepcopy(baseline)
    for i, row in enumerate(challenger):
        row.update(controller_kind="laya", controller_hash="sha256:" + "5" * 64)
        row.update(execution_id=f"challenger-{i}", artifact_hash="sha256:" + f"{i + 20:064x}")
        row["metrics"]["elapsed_ticks"] = 90
    return suite, baseline, challenger


def test_matched_better_results_only_offer_review_and_preserve_incumbent():
    report = compare_matched_runs(*_evidence())
    assert report["eligible_for_replacement_review"]
    assert report["incumbent_retained"] and not report["eligible_for_real_nauvis"]


def test_historical_easier_conditions_and_shadow_success_cannot_qualify():
    suite, baseline, challenger = _evidence()
    easier = deepcopy(suite["cases"][0]["conditions"])
    easier["starting_inventory"]["iron-plate"] = 20000
    baseline[0]["conditions_hash"] = conditions_hash(easier)
    with pytest.raises(LayaComparisonError, match="starting conditions differ"):
        compare_matched_runs(suite, baseline, challenger)
    suite, baseline, challenger = _evidence()
    challenger[0]["execution_authority"] = "shadow"
    with pytest.raises(LayaComparisonError, match="shadow"):
        compare_matched_runs(suite, baseline, challenger)


@pytest.mark.parametrize("metric,value", [("objective_completed", False), ("safety_violations", 1), ("material_cost", 11)])
def test_one_failed_or_regressing_seed_blocks_replacement(metric, value):
    suite, baseline, challenger = _evidence()
    challenger[0]["metrics"][metric] = value
    report = compare_matched_runs(suite, baseline, challenger)
    assert report["status"] == "blocked" and report["blocking_reasons"]


def test_failed_pairs_cannot_be_dropped_or_repeated_as_new_seeds():
    suite, baseline, challenger = _evidence()
    with pytest.raises(LayaComparisonError, match="every frozen benchmark case"):
        compare_matched_runs(suite, baseline, challenger[:-1])
    suite["cases"][1]["seed"] = 0
    with pytest.raises(LayaComparisonError, match="independent held-out"):
        compare_matched_runs(suite, baseline, challenger)


def test_reused_artifacts_wrong_seed_and_wrong_runtime_are_rejected():
    for field, value, match in [
        ("artifact_hash", "sha256:" + f"{10:064x}", "independent"),
        ("seed", 999, "seed/family"),
        ("runtime", {"kind": "real_nauvis"}, "disposable runtime"),
    ]:
        suite, baseline, challenger = _evidence()
        challenger[0][field] = value
        with pytest.raises(LayaComparisonError, match=match):
            compare_matched_runs(suite, baseline, challenger)


@pytest.mark.parametrize("bad", [None, [], {"version": "1.0.0", "suite_id": "bad", "cases": "bad"}, {"version": "1.0.0", "suite_id": "bad", "cases": [1]}])
def test_malformed_suite_is_a_contract_error(bad):
    with pytest.raises(LayaComparisonError):
        compare_matched_runs(bad, [], [])
