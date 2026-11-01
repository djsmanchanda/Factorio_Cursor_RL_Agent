# Path: tests/test_material_balance.py
# Purpose: Verify exact fixed-system material balance and its feasibility boundaries.

from fractions import Fraction
import json
from pathlib import Path

import pytest

from core.material_balance import BalanceSolution, solve_fixed_balance
from core.recipe_dag import compile_recipe_dag

CATALOG = json.loads((Path(__file__).parent / "fixtures/recipe_catalog.json").read_text())


def test_single_output_chain_agrees_with_existing_dag():
    goal = {"version": "1.0.0", "target": "copper-cable", "delta": {"rate": 2, "unit": "per_second"}}
    graph = compile_recipe_dag(goal, CATALOG)
    solution = solve_fixed_balance([[1, -1], [0, 2]], [0, 2])
    assert solution == BalanceSolution("unique", (Fraction(1), Fraction(1)))
    rates = {node["recipe"]: node["crafts_per_second"] for node in graph["nodes"]}
    assert rates["copper-plate"] == float(solution.rates[0])
    assert rates["copper-cable"] == float(solution.rates[1])


def test_coproducts_and_cyclic_fixed_system_are_balanced_exactly():
    solution = solve_fixed_balance([[2, -1], [1, 1]], [1, 1])
    assert solution == BalanceSolution("unique", (Fraction(2, 3), Fraction(1, 3)))


def test_rectangular_and_degenerate_systems_are_distinguished():
    assert solve_fixed_balance([[1], [2]], [2, 4]).rates == (Fraction(2),)
    assert solve_fixed_balance([[1], [2]], [2, 5]).status == "inconsistent"
    assert solve_fixed_balance([[1, 1]], [1]).status == "underdetermined"
    assert solve_fixed_balance([[0, 0]], [1]).status == "inconsistent"
    assert solve_fixed_balance([[0, 0]], [0]).status == "underdetermined"


def test_unique_negative_rates_are_not_claimed_feasible():
    assert solve_fixed_balance([[1]], [-1]).rates == (Fraction(-1),)


@pytest.mark.parametrize("matrix,demand", [([], []), ([[1]], []), ([[]], [1]), ([[1], [1, 2]], [1, 2])])
def test_invalid_dimensions_are_rejected(matrix, demand):
    with pytest.raises(ValueError):
        solve_fixed_balance(matrix, demand)


def test_floats_are_not_silently_converted():
    with pytest.raises(TypeError):
        solve_fixed_balance([[0.1]], [1])
