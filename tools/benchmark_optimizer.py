# Path: tools/benchmark_optimizer.py | Purpose: Reproduce offline bounded material-balance benchmarks and emit JSON evidence.

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import statistics
import sys
import time
import tracemalloc
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.bounded_recipe_balance import (  # noqa: E402
    BoundedRecipeBalanceResult,
    optimize_bounded_recipe_balance,
)

HISTORICAL_FIXTURE = (
    REPO_ROOT
    / "data/factorio-2.1.21/probes/deterministic/server/script-output/factorio_mod"
    / "recipe_catalogs/recipe_catalog_243688.json"
)

METHODOLOGY = {
    "arithmetic": (
        "core.bounded_linear uses exact fractions.Fraction arithmetic; no floating point "
        "is used inside the bounded solve."
    ),
    "timing": (
        "time.perf_counter wraps each complete solver call. The full call includes catalog "
        "validation, model construction, vertex enumeration, and result decoding. Timed "
        "samples are repeated and reported as samples, median, minimum, and maximum. No "
        "warm-up samples are discarded."
    ),
    "memory": (
        "A separate identical solver call runs under tracemalloc. The reported value is the "
        "peak traced Python allocation for that call, after resetting the peak. Its result "
        "must match the timed result exactly. It excludes the interpreter baseline, fixture "
        "loading, and native allocations outside Python's allocator."
    ),
    "first_valid": (
        "The public solver returns only a final result. It has no first-valid-incumbent "
        "callback, timestamp, or partial-result stream, so time-to-first-valid is reported "
        "as unavailable rather than inferred."
    ),
    "candidate_budget": (
        "max_candidate_solves is passed through unchanged. A budget_exhausted status is "
        "neither a proof of optimality nor a proof of infeasibility."
    ),
    "determinism": (
        "For fixed model bytes, arms, and candidate budget, status, candidate counts, "
        "objective value, and rates are deterministic. Wall times and peak memory remain "
        "host- and environment-dependent."
    ),
    "scope": "Offline Python process only; no game, RCON, live runner, network, or deployment.",
}

NON_CLAIMS = (
    "Exact material-balance results only; no machine, power, transport, geometry, construction, or runtime feasibility claim.",
    "Timing and tracemalloc values are this host's Python measurements, not Factorio runtime performance.",
    "The historical 2.1.21 catalog is an exported capture, not evidence of the selected live save.",
    "Budget-exhausted incumbents are feasible for the explicit model but are not proven optimal.",
    "Time to first valid incumbent is not exposed by the current solver API and is reported unavailable.",
)


@dataclass(frozen=True)
class CatalogFixture:
    """Parsed historical catalog plus byte-level provenance."""

    path: Path
    available: bool
    sha256: str | None
    size_bytes: int
    catalog: Mapping[str, Any] | None
    catalog_schema_version: str | None
    force: str | None
    tick: int | None
    recipe_count: int | None
    raw_resource_count: int | None
    game_version_context: str
    error: str | None


@dataclass(frozen=True)
class CaseSpec:
    """One fully explicit bounded model and its deterministic candidate budget."""

    case_id: str
    family: str
    description: str
    catalog: Mapping[str, Any]
    recipes: Mapping[str, int]
    sources: Mapping[str, int]
    demand: Mapping[str, int]
    disposals: Mapping[str, int]
    objective: Mapping[str, Any]
    candidate_budget: int
    catalog_provenance: Mapping[str, Any]
    expected_vertex_candidates: int | None = None
    expected_candidate_basis: str | None = None


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _game_version_context(path: Path) -> str:
    for part in path.parts:
        match = re.fullmatch(r"factorio-(\d+\.\d+\.\d+)", part)
        if match:
            return (
                f"{match.group(1)} inferred from fixture directory {part!r}; "
                "the catalog JSON has no game-version field"
            )
    return "not encoded in the catalog JSON or fixture path"


def load_catalog_fixture(path: str | Path = HISTORICAL_FIXTURE) -> CatalogFixture:
    """Load the historical catalog and record exact bytes; never fabricate a fixture."""

    fixture_path = Path(path)
    context = _game_version_context(fixture_path)
    if not fixture_path.is_file():
        return CatalogFixture(
            path=fixture_path,
            available=False,
            sha256=None,
            size_bytes=0,
            catalog=None,
            catalog_schema_version=None,
            force=None,
            tick=None,
            recipe_count=None,
            raw_resource_count=None,
            game_version_context=context,
            error="fixture file not found",
        )

    raw = fixture_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        catalog = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return CatalogFixture(
            path=fixture_path,
            available=False,
            sha256=digest,
            size_bytes=len(raw),
            catalog=None,
            catalog_schema_version=None,
            force=None,
            tick=None,
            recipe_count=None,
            raw_resource_count=None,
            game_version_context=context,
            error=f"fixture JSON is invalid: {exc}",
        )

    if not isinstance(catalog, Mapping) or not isinstance(catalog.get("recipes"), list):
        return CatalogFixture(
            path=fixture_path,
            available=False,
            sha256=digest,
            size_bytes=len(raw),
            catalog=None,
            catalog_schema_version=None,
            force=None,
            tick=None,
            recipe_count=None,
            raw_resource_count=None,
            game_version_context=context,
            error="fixture must be an object with a recipes list",
        )

    tick = catalog.get("tick")
    force = catalog.get("force")
    schema_version = catalog.get("version")
    raw_resources = catalog.get("raw_resources")
    return CatalogFixture(
        path=fixture_path,
        available=True,
        sha256=digest,
        size_bytes=len(raw),
        catalog=catalog,
        catalog_schema_version=schema_version if isinstance(schema_version, str) else None,
        force=force if isinstance(force, str) else None,
        tick=tick if isinstance(tick, int) and not isinstance(tick, bool) else None,
        recipe_count=len(catalog["recipes"]),
        raw_resource_count=len(raw_resources) if isinstance(raw_resources, list) else None,
        game_version_context=context,
        error=None,
    )


def fixture_metadata(fixture: CatalogFixture) -> dict[str, Any]:
    """Return serializable provenance without embedding the catalog body."""

    return {
        "path": _display_path(fixture.path),
        "available": fixture.available,
        "sha256": fixture.sha256,
        "size_bytes": fixture.size_bytes,
        "catalog_schema_version": fixture.catalog_schema_version,
        "force": fixture.force,
        "tick": fixture.tick,
        "recipe_count": fixture.recipe_count,
        "raw_resource_count": fixture.raw_resource_count,
        "game_version_context": fixture.game_version_context,
        "error": fixture.error,
        "note": (
            "Historical exported catalog capture. It is not evidence of the selected live "
            "save, current mod set, or runtime behavior."
        ),
    }


def _objective(
    recipes: Mapping[str, int],
    sources: Mapping[str, int],
    disposals: Mapping[str, int] | None = None,
) -> dict[str, dict[str, int]]:
    return {
        "recipes": dict(recipes),
        "sources": dict(sources),
        "disposals": {} if disposals is None else dict(disposals),
    }


def _fixture_provenance(fixture: CatalogFixture) -> dict[str, Any]:
    return {
        "origin": "historical_factorio_2_1_21_fixture",
        "path": _display_path(fixture.path),
        "sha256": fixture.sha256,
        "size_bytes": fixture.size_bytes,
        "catalog_schema_version": fixture.catalog_schema_version,
        "force": fixture.force,
        "tick": fixture.tick,
        "recipe_count": fixture.recipe_count,
        "game_version_context": fixture.game_version_context,
        "note": "Exact historical catalog bytes; not the selected live save.",
    }


def _synthetic_provenance(size: int) -> dict[str, Any]:
    return {
        "origin": "synthetic_inline",
        "sha256": None,
        "note": (
            f"Generated in memory with {size} independent product equations and one shared "
            "capped source; not a game export."
        ),
    }


def oil_case_specs(fixture: CatalogFixture) -> list[CaseSpec]:
    """Return realistic historical oil/cracking models using exact fixture bytes."""

    if not fixture.available or fixture.catalog is None:
        return []
    catalog = fixture.catalog
    provenance = _fixture_provenance(fixture)

    advanced_recipes = {
        "advanced-oil-processing": 10,
        "heavy-oil-cracking": 10,
        "light-oil-cracking": 10,
    }
    advanced_sources = {"crude-oil": 300, "water": 400}
    advanced_objective = _objective(
        {name: 1 for name in advanced_recipes},
        {name: -1 for name in advanced_sources},
    )

    alternative_recipes = {
        "advanced-oil-processing": 20,
        "basic-oil-processing": 20,
        "heavy-oil-cracking": 20,
        "light-oil-cracking": 20,
    }
    alternative_sources = {"crude-oil": 1000, "water": 1000}
    alternative_objective = _objective(
        {name: 1 for name in alternative_recipes},
        {name: -1 for name in alternative_sources},
    )

    common = {
        "family": "historical_oil_cracking",
        "catalog": catalog,
        "demand": {"petroleum-gas": 195},
        "disposals": {},
        "catalog_provenance": provenance,
    }
    return [
        CaseSpec(
            case_id="oil-cracking-advanced-budget-0",
            description=(
                "Historical advanced oil processing plus heavy/light cracking; zero budget "
                "must expose no incumbent."
            ),
            recipes=advanced_recipes,
            sources=advanced_sources,
            objective=advanced_objective,
            candidate_budget=0,
            **common,
        ),
        CaseSpec(
            case_id="oil-cracking-advanced-budget-1",
            description=(
                "Historical advanced oil processing plus heavy/light cracking; one candidate "
                "completes the exact optimum."
            ),
            recipes=advanced_recipes,
            sources=advanced_sources,
            objective=advanced_objective,
            candidate_budget=1,
            **common,
        ),
        CaseSpec(
            case_id="oil-cracking-alternatives-budget-6",
            description=(
                "Historical oil model with advanced and basic processing alternatives; six "
                "candidates exhaust the budget with an unproven incumbent."
            ),
            recipes=alternative_recipes,
            sources=alternative_sources,
            objective=alternative_objective,
            candidate_budget=6,
            **common,
        ),
        CaseSpec(
            case_id="oil-cracking-alternatives-budget-12",
            description=(
                "Historical oil model with advanced and basic processing alternatives; twelve "
                "candidates complete exact enumeration."
            ),
            recipes=alternative_recipes,
            sources=alternative_sources,
            objective=alternative_objective,
            candidate_budget=12,
            **common,
        ),
    ]


def _part(name: str, amount: int) -> dict[str, Any]:
    return {"name": name, "type": "item", "amount": amount}


def _synthetic_catalog(size: int) -> dict[str, Any]:
    recipes: list[dict[str, Any]] = []
    for index in range(size):
        recipes.append(
            {
                "name": f"lean-{index}",
                "enabled": True,
                "category": "crafting",
                "energy_ticks": 60,
                "ingredients": [_part("ore", 1)],
                "products": [_part(f"product-{index}", 1)],
                "supported": True,
            }
        )
        recipes.append(
            {
                "name": f"wasteful-{index}",
                "enabled": True,
                "category": "crafting",
                "energy_ticks": 60,
                "ingredients": [_part("ore", 2)],
                "products": [_part(f"product-{index}", 1)],
                "supported": True,
            }
        )
    return {
        "version": "benchmark-synthetic-1.0.0",
        "force": "planner",
        "tick": 0,
        "raw_resources": ["ore"],
        "recipes": recipes,
    }


def synthetic_case_specs(
    sizes: Sequence[int],
    budgets: Sequence[int],
) -> list[CaseSpec]:
    """Build increasing-size, full-rank, exactly constrained choice models."""

    cases: list[CaseSpec] = []
    for size in sizes:
        catalog = _synthetic_catalog(size)
        variable_count = 2 * size + 1
        material_rows = size + 1
        expected = math.comb(2 * variable_count, variable_count - material_rows)
        recipes = {
            f"{kind}-{index}": 10
            for index in range(size)
            for kind in ("lean", "wasteful")
        }
        objective = _objective(
            {name: (1 if name.startswith("lean-") else 0) for name in recipes},
            {"ore": -1},
        )
        provenance = _synthetic_provenance(size)
        for budget in budgets:
            cases.append(
                CaseSpec(
                    case_id=f"synthetic-choice-size-{size}-budget-{budget}",
                    family="synthetic_increasing_size",
                    description=(
                        f"{size} independent products, each with a lean and a wasteful recipe, "
                        "sharing one source capped at exactly the lean demand. The model has "
                        f"{variable_count} variables and {material_rows} full-rank material rows."
                    ),
                    catalog=catalog,
                    recipes=recipes,
                    sources={"ore": size},
                    demand={f"product-{index}": 1 for index in range(size)},
                    disposals={},
                    objective=objective,
                    candidate_budget=budget,
                    catalog_provenance=provenance,
                    expected_vertex_candidates=expected,
                    expected_candidate_basis=(
                        "comb(2 * variables, variables - material_rows) for these full-rank "
                        "models with nonzero caps; this counts the solver's upper- and "
                        "lower-bound candidate pool."
                    ),
                )
            )
    return cases


def _fraction_text(value: Fraction | None) -> str | None:
    return None if value is None else f"{value.numerator}/{value.denominator}"


def _result_signature(result: BoundedRecipeBalanceResult) -> tuple[Any, ...]:
    return (
        result.status,
        result.optimal,
        result.objective_value,
        result.recipe_rates,
        result.source_rates,
        result.disposal_rates,
        result.material_net_rates,
        result.candidates_evaluated,
        result.candidate_budget,
        result.reason,
    )


def _solve(spec: CaseSpec) -> BoundedRecipeBalanceResult:
    return optimize_bounded_recipe_balance(
        spec.catalog,
        recipes=spec.recipes,
        sources=spec.sources,
        demand=spec.demand,
        disposals=spec.disposals,
        objective=spec.objective,
        max_candidate_solves=spec.candidate_budget,
    )


def _measure_timing(
    spec: CaseSpec,
    repeats: int,
) -> tuple[BoundedRecipeBalanceResult, list[float]]:
    samples: list[float] = []
    expected_signature: tuple[Any, ...] | None = None
    result: BoundedRecipeBalanceResult | None = None
    for _ in range(repeats):
        started = time.perf_counter()
        current = _solve(spec)
        samples.append(time.perf_counter() - started)
        signature = _result_signature(current)
        if expected_signature is None:
            expected_signature = signature
            result = current
        elif signature != expected_signature:
            raise RuntimeError(f"timed solver results were not deterministic for {spec.case_id}")
    if result is None:
        raise ValueError("repeats must be at least one")
    return result, samples


def _measure_memory(
    spec: CaseSpec,
    expected_signature: tuple[Any, ...],
) -> int:
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        result = _solve(spec)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        if not already_tracing:
            tracemalloc.stop()
    if _result_signature(result) != expected_signature:
        raise RuntimeError(f"memory-run solver result differs for {spec.case_id}")
    return peak


def _optimality_truth(result: BoundedRecipeBalanceResult) -> str:
    if result.status == "optimal":
        return "optimal_by_complete_candidate_enumeration"
    if result.status == "infeasible":
        return "infeasible_by_exact_contradiction"
    if result.recipe_rates is None:
        return "budget_exhausted_no_incumbent_not_infeasible_not_optimal"
    return "budget_exhausted_feasible_incumbent_not_optimal"


def _rates_payload(result: BoundedRecipeBalanceResult) -> dict[str, Any]:
    recipe_rates = (
        None
        if result.recipe_rates is None
        else {
            decision.name: _fraction_text(rate)
            for decision, rate in zip(result.recipes, result.recipe_rates)
        }
    )
    source_rates = (
        None
        if result.source_rates is None
        else {
            decision.material.name: _fraction_text(rate)
            for decision, rate in zip(result.sources, result.source_rates)
        }
    )
    disposal_rates = (
        None
        if result.disposal_rates is None
        else {
            decision.material.name: _fraction_text(rate)
            for decision, rate in zip(result.disposals, result.disposal_rates)
        }
    )
    material_net_rates = (
        None
        if result.material_net_rates is None
        else {
            material.name: _fraction_text(rate)
            for material, rate in zip(result.materials, result.material_net_rates)
        }
    )
    return {
        "recipe_rates": recipe_rates,
        "source_rates": source_rates,
        "disposal_rates": disposal_rates,
        "material_net_rates": material_net_rates,
    }


def _case_payload(
    spec: CaseSpec,
    result: BoundedRecipeBalanceResult,
    timing_samples: list[float],
    peak_python_alloc_bytes: int,
) -> dict[str, Any]:
    variable_count = len(spec.recipes) + len(spec.sources) + len(spec.disposals)
    return {
        "case_id": spec.case_id,
        "family": spec.family,
        "description": spec.description,
        "catalog": dict(spec.catalog_provenance),
        "candidate_budget": spec.candidate_budget,
        "model": {
            "variables": variable_count,
            "recipe_decisions": len(result.recipes),
            "source_decisions": len(result.sources),
            "disposal_decisions": len(result.disposals),
            "material_rows": len(result.materials),
            "expected_vertex_candidates": spec.expected_vertex_candidates,
            "expected_candidate_basis": spec.expected_candidate_basis,
        },
        "result": {
            "status": result.status,
            "optimal": result.optimal,
            "optimality_claimed": result.optimal,
            "optimality_truth": _optimality_truth(result),
            "budget_exhausted": result.status == "budget_exhausted",
            "budget_exhausted_with_incumbent": (
                result.status == "budget_exhausted" and result.recipe_rates is not None
            ),
            "candidates_evaluated": result.candidates_evaluated,
            "candidate_budget": result.candidate_budget,
            "objective_value": _fraction_text(result.objective_value),
            "api_reason": result.reason,
            **_rates_payload(result),
        },
        "timing": {
            "total_seconds_median": statistics.median(timing_samples),
            "total_seconds_min": min(timing_samples),
            "total_seconds_max": max(timing_samples),
            "total_seconds_samples": timing_samples,
            "timing_repeats": len(timing_samples),
            "time_to_first_valid_seconds": None,
            "time_to_first_valid_available": False,
            "time_to_first_valid_unavailable_reason": (
                "core.bounded_recipe_balance returns only the final result and exposes no "
                "first-valid-incumbent callback, timestamp, or partial-result stream."
            ),
        },
        "memory": {
            "peak_python_alloc_bytes": peak_python_alloc_bytes,
            "peak_python_alloc_mib": peak_python_alloc_bytes / (1024 * 1024),
            "metric": "tracemalloc peak traced Python allocations during a separate identical solver call",
            "tracemalloc_domain": "python",
            "excludes": [
                "interpreter baseline",
                "fixture loading",
                "native allocations outside Python's allocator",
            ],
        },
    }


def run_case(spec: CaseSpec, repeats: int = 3) -> dict[str, Any]:
    """Run one case without tracing for time and separately under tracemalloc."""

    if repeats < 1:
        raise ValueError("repeats must be at least one")
    result, samples = _measure_timing(spec, repeats)
    peak = _measure_memory(spec, _result_signature(result))
    return _case_payload(spec, result, samples, peak)


def build_benchmark(
    *,
    fixture_path: str | Path = HISTORICAL_FIXTURE,
    repeats: int = 3,
    include_fixture: bool = True,
    include_synthetic: bool = True,
    synthetic_sizes: Sequence[int] = (1, 2, 3, 4),
    synthetic_budgets: Sequence[int] = (10, 1000),
) -> dict[str, Any]:
    """Build the complete reproducible report; no files are written here."""

    if repeats < 1:
        raise ValueError("repeats must be at least one")
    for size in synthetic_sizes:
        if size < 1:
            raise ValueError("synthetic sizes must be positive")
    for budget in synthetic_budgets:
        if budget < 0:
            raise ValueError("synthetic budgets must be nonnegative")

    fixture = load_catalog_fixture(fixture_path)
    skipped: list[dict[str, str]] = []
    specs: list[CaseSpec] = []
    if include_fixture:
        if fixture.available:
            specs.extend(oil_case_specs(fixture))
        else:
            skipped.append(
                {
                    "family": "historical_oil_cracking",
                    "reason": fixture.error or "fixture unavailable",
                }
            )
    if include_synthetic:
        specs.extend(synthetic_case_specs(synthetic_sizes, synthetic_budgets))

    return {
        "tool": "benchmark_optimizer",
        "format_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "fixture": fixture_metadata(fixture),
        "requested": {
            "include_fixture": include_fixture,
            "include_synthetic": include_synthetic,
            "timing_repeats": repeats,
            "synthetic_sizes": list(synthetic_sizes),
            "synthetic_budgets": list(synthetic_budgets),
        },
        "methodology": dict(METHODOLOGY),
        "non_claims": list(NON_CLAIMS),
        "skipped_families": skipped,
        "cases": [run_case(spec, repeats=repeats) for spec in specs],
    }


def _positive_int_list(value: str) -> list[int]:
    try:
        items = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not items or any(item < 1 for item in items):
        raise argparse.ArgumentTypeError("expected one or more positive integers")
    return items


def _budget_list(value: str) -> list[int]:
    try:
        items = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not items or any(item < 0 for item in items):
        raise argparse.ArgumentTypeError("expected one or more nonnegative integers")
    return items


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(HISTORICAL_FIXTURE))
    parser.add_argument("--repeat", type=int, default=3, help="timed repetitions per case")
    parser.add_argument(
        "--sizes",
        type=_positive_int_list,
        default=[1, 2, 3, 4],
        help="comma-separated synthetic model sizes",
    )
    parser.add_argument(
        "--budgets",
        type=_budget_list,
        default=[10, 1000],
        help="comma-separated synthetic candidate budgets",
    )
    parser.add_argument("--no-fixture", action="store_true", help="skip historical oil cases")
    parser.add_argument("--no-synthetic", action="store_true", help="skip synthetic scaling cases")
    parser.add_argument("--output", type=Path, help="write the JSON report to this path")
    args = parser.parse_args(argv)

    report = build_benchmark(
        fixture_path=args.fixture,
        repeats=args.repeat,
        include_fixture=not args.no_fixture,
        include_synthetic=not args.no_synthetic,
        synthetic_sizes=args.sizes,
        synthetic_budgets=args.budgets,
    )
    text = json.dumps(report, indent=2)
    if args.output is None:
        print(text)
    else:
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
