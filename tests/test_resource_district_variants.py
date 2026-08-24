# Path: tests/test_resource_district_variants.py
# Purpose: Verify cumulative offline comparisons for cohesive mine/refinery variants.

from __future__ import annotations

import json

import pytest

from tools.evaluate_resource_district_variants import (
    VARIANTS,
    EvaluationReport,
    evaluate_all,
)


@pytest.fixture(scope="module")
def report() -> EvaluationReport:
    return evaluate_all()


def _result(report: EvaluationReport, scenario: str, variant: str):
    return next(
        result for result in report.results
        if result.scenario == scenario and result.variant == variant
    )


def test_all_cumulative_scenarios_and_variants_are_reported(report: EvaluationReport) -> None:
    scenarios = {result.scenario for result in report.results}
    assert scenarios == {
        "clear",
        "long_pipeline_crossing",
        "foreign_infrastructure",
        "obstacle_beyond_all_tiers",
        "obstacle_within_tier_reach",
        "blocked_endpoint",
        "ghosts_pending_partial_phase",
        "conflicting_future_reservation",
        "retry_recovery",
    }
    assert len(report.results) == len(scenarios) * len(VARIANTS)
    assert all(
        {result.variant for result in report.results if result.scenario == scenario}
        == set(VARIANTS)
        for scenario in scenarios
    )


@pytest.mark.parametrize("variant", VARIANTS)
def test_clear_growth_keeps_one_district_and_refinery_with_capacity_valid_routes(
    report: EvaluationReport, variant: str,
) -> None:
    result = _result(report, "clear", variant)
    assert result.outcome == "build"
    assert result.usable_drills == 50
    assert result.district_count == 1
    assert result.refinery_sites == 1
    assert result.declared_connectivity is True
    assert result.capacity_valid is True
    assert result.collision_count == 0
    assert result.raw_t_merges == 0
    assert result.submitted_actions == result.planned_actions > 0
    assert result.throughput_headroom >= 0


@pytest.mark.parametrize("variant", VARIANTS)
def test_comparison_does_not_overstate_unemitted_physical_continuity(
    report: EvaluationReport, variant: str,
) -> None:
    result = _result(report, "clear", variant)
    assert result.physical_continuity_verified is False
    assert result.physical_continuity_reason
    if variant == "C":
        assert result.geometry_source == "fixture_projection"
        assert result.physical_continuity_reason == "variant_c_fixture_projection"
    else:
        assert result.geometry_source == "production_phase_plan"
        assert result.physical_continuity_reason == (
            "collector_to_manifold_geometry_not_emitted"
        )


def test_variant_a_is_preferred_and_b_preserves_separate_capacity_bounded_lanes(
    report: EvaluationReport,
) -> None:
    merged = _result(report, "clear", "A")
    separate = _result(report, "clear", "B")
    comb = _result(report, "clear", "C")
    assert merged.mine_outputs == 1
    assert separate.mine_outputs > 1
    assert separate.capacity_valid
    assert comb.splitters > merged.splitters
    assert comb.additional_belt_entities > merged.additional_belt_entities
    assert comb.occupied_area > merged.occupied_area


@pytest.mark.parametrize("variant", VARIANTS)
def test_blocked_layouts_refuse_atomically(report: EvaluationReport, variant: str) -> None:
    for scenario in (
        "long_pipeline_crossing",
        "blocked_endpoint",
        "conflicting_future_reservation",
    ):
        result = _result(report, scenario, variant)
        assert result.outcome == "refuse"
        assert result.refusal_reason
        assert result.submitted_actions == 0


@pytest.mark.parametrize("variant", VARIANTS)
def test_route_obstacles_never_exceed_live_tier_reach(
    report: EvaluationReport, variant: str,
) -> None:
    within = _result(report, "obstacle_within_tier_reach", variant)
    beyond = _result(report, "obstacle_beyond_all_tiers", variant)
    assert within.outcome == "build"
    assert within.underground_pairs >= 1
    assert 0 < within.maximum_span_reach_ratio <= 1
    assert beyond.outcome == "build"
    assert beyond.maximum_span_reach_ratio <= 1
    assert beyond.route_excess > 0


@pytest.mark.parametrize("variant", VARIANTS)
def test_foreign_infrastructure_is_never_reused_or_crossed(
    report: EvaluationReport, variant: str,
) -> None:
    result = _result(report, "foreign_infrastructure", variant)
    assert result.outcome == "build"
    assert result.reused_foreign_tiles == 0
    assert result.collision_count == 0
    assert result.route_excess > 0
    if variant == "A":
        assert result.reused_owned_tiles == 2


@pytest.mark.parametrize("variant", VARIANTS)
def test_pending_ghost_and_retry_recovery_are_addition_only_and_deterministic(
    report: EvaluationReport, variant: str,
) -> None:
    pending = _result(report, "ghosts_pending_partial_phase", variant)
    retry = _result(report, "retry_recovery", variant)
    clear = _result(report, "clear", variant)
    assert pending.outcome == retry.outcome == "build"
    assert pending.usable_drills == retry.usable_drills == 50
    assert pending.district_count == retry.district_count == 1
    assert pending.refinery_sites == retry.refinery_sites == 1
    assert pending.additional_belt_entities == clear.additional_belt_entities
    assert retry.additional_belt_entities == clear.additional_belt_entities


def test_identical_inputs_produce_byte_stable_metrics(report: EvaluationReport) -> None:
    repeated = evaluate_all()
    assert repeated == report
    assert repeated.to_json() == report.to_json()
    assert json.loads(report.to_json())["results"]
    assert all(result.deterministic_key for result in report.results)
