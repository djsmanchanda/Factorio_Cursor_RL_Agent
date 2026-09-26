# Path: tests/test_laya_supply_evidence.py
# Purpose: Verify factual supply projection, reservation accounting, and recipe recursion.

import pytest

from training.laya_supply_evidence import (
    LayaSupplyEvidenceError,
    project_supply_evidence,
)


def test_belt_future_bill_projects_gear_iron_and_preserves_provenance():
    result = project_supply_evidence(
        {
            "transport-belt": {
                "stock": 3,
                "production_rate": 1.0,
                "draw_rate": 0.5,
                "capacity_rate": 1.5,
                "provenance": {"surface": "training-1", "tick": 120},
                "bottleneck": {"kind": "measured_input_starvation", "item": "iron-plate"},
            },
            "iron-gear-wheel": {"stock": 4, "production_rate": 0.7, "draw_rate": 0.2},
            "iron-plate": {"stock": 30, "production_rate": 2, "draw_rate": 1.5},
            "copper-cable": {"stock": 2, "production_rate": 2, "draw_rate": 0.2},
            "copper-plate": {"stock": 18, "production_rate": 1, "draw_rate": 0.5},
        },
        committed_bills=[{
            "bill_id": "belts-for-construction",
            "requirements": {"transport-belt": 10},
            "provenance": {"transaction": "ghost-batch-7"},
        }],
        recipes={
            "belt": {
                "output_item": "transport-belt", "output_amount": 2,
                "ingredients": {"iron-gear-wheel": 1, "copper-cable": 1},
                "provenance": {"recipe_source": "live-prototype-snapshot"},
            },
            "gear": {
                "output_item": "iron-gear-wheel", "output_amount": 1,
                "ingredients": {"iron-plate": 2},
                "provenance": "recipe-observation-44",
            },
            "cable": {
                "output_item": "copper-cable", "output_amount": 2,
                "ingredients": {"copper-plate": 1},
            },
        },
    )

    belt = result["items"]["transport-belt"]
    assert belt["demand"]["committed"]["gross"] == 10
    assert belt["demand"]["committed"]["remaining"] == 10
    assert belt["production"]["headroom_rate"] == 0.5
    assert belt["production"]["capacity_margin_over_current_demand"] == 1.0
    assert belt["bottleneck"]["evidence"]["item"] == "iron-plate"
    assert belt["provenance"]["tick"] == 120
    assert result["items"]["iron-plate"]["demand"]["recipe_projected"] == 10
    assert result["items"]["iron-plate"]["demand"]["recipe_sources"][0]["bill_ids"] == [
        "belts-for-construction"
    ]
    assert result["recipe_projection"]["ingredients"] == {
        "copper-plate": 2.5,
        "iron-gear-wheel": 5.0,
        "iron-plate": 10.0,
        "copper-cable": 5.0,
    }


def test_reserved_stock_is_allocated_and_offsets_its_committed_bill_once():
    result = project_supply_evidence(
        {"transport-belt": {"stock": 10, "production_rate": 1, "draw_rate": 0.5}},
        committed_bills=[{
            "bill_id": "belt-job",
            "requirements": {"transport-belt": 8},
            "provenance": "construction-queue",
        }],
        reservations=[{
            "bill_id": "belt-job", "item": "transport-belt", "amount": 3,
            "provenance": "inventory-reservation-2",
        }],
    )

    belt = result["items"]["transport-belt"]
    assert belt["stock"] == {
        "measured": 10.0,
        "allocated": 3.0,
        "accessible": 7.0,
        "overallocated": 0.0,
        "reservation_provenance": ["inventory-reservation-2"],
    }
    assert belt["demand"]["committed"]["gross"] == 8
    assert belt["demand"]["committed"]["reserved"] == 3
    assert belt["demand"]["committed"]["remaining"] == 5


def test_unknown_rates_remain_unknown_and_recipe_cycles_are_reported():
    result = project_supply_evidence(
        {"transport-belt": {"stock": 2, "production_rate": None, "draw_rate": None}},
        committed_bills=[{"bill_id": "cyclic", "requirements": {"transport-belt": 2}}],
        recipes={
            "belt": {
                "output_item": "transport-belt", "output_amount": 1,
                "ingredients": {"iron-gear-wheel": 1},
            },
            "gear": {
                "output_item": "iron-gear-wheel", "output_amount": 1,
                "ingredients": {"transport-belt": 1},
            },
        },
    )

    belt = result["items"]["transport-belt"]
    assert belt["demand"]["current_rate"] is None
    assert belt["production"]["observed_rate"] is None
    assert belt["production"]["headroom_rate"] is None
    assert belt["production"]["headroom_status"] == "unknown"
    assert result["recipe_projection"]["cycles"] == [{
        "cycle": ["transport-belt", "iron-gear-wheel", "transport-belt"],
        "blocked_recipe": "belt",
    }]


def test_duplicate_bill_identity_is_rejected_to_prevent_double_counting():
    with pytest.raises(LayaSupplyEvidenceError, match="duplicate committed bill id"):
        project_supply_evidence(
            {},
            committed_bills=[
                {"bill_id": "same", "requirements": {"transport-belt": 1}},
                {"bill_id": "same", "requirements": {"transport-belt": 1}},
            ],
        )


def test_reservation_cannot_exceed_its_named_bill():
    with pytest.raises(LayaSupplyEvidenceError, match="exceeds bill requirement"):
        project_supply_evidence(
            {"transport-belt": {"stock": 10}},
            committed_bills=[{"bill_id": "bridge", "requirements": {"transport-belt": 2}}],
            reservations=[{"bill_id": "bridge", "item": "transport-belt", "amount": 3}],
        )


def test_nonfinite_provenance_cannot_enter_hashed_evidence():
    with pytest.raises(LayaSupplyEvidenceError, match="finite"):
        project_supply_evidence({"iron-plate": {"stock": 2, "provenance": {"rate": float("nan")}}})
