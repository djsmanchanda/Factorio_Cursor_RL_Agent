# Path: tests/test_bounded_linear.py
# Purpose: Verify exact bounded linear optimisation, budgets, ties, and input rejection.

from fractions import Fraction

import pytest

from core.bounded_linear import (
    DEFAULT_CANDIDATE_BUDGET,
    MAX_CANDIDATE_BUDGET,
    MAX_CONSTRAINT_ROWS,
    MAX_VARIABLES,
    solve_bounded_linear,
)


def test_multi_output_oil_like_choice_uses_explicit_objective():
    # Variables: heavy cracking, light cracking, advanced oil processing.
    # Balances are illustrative, not claims about a live Factorio catalog.
    result = solve_bounded_linear(
        [0, 20, 55],
        [10, 10, 10],
        equalities=[
            [-40, 0, 25],
            [30, -30, 45],
        ],
        equality_targets=[0, 0],
        inequalities=[[0, 0, 100]],
        inequality_targets=[200],
    )

    assert result.status == "optimal"
    assert result.variables == (Fraction(5, 4), Fraction(17, 4), Fraction(2))
    assert result.objective_value == Fraction(195)
    assert result.candidates_evaluated == 7


def test_degenerate_duplicate_equalities_and_zero_cap():
    result = solve_bounded_linear(
        [1, 1],
        [0, 2],
        equalities=[[1, 1], [2, 2]],
        equality_targets=[2, 4],
    )

    assert result.status == "optimal"
    assert result.variables == (Fraction(0), Fraction(2))
    assert result.objective_value == Fraction(2)
    assert result.candidates_evaluated == 3


def test_rectangular_inequality_system_and_lexicographic_tie():
    result = solve_bounded_linear(
        [1, 1],
        [2, 2],
        inequalities=[[1, 0], [0, 1], [1, 1]],
        inequality_targets=[2, 2, 3],
    )

    assert result.status == "optimal"
    assert result.variables == (Fraction(1), Fraction(2))
    assert result.objective_value == Fraction(3)


def test_zero_value_variables_and_zero_dimension_feasible_problem():
    pinned = solve_bounded_linear([1, 1], [0, 0])
    assert pinned.status == "optimal"
    assert pinned.variables == (Fraction(0), Fraction(0))
    assert pinned.objective_value == Fraction(0)

    empty = solve_bounded_linear([], [])
    assert empty.status == "optimal"
    assert empty.variables == ()
    assert empty.objective_value == Fraction(0)
    assert empty.candidates_evaluated == 1


def test_no_feasible_point_from_cap_conflict():
    result = solve_bounded_linear([1], [1], equalities=[[1]], equality_targets=[2])

    assert result.status == "infeasible"
    assert result.variables is None
    assert result.objective_value is None
    assert result.candidates_evaluated == 1


def test_no_feasible_point_from_conflicting_inequalities():
    result = solve_bounded_linear(
        [1],
        [1],
        inequalities=[[1], [-1]],
        inequality_targets=[0, -2],
    )

    assert result.status == "infeasible"
    assert result.variables is None


def test_rational_tie_resolution_is_stable():
    model = dict(
        objective=[0, 0],
        upper_bounds=[Fraction(2, 3), Fraction(2, 3)],
        equalities=[[1, 1]],
        equality_targets=[1],
    )
    result = solve_bounded_linear(**model)
    repeated = solve_bounded_linear(**model)

    assert result.status == "optimal"
    assert result.variables == (Fraction(1, 3), Fraction(2, 3))
    assert result.objective_value == Fraction(0)
    assert result.candidates_evaluated == 4
    assert repeated == result


def test_budget_exhaustion_preserves_a_valid_incumbent():
    result = solve_bounded_linear(
        [1, 2],
        [5, 5],
        inequalities=[[1, 1]],
        inequality_targets=[1],
        max_candidate_solves=1,
    )

    assert result.status == "budget_exhausted"
    assert result.variables == (Fraction(0), Fraction(0))
    assert result.objective_value == Fraction(0)
    assert result.candidates_evaluated == 1


def test_early_budget_exhaustion_does_not_claim_an_incumbent_or_infeasibility():
    result = solve_bounded_linear(
        [1],
        [5],
        inequalities=[[-1]],
        inequality_targets=[-1],
        max_candidate_solves=1,
    )

    assert result.status == "budget_exhausted"
    assert result.variables is None
    assert result.objective_value is None
    assert result.candidates_evaluated == 1


def test_explicit_caps_limit_the_objective():
    capped = solve_bounded_linear(
        [1],
        [3],
        inequalities=[[1]],
        inequality_targets=[10],
    )
    assert capped.status == "optimal"
    assert capped.variables == (Fraction(3),)
    assert capped.objective_value == Fraction(3)


def test_equality_contradictions_are_infeasible_without_candidates():
    result = solve_bounded_linear(
        [1, 1],
        [5, 5],
        equalities=[[1, 1], [1, 1]],
        equality_targets=[1, 2],
    )
    zero_row = solve_bounded_linear(
        [1, 1],
        [5, 5],
        equalities=[[0, 0]],
        equality_targets=[1],
    )

    assert result.status == "infeasible"
    assert result.candidates_evaluated == 0
    assert zero_row.status == "infeasible"
    assert zero_row.candidates_evaluated == 0


@pytest.mark.parametrize(
    "objective,upper_bounds,options",
    [
        ([1, 2], [1], {}),
        ([1, 2], [1, 2], {"equalities": [[1]], "equality_targets": [1]}),
        ([1, 2], [1, 2], {"equalities": [[1, 2]], "equality_targets": []}),
        ([1, 2], [1, 2], {"inequalities": [[1]], "inequality_targets": [1]}),
        ([1, 2], [1, 2], {"inequalities": [[1, 2]], "inequality_targets": []}),
    ],
)
def test_invalid_dimensions_are_rejected(objective, upper_bounds, options):
    with pytest.raises(ValueError):
        solve_bounded_linear(objective, upper_bounds, **options)


@pytest.mark.parametrize(
    "objective,upper_bounds,options,error",
    [
        ([1.0], [1], {}, TypeError),
        ([True], [1], {}, TypeError),
        ([1], [1.0], {}, TypeError),
        ([1], [True], {}, TypeError),
        ([1], [-1], {}, ValueError),
        ("1", [1], {}, TypeError),
        ([1], [1], {"equalities": 1}, TypeError),
        ([1], [1], {"equalities": [[True]]}, TypeError),
        ([1], [1], {"equalities": [[1]], "equality_targets": 1}, TypeError),
        ([1], [1], {"equalities": [[1]], "equality_targets": [None]}, TypeError),
        ([1], [1], {"inequalities": [[None]], "inequality_targets": [0]}, TypeError),
        ([1], [1], {"max_candidate_solves": True}, TypeError),
        ([1], [1], {"max_candidate_solves": 1.0}, TypeError),
        ([1], [1], {"max_candidate_solves": -1}, ValueError),
        ([1], [1], {"max_candidate_solves": MAX_CANDIDATE_BUDGET + 1}, ValueError),
    ],
)
def test_invalid_input_types_and_caps_are_rejected(
    objective,
    upper_bounds,
    options,
    error,
):
    with pytest.raises(error):
        solve_bounded_linear(objective, upper_bounds, **options)


def test_explicit_dimension_and_budget_limits_are_enforced():
    with pytest.raises(ValueError):
        solve_bounded_linear(
            list(range(MAX_VARIABLES + 1)),
            [1] * (MAX_VARIABLES + 1),
        )

    with pytest.raises(ValueError):
        solve_bounded_linear(
            [1],
            [1],
            equalities=[[0]] * (MAX_CONSTRAINT_ROWS + 1),
            equality_targets=[0] * (MAX_CONSTRAINT_ROWS + 1),
        )

    assert DEFAULT_CANDIDATE_BUDGET < MAX_CANDIDATE_BUDGET
