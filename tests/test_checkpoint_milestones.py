# Path: tests/test_checkpoint_milestones.py
# Purpose: Verify structured, versioned, monotonic checkpoint predicates.
from orchestrator.checkpoint_milestones import (
    CheckpointEvaluator,
    FactorySnapshot,
    PredicateStatus,
    default_milestones,
    evaluate_milestone,
)


def _snapshot(**overrides):
    value = {
        "tick": 0,
        "base_valid": True,
        "starters": {
            "iron": {"present": True, "healthy": True, "producing": True},
            "copper": {"present": True, "healthy": True, "producing": True},
            "stone": {"present": True, "healthy": True, "producing": True},
        },
        "mall_assemblers": [
            {"recipe": "iron-gear-wheel", "working": True},
            {"recipe": "copper-cable", "working": True},
            {"recipe": "electronic-circuit", "working": True},
            {"recipe": "transport-belt", "working": True},
        ],
    }
    value.update(overrides)
    return value


def test_default_registry_is_ordered_and_versioned_with_advanced_circuit_milestone():
    milestones = default_milestones()
    assert [item.checkpoint_id for item in milestones] == ["C0", "C1", "C2", "C3", "C4", "C5", "C6"]
    assert [item.predicate_version for item in milestones] == [
        "c0-v1", "c1-v1", "c2-v1", "c3-v1", "c4-v1", "c5-v2", "c6-v1"]
    assert milestones[-1].name == "advanced_circuit_consumption"


def test_missing_telemetry_is_unknown_not_failed():
    result = evaluate_milestone("C1", {"tick": 1})
    assert result.status is PredicateStatus.UNKNOWN
    assert "iron_starter_ready" in result.missing


def test_c1_requires_four_working_mall_cells_and_required_recipes():
    result = evaluate_milestone("C1", _snapshot())
    assert result.passed
    broken = _snapshot(mall_assemblers=[{"recipe": "iron-gear-wheel", "working": True}] * 4)
    assert evaluate_milestone("C1", broken).failed


def test_c2_requires_six_machine_foundations_and_absent_starters():
    value = _snapshot(
        foundations={
            "iron": {"machine_count": 6, "healthy": True, "producing": True, "output_count": 1},
            "copper": {"machine_count": 6, "healthy": True, "producing": True, "output_count": 1},
        },
        starters={
            "iron": {"present": False}, "copper": {"present": False},
        },
    )
    assert evaluate_milestone("C2", value).passed
    value["foundations"]["iron"]["machine_count"] = 5
    assert evaluate_milestone("C2", value).failed


def test_foundation_requires_measured_output_not_only_running_machines():
    value = _snapshot(
        foundations={
            "iron": {"machine_count": 6, "healthy": True, "producing": True, "output_count": 0},
            "copper": {"machine_count": 6, "healthy": True, "producing": True, "output_count": 1},
        },
        starters={"iron": {"present": False}, "copper": {"present": False}},
    )
    assert evaluate_milestone("C2", value).failed


def test_c4_requires_new_plastic_production_and_delivery():
    baseline = {"tick": 100, "production": {"plastic-bar": {"produced": 2}}, "providers": {"plastic-bar": {"delivered": 1}}}
    current = {"tick": 200, "production": {"plastic-bar": {"produced": 3}}, "providers": {"plastic-bar": {"delivered": 2}}}
    assert evaluate_milestone("C4", current, baseline=baseline).passed
    assert evaluate_milestone("C4", current).unknown


def _startup(counts, *, start=600, baseline=100):
    return {"tick": start + (len(counts) - 1) * 1800, "plastic_startup": {
        "version": "production-startup-v1", "item": "plastic-bar", "run_id": "test",
        "source": "force.get_item_production_statistics(surface).get_input_count",
        "baseline_tick": 0, "baseline_count": baseline, "start_tick": start,
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
    assert evaluate_milestone("C5", snapshot).unknown


def test_c6_proves_advanced_circuit_production_and_new_owned_consumer_output():
    baseline = {
        "production": {"advanced-circuit": {"produced": 1}},
        "recipes": {"processing-unit": {"owned": True, "ingredients": ["advanced-circuit"], "output_count": 0}},
    }
    current = {
        "production": {"advanced-circuit": {"produced": 2}},
        "recipes": {"processing-unit": {"owned": True, "ingredients": ["advanced-circuit"], "output_count": 1}},
    }
    assert evaluate_milestone("C6", current, baseline=baseline).passed


def test_evaluator_keeps_passed_checkpoint_monotonic():
    evaluator = CheckpointEvaluator()
    assert evaluator.evaluate("C0", _snapshot()).passed
    later = evaluator.evaluate("C0", {"tick": 4})
    assert later.passed
    assert later.evidence["monotonic_reached"] is True


def test_c1_evidence_counts_mall_statuses_and_reads_idle_producers_as_failed():
    idle = [{"recipe": "iron-gear-wheel", "working": False, "status": "output_full"},
            {"recipe": "copper-cable", "working": False, "status": "no_ingredients"},
            {"recipe": "electronic-circuit", "working": True, "status": "working"},
            {"recipe": "transport-belt", "working": False}]
    starters = {"iron": {"present": True, "healthy": True, "producing": True},
                "copper": {"present": True, "healthy": True, "producing": False},
                "stone": {"present": False}}
    result = evaluate_milestone("C1", _snapshot(mall_assemblers=idle, starters=starters))
    assert result.failed
    assert result.evidence["mall_status_counts"] == {"no_ingredients": 1, "output_full": 1, "unreported": 1, "working": 1}
    assert result.evidence["recipes_present"] == {"iron-gear-wheel": True, "copper-cable": True}
    assert result.evidence["checks"]["iron_gear_producer_working"] is False
    assert result.evidence["starter_blockers"] == {"iron": "ready", "copper": "not_producing", "stone": "absent"}


def test_c1_idle_survey_now_reads_producers_false_without_changing_status():
    # c1-v1 change: an observed survey with no working machine used to report the producer checks unknown;
    # they are now measured False. The count check already failed it, so the status is unchanged.
    idle = [{"recipe": "iron-gear-wheel", "working": False, "status": "no_power"}] * 4
    result = evaluate_milestone("C1", _snapshot(mall_assemblers=idle))
    assert result.failed and result.evidence["checks"]["copper_cable_producer_working"] is False
    empty = evaluate_milestone("C1", _snapshot(mall_assemblers=[]))
    assert empty.unknown and empty.evidence["checks"]["iron_gear_producer_working"] is None
    assert empty.evidence["mall_status_counts"] == {}
