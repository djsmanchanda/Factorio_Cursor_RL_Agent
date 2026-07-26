# Path: tests/test_land_value.py
# Purpose: Pin intrinsic land values, stage placement priorities, late-game gating, and shadow-only outward relocation.

from __future__ import annotations

import pytest

from planners.land_value import (
    BLOCK_CATEGORIES,
    LATE_GAME_SCIENCE_RATE_PER_TICK,
    MIN_RELOCATION_WINDOW_TICKS,
    DevelopmentMetrics,
    LandValuePolicy,
    RelocationVerdict,
    StageKind,
    evaluate_relocation,
)

POLICY = LandValuePolicy(base_center=(0.0, 0.0))
REQUIRED_SCIENCES = (
    "automation-science-pack",
    "logistic-science-pack",
    "chemical-science-pack",
    "production-science-pack",
)


def _mature_development() -> DevelopmentMetrics:
    return DevelopmentMetrics(
        required_sciences=REQUIRED_SCIENCES,
        science_rates_per_tick={
            science: LATE_GAME_SCIENCE_RATE_PER_TICK for science in REQUIRED_SCIENCES
        },
        measurement_window_ticks=MIN_RELOCATION_WINDOW_TICKS,
    )


def test_intrinsic_value_is_deterministic_and_falls_by_manhattan_block_distance():
    first = POLICY.intrinsic_value((64.0, 64.0))
    again = POLICY.intrinsic_value((64.0, 64.0))

    assert first == again == pytest.approx(0.5)
    assert POLICY.intrinsic_value((0.0, 0.0)) == 1.0
    assert POLICY.intrinsic_value((256.0, 0.0)) == 0.0
    assert POLICY.intrinsic_value((1_000.0, 1_000.0)) == 0.0


def test_placement_penalties_order_tasks_from_peripheral_to_central():
    at_center = (0.0, 0.0)

    penalties = {kind: POLICY.placement_penalty(kind, at_center) for kind in StageKind}

    assert penalties[StageKind.MINING] > penalties[StageKind.SMELTING]
    assert penalties[StageKind.SMELTING] > penalties[StageKind.CONVERSION]
    assert penalties[StageKind.CONVERSION] > penalties[StageKind.SCIENCE]
    assert penalties[StageKind.SCIENCE] > penalties[StageKind.SPACEPORT]
    assert penalties[StageKind.SPACEPORT] > penalties[StageKind.RESEARCH]


def test_block_categories_preserve_each_stage_role():
    assert BLOCK_CATEGORIES == {
        StageKind.MINING: "mining",
        StageKind.SMELTING: "smelting",
        StageKind.CONVERSION: "production",
        StageKind.SCIENCE: "science",
        StageKind.RESEARCH: "science",
        StageKind.SPACEPORT: "infrastructure",
    }


def test_large_smelting_has_stronger_outward_pressure_than_small_smelting():
    central = (0.0, 0.0)
    outskirts = (256.0, 0.0)

    assert POLICY.placement_penalty(
        StageKind.SMELTING, central, machine_count=20
    ) > POLICY.placement_penalty(StageKind.SMELTING, central, machine_count=19)
    assert (
        POLICY.placement_penalty(StageKind.SMELTING, outskirts, machine_count=20) == 0.0
    )


@pytest.mark.parametrize(
    "required,rates,window",
    [
        ((), {}, MIN_RELOCATION_WINDOW_TICKS),
        (REQUIRED_SCIENCES, {}, MIN_RELOCATION_WINDOW_TICKS),
        (
            REQUIRED_SCIENCES,
            {
                science: LATE_GAME_SCIENCE_RATE_PER_TICK
                for science in REQUIRED_SCIENCES[:-1]
            },
            MIN_RELOCATION_WINDOW_TICKS,
        ),
        (
            REQUIRED_SCIENCES,
            {science: LATE_GAME_SCIENCE_RATE_PER_TICK for science in REQUIRED_SCIENCES},
            MIN_RELOCATION_WINDOW_TICKS - 1,
        ),
        (
            REQUIRED_SCIENCES,
            {
                **{
                    science: LATE_GAME_SCIENCE_RATE_PER_TICK
                    for science in REQUIRED_SCIENCES
                },
                REQUIRED_SCIENCES[-1]: LATE_GAME_SCIENCE_RATE_PER_TICK - 0.001,
            },
            MIN_RELOCATION_WINDOW_TICKS,
        ),
    ],
    ids=[
        "empty-required-set",
        "all-rates-missing",
        "one-rate-missing",
        "window-too-short",
        "one-rate-too-low",
    ],
)
def test_relocation_gate_fails_closed(required, rates, window):
    development = DevelopmentMetrics(
        required_sciences=required,
        science_rates_per_tick=rates,
        measurement_window_ticks=window,
    )

    assert development.relocation_ready is False


def test_relocation_gate_accepts_exact_rate_and_window_boundaries():
    assert _mature_development().relocation_ready is True


@pytest.mark.parametrize(
    "kind,machine_count",
    [
        (StageKind.MINING, 8),
        (StageKind.SMELTING, 20),
    ],
)
def test_mature_central_heavy_industry_builds_shadow_outward(kind, machine_count):
    decision = evaluate_relocation(
        kind,
        current_point=(0.0, 0.0),
        machine_count=machine_count,
        policy=POLICY,
        development=_mature_development(),
        higher_value_waiting=True,
        outer_site_available=True,
    )

    assert decision.verdict is RelocationVerdict.BUILD_SHADOW_OUTWARD
    assert decision.target_min_ring is not None
    assert decision.target_min_ring >= POLICY.central_radius_cells
    assert "build and validate a shadow" in decision.reason
    assert "before retiring" in decision.reason


@pytest.mark.parametrize(
    "kind,machine_count,point",
    [
        (StageKind.SMELTING, 19, (0.0, 0.0)),
        (StageKind.CONVERSION, 30, (0.0, 0.0)),
        (StageKind.SCIENCE, 30, (0.0, 0.0)),
        (StageKind.RESEARCH, 30, (0.0, 0.0)),
        (StageKind.SPACEPORT, 30, (0.0, 0.0)),
        (StageKind.MINING, 8, (256.0, 0.0)),
    ],
)
def test_ineligible_or_already_outward_stages_stay_in_place(kind, machine_count, point):
    decision = evaluate_relocation(
        kind,
        current_point=point,
        machine_count=machine_count,
        policy=POLICY,
        development=_mature_development(),
        higher_value_waiting=True,
        outer_site_available=True,
    )

    assert decision.verdict is RelocationVerdict.KEEP_IN_PLACE
    assert decision.target_min_ring is None


def test_immature_base_never_moves_even_central_mining():
    development = DevelopmentMetrics(
        required_sciences=REQUIRED_SCIENCES,
        science_rates_per_tick={
            science: LATE_GAME_SCIENCE_RATE_PER_TICK for science in REQUIRED_SCIENCES
        },
        measurement_window_ticks=MIN_RELOCATION_WINDOW_TICKS - 1,
    )

    decision = evaluate_relocation(
        StageKind.MINING,
        current_point=(0.0, 0.0),
        machine_count=8,
        policy=POLICY,
        development=development,
        higher_value_waiting=True,
        outer_site_available=True,
    )

    assert decision.verdict is RelocationVerdict.KEEP_IN_PLACE
    assert decision.target_min_ring is None


@pytest.mark.parametrize(
    ("higher_value_waiting", "outer_site_available", "expected_reason"),
    [
        (False, True, "no specific higher-value"),
        (True, False, "no legal outward shadow site"),
    ],
)
def test_relocation_requires_demand_and_a_legal_shadow_site(
    higher_value_waiting,
    outer_site_available,
    expected_reason,
):
    decision = evaluate_relocation(
        StageKind.MINING,
        current_point=(0.0, 0.0),
        machine_count=8,
        policy=POLICY,
        development=_mature_development(),
        higher_value_waiting=higher_value_waiting,
        outer_site_available=outer_site_available,
    )

    assert decision.verdict is RelocationVerdict.KEEP_IN_PLACE
    assert expected_reason in decision.reason


@pytest.mark.parametrize("bad_window", [True, 1.5, -1])
def test_measurement_window_requires_non_negative_integer_ticks(bad_window):
    with pytest.raises(ValueError, match="measurement_window_ticks"):
        DevelopmentMetrics(
            required_sciences=REQUIRED_SCIENCES,
            science_rates_per_tick={},
            measurement_window_ticks=bad_window,
        )
