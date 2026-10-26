# Path: tests/test_plastic_startup_milestone.py | Purpose: Preserve the user-directed C5-v2 startup-rate contract.
from orchestrator.checkpoint_milestones import evaluate_milestone


def _startup(counts, *, start=600, baseline=0):
    return {"tick": start + (len(counts) - 1) * 1800, "plastic_startup": {
        "version": "production-startup-v2", "item": "plastic-bar", "run_id": "test",
        "source": "force.get_item_production_statistics(surface).get_input_count",
        "armed_at_episode_start": True, "baseline_tick": 0, "baseline_count": baseline, "start_tick": start,
        "observed_tick": start + (len(counts) - 1) * 1800,
        "samples": [{"tick": start + i * 1800, "produced": count} for i, count in enumerate(counts)],
    }}


def test_c5_stability_can_arrive_early_and_survives_full_output_or_diversion():
    # First two intervals establish 10/s; later stock gating / gas diversion
    # must not retract the startup proof, even in a fresh final evaluation.
    for counts in ([102, 402, 702], [102, 402, 702, 702, 702]):
        result = evaluate_milestone("C5", _startup(counts))
        assert result.passed
        assert result.predicate_version == "c5-v2"
        assert result.evidence["qualified_tick"] == 4200


def test_c5_accepts_ramp_then_stability_within_first_two_minutes():
    result = evaluate_milestone("C5", _startup([102, 132, 432, 732, 732]))
    assert result.passed
    assert result.evidence["qualified_tick"] == 6000


def test_c5_stable_rate_tolerance_and_positive_output():
    assert evaluate_milestone("C5", _startup([102, 402, 642])).passed  # 20% lower
    assert evaluate_milestone("C5", _startup([102, 402, 641, 641, 641])).failed
    assert evaluate_milestone("C5", _startup([102, 102, 102, 102, 102])).failed
    assert evaluate_milestone("C5", _startup([102, 402, 402, 702, 702])).failed


def test_c5_waits_for_evidence_without_fabricating_rates_from_stock():
    assert evaluate_milestone("C5", _startup([102, 402])).unknown
    assert evaluate_milestone("C5", {"tick": 9000, "providers": {"plastic-bar": 4800}}).unknown
    assert evaluate_milestone("C5", {"tick": 9000, "production": {"plastic-bar": 4800}},
                              history=[{"tick": 0, "production": {"plastic-bar": 1}}]).unknown


def test_c5_rejects_counter_reset_gaps_and_extra_late_samples():
    for counts in ([102, 402, 2], [102, 402, float("nan")], [102, 402, 702, 1002, 1302, 1602]):
        assert evaluate_milestone("C5", _startup(counts)).unknown
    snapshot = _startup([102, 402, 702])
    snapshot["plastic_startup"]["samples"][1]["tick"] += 60
    assert evaluate_milestone("C5", snapshot).unknown
    snapshot = _startup([102, 402, 702])
    snapshot["plastic_startup"]["error"] = "production_counter_reset"
    assert evaluate_milestone("C5", snapshot).passed
    assert evaluate_milestone("C5", _startup([502, 802, 1102], baseline=500)).passed
    late = _startup([502, 802, 1102], baseline=500)
    del late["plastic_startup"]["armed_at_episode_start"]
    assert evaluate_milestone("C5", late).unknown
    snapshot = _startup([102, 402])
    snapshot["plastic_startup"]["error"] = "production_counter_reset"
    assert evaluate_milestone("C5", snapshot).unknown


def test_c5_v1_remains_a_separate_comparable_predicate():
    from orchestrator.checkpoint_milestones import C5_V1, comparable_milestones
    assert comparable_milestones("C5", "c5-v2") == (C5_V1,)
    assert evaluate_milestone("C5", {"tick": 9000, "production": {"plastic-bar": 4800}},
                              milestones=(C5_V1,)).predicate_version == "c5-v1"



def test_c5_rejects_start_before_baseline_and_future_sample():
    for change in ("baseline", "future"):
        snapshot = _startup([102, 402, 702])
        if change == "baseline":
            snapshot["plastic_startup"]["baseline_tick"] = snapshot["plastic_startup"]["start_tick"]
        else:
            snapshot["plastic_startup"]["observed_tick"] -= 1
        assert evaluate_milestone("C5", snapshot).unknown



def test_c5_requires_a_named_logical_episode():
    for run_id in (None, "", 123):
        snapshot = _startup([102, 402, 702])
        snapshot["plastic_startup"]["run_id"] = run_id
        assert evaluate_milestone("C5", snapshot).unknown
