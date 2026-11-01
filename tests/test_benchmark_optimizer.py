# Path: tests/test_benchmark_optimizer.py | Purpose: Verify the offline bounded-optimizer benchmark, provenance, budgets, and measured fields.

from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path

import pytest

from tools import benchmark_optimizer as benchmark

REAL_CATALOG = (
    Path(__file__).resolve().parents[1]
    / "data/factorio-2.1.21/probes/deterministic/server/script-output/factorio_mod"
    / "recipe_catalogs/recipe_catalog_243688.json"
)


def _cases(report: dict) -> dict[str, dict]:
    return {case["case_id"]: case for case in report["cases"]}


@pytest.mark.skipif(not REAL_CATALOG.is_file(), reason="historical 2.1.21 catalog is unavailable")
def test_historical_fixture_provenance_is_hash_bound():
    fixture = benchmark.load_catalog_fixture()

    assert fixture.available is True
    assert fixture.error is None
    assert fixture.sha256 == hashlib.sha256(REAL_CATALOG.read_bytes()).hexdigest()
    assert fixture.size_bytes == REAL_CATALOG.stat().st_size
    assert fixture.catalog_schema_version == "1.2.0"
    assert fixture.force == "player"
    assert fixture.tick == 243688
    assert fixture.recipe_count == 651
    assert fixture.raw_resource_count == 23
    assert "2.1.21" in fixture.game_version_context

    metadata = benchmark.fixture_metadata(fixture)
    assert metadata["sha256"] == fixture.sha256
    assert metadata["available"] is True
    assert metadata["path"].endswith("recipe_catalog_243688.json")


@pytest.mark.skipif(not REAL_CATALOG.is_file(), reason="historical 2.1.21 catalog is unavailable")
def test_historical_oil_cracking_models_report_exact_status_and_rates():
    report = benchmark.build_benchmark(repeats=1, include_synthetic=False)
    cases = _cases(report)
    fixture_sha = report["fixture"]["sha256"]

    zero = cases["oil-cracking-advanced-budget-0"]
    assert zero["catalog"]["sha256"] == fixture_sha
    assert zero["result"]["status"] == "budget_exhausted"
    assert zero["result"]["optimal"] is False
    assert zero["result"]["budget_exhausted_with_incumbent"] is False
    assert zero["result"]["candidates_evaluated"] == 0
    assert zero["result"]["recipe_rates"] is None
    assert zero["result"]["optimality_truth"] == (
        "budget_exhausted_no_incumbent_not_infeasible_not_optimal"
    )

    advanced = cases["oil-cracking-advanced-budget-1"]
    assert advanced["catalog"]["sha256"] == fixture_sha
    assert advanced["result"]["status"] == "optimal"
    assert advanced["result"]["optimal"] is True
    assert advanced["result"]["candidates_evaluated"] == 1
    assert advanced["result"]["candidate_budget"] == 1
    assert advanced["result"]["objective_value"] == "-915/2"
    assert advanced["result"]["recipe_rates"] == {
        "advanced-oil-processing": "2/1",
        "heavy-oil-cracking": "5/4",
        "light-oil-cracking": "17/4",
    }
    assert advanced["result"]["source_rates"] == {
        "crude-oil": "200/1",
        "water": "265/1",
    }
    assert advanced["result"]["optimality_truth"] == (
        "optimal_by_complete_candidate_enumeration"
    )

    partial = cases["oil-cracking-alternatives-budget-6"]
    assert partial["result"]["status"] == "budget_exhausted"
    assert partial["result"]["optimal"] is False
    assert partial["result"]["budget_exhausted"] is True
    assert partial["result"]["budget_exhausted_with_incumbent"] is True
    assert partial["result"]["candidates_evaluated"] == 6
    assert partial["result"]["optimality_truth"] == (
        "budget_exhausted_feasible_incumbent_not_optimal"
    )

    complete = cases["oil-cracking-alternatives-budget-12"]
    assert complete["result"]["status"] == "optimal"
    assert complete["result"]["optimal"] is True
    assert complete["result"]["candidates_evaluated"] == 12
    assert complete["result"]["objective_value"] == "-429/1"
    assert complete["result"]["recipe_rates"] == {
        "advanced-oil-processing": "0/1",
        "basic-oil-processing": "13/3",
        "heavy-oil-cracking": "0/1",
        "light-oil-cracking": "0/1",
    }
    assert complete["result"]["source_rates"] == {
        "crude-oil": "1300/3",
        "water": "0/1",
    }


def test_synthetic_increasing_size_models_have_deterministic_candidate_counts():
    report = benchmark.build_benchmark(
        repeats=1,
        include_fixture=False,
        synthetic_sizes=(1, 2, 3, 4),
        synthetic_budgets=(10, 1000),
    )
    cases = _cases(report)

    for size in (1, 2, 3, 4):
        variables = 2 * size + 1
        material_rows = size + 1
        expected = math.comb(2 * variables, variables - material_rows)
        low = cases[f"synthetic-choice-size-{size}-budget-10"]
        high = cases[f"synthetic-choice-size-{size}-budget-1000"]

        for case in (low, high):
            assert case["catalog"]["origin"] == "synthetic_inline"
            assert case["catalog"]["sha256"] is None
            assert case["model"] == {
                "variables": variables,
                "recipe_decisions": 2 * size,
                "source_decisions": 1,
                "disposal_decisions": 0,
                "material_rows": material_rows,
                "expected_vertex_candidates": expected,
                "expected_candidate_basis": case["model"]["expected_candidate_basis"],
            }
            assert "comb(2 * variables" in case["model"]["expected_candidate_basis"]

        if size == 1:
            assert low["result"]["status"] == "optimal"
            assert low["result"]["candidates_evaluated"] == expected
        else:
            assert low["result"]["status"] == "budget_exhausted"
            assert low["result"]["optimal"] is False
            assert low["result"]["candidates_evaluated"] == 10

        if size < 4:
            assert high["result"]["status"] == "optimal"
            assert high["result"]["candidates_evaluated"] == expected
            assert high["result"]["optimal"] is True
        else:
            assert high["result"]["status"] == "budget_exhausted"
            assert high["result"]["optimal"] is False
            assert high["result"]["candidates_evaluated"] == 1000
            assert high["result"]["optimality_truth"] in {
                "budget_exhausted_no_incumbent_not_infeasible_not_optimal",
                "budget_exhausted_feasible_incumbent_not_optimal",
            }


def test_budget_exhaustion_is_never_reported_as_optimality():
    report = benchmark.build_benchmark(
        repeats=1,
        include_fixture=False,
        synthetic_sizes=(2, 4),
        synthetic_budgets=(10,),
    )

    for case in report["cases"]:
        result = case["result"]
        assert result["status"] == "budget_exhausted"
        assert result["optimal"] is False
        assert result["optimality_claimed"] is False
        assert result["budget_exhausted"] is True
        assert result["optimality_truth"].startswith("budget_exhausted_")
        assert "neither infeasibility nor optimality is claimed" in result["api_reason"] or (
            "optimality is not claimed" in result["api_reason"]
        )


def test_first_valid_time_is_unavailable_and_memory_is_traced():
    report = benchmark.build_benchmark(
        repeats=2,
        include_fixture=False,
        synthetic_sizes=(1,),
        synthetic_budgets=(10,),
    )
    case = report["cases"][0]
    timing = case["timing"]
    memory = case["memory"]

    assert timing["time_to_first_valid_seconds"] is None
    assert timing["time_to_first_valid_available"] is False
    assert "no first-valid-incumbent callback" in (
        timing["time_to_first_valid_unavailable_reason"]
    )
    assert timing["timing_repeats"] == 2
    assert len(timing["total_seconds_samples"]) == 2
    assert all(isinstance(sample, float) and sample >= 0 for sample in timing["total_seconds_samples"])
    assert timing["total_seconds_median"] == statistics.median(
        timing["total_seconds_samples"]
    )
    assert timing["total_seconds_min"] <= timing["total_seconds_median"]
    assert timing["total_seconds_median"] <= timing["total_seconds_max"]

    assert isinstance(memory["peak_python_alloc_bytes"], int)
    assert memory["peak_python_alloc_bytes"] > 0
    assert memory["peak_python_alloc_mib"] == (
        memory["peak_python_alloc_bytes"] / (1024 * 1024)
    )
    assert memory["tracemalloc_domain"] == "python"
    assert "tracemalloc" in memory["metric"]


def test_results_and_candidate_counts_repeat_exactly():
    first = benchmark.build_benchmark(
        repeats=1,
        include_fixture=False,
        synthetic_sizes=(1, 2, 3),
        synthetic_budgets=(10, 1000),
    )
    second = benchmark.build_benchmark(
        repeats=1,
        include_fixture=False,
        synthetic_sizes=(1, 2, 3),
        synthetic_budgets=(10, 1000),
    )

    def stable_results(report: dict) -> list[tuple]:
        return [
            (
                case["case_id"],
                case["result"]["status"],
                case["result"]["optimal"],
                case["result"]["candidates_evaluated"],
                case["result"]["objective_value"],
                case["result"]["recipe_rates"],
                case["result"]["source_rates"],
            )
            for case in report["cases"]
        ]

    assert stable_results(first) == stable_results(second)


def test_missing_fixture_skips_historical_cases_without_fabrication(tmp_path):
    missing = tmp_path / "missing-catalog.json"
    report = benchmark.build_benchmark(
        fixture_path=missing,
        repeats=1,
        include_fixture=True,
        include_synthetic=True,
        synthetic_sizes=(1,),
        synthetic_budgets=(10,),
    )

    assert report["fixture"]["available"] is False
    assert report["fixture"]["sha256"] is None
    assert report["fixture"]["error"] == "fixture file not found"
    assert report["skipped_families"] == [
        {"family": "historical_oil_cracking", "reason": "fixture file not found"}
    ]
    assert [case["case_id"] for case in report["cases"]] == [
        "synthetic-choice-size-1-budget-10"
    ]
    assert report["cases"][0]["catalog"]["origin"] == "synthetic_inline"


def test_cli_writes_a_json_report(tmp_path):
    output = tmp_path / "benchmark.json"
    status = benchmark.main(
        [
            "--no-fixture",
            "--sizes",
            "1",
            "--budgets",
            "10",
            "--repeat",
            "1",
            "--output",
            str(output),
        ]
    )

    assert status == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["tool"] == "benchmark_optimizer"
    assert report["format_version"] == 1
    assert report["requested"]["include_fixture"] is False
    assert len(report["cases"]) == 1
    assert report["cases"][0]["result"]["status"] == "optimal"
