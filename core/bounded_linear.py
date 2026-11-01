# Path: core/bounded_linear.py
# Purpose: Maximise an explicit rational objective over bounded nonnegative variables exactly.

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
from typing import Literal

Number = int | Fraction
Status = Literal["optimal", "infeasible", "budget_exhausted"]

# Small-system limits keep the vertex enumeration finite without a time-based
# cutoff. Callers with larger models must decompose them before solving.
MAX_VARIABLES = 10
MAX_CONSTRAINT_ROWS = 20
DEFAULT_CANDIDATE_BUDGET = 5_000
MAX_CANDIDATE_BUDGET = 100_000


@dataclass(frozen=True)
class BoundedLinearSolution:
    status: Status
    objective_value: Fraction | None = None
    variables: tuple[Fraction, ...] | None = None
    candidates_evaluated: int = 0


def solve_bounded_linear(
    objective: Sequence[Number],
    upper_bounds: Sequence[Number],
    *,
    equalities: Sequence[Sequence[Number]] = (),
    equality_targets: Sequence[Number] = (),
    inequalities: Sequence[Sequence[Number]] = (),
    inequality_targets: Sequence[Number] = (),
    max_candidate_solves: int = DEFAULT_CANDIDATE_BUDGET,
) -> BoundedLinearSolution:
    """Maximise ``objective @ x`` subject to exact bounded linear constraints.

    The model is::

        maximise    c @ x
        subject to  A_eq x = b_eq
                    A_ineq x <= b_ineq
                    0 <= x_i <= upper_bounds[i]

    Every variable has a mandatory finite nonnegative upper bound, so the
    feasible region is bounded and no unbounded status is possible. Equalities
    and inequalities are optional; callers supply the objective and all caps
    explicitly rather than relying on hidden weights.

    The solver enumerates candidate vertices formed by the equality basis plus
    variable bounds and active inequalities. It uses only standard-library
    ``Fraction`` arithmetic, checks every candidate against the original
    constraints, and revalidates the incumbent before returning it. Equal
    objective values are resolved by the lexicographically smallest variable
    tuple, independent of enumeration order.

    ``candidates_evaluated`` counts candidate vertex systems attempted. When
    more candidates exist than ``max_candidate_solves`` allows, the status is
    ``budget_exhausted``: a feasible incumbent may be returned, but it is not
    claimed optimal, and an empty incumbent is not claimed infeasible. Exact
    algebraic contradictions (inconsistent equalities or a negative-target
    zero-row inequality) are reported as ``infeasible`` even at zero budget
    because they are proofs independent of candidate enumeration.

    This is an offline primitive. It does not read catalogs, call SciPy, choose
    recipes, validate machines, or touch any runtime.
    """
    objective_values = _exact_values(objective, "objective")
    width = len(objective_values)
    if width > MAX_VARIABLES:
        raise ValueError(f"at most {MAX_VARIABLES} variables are supported")

    upper_values = _exact_values(upper_bounds, "upper_bounds")
    if len(upper_values) != width:
        raise ValueError("objective and upper_bounds must have the same length")
    for index, cap in enumerate(upper_values):
        if cap < 0:
            raise ValueError(f"upper_bounds[{index}] must be nonnegative")

    equality_rows = _exact_matrix(equalities, width, "equalities")
    equality_values = _exact_values(equality_targets, "equality_targets")
    if len(equality_rows) != len(equality_values):
        raise ValueError("equalities and equality_targets must have the same length")

    inequality_rows = _exact_matrix(inequalities, width, "inequalities")
    inequality_values = _exact_values(inequality_targets, "inequality_targets")
    if len(inequality_rows) != len(inequality_values):
        raise ValueError("inequalities and inequality_targets must have the same length")

    if len(equality_rows) + len(inequality_rows) > MAX_CONSTRAINT_ROWS:
        raise ValueError(f"at most {MAX_CONSTRAINT_ROWS} constraint rows are supported")

    budget = _exact_budget(max_candidate_solves)

    augmented_equalities = [
        [*row, target] for row, target in zip(equality_rows, equality_values)
    ]
    reduced = _rref(augmented_equalities, width)
    if reduced is None:
        return BoundedLinearSolution("infeasible")
    equality_rref, equality_pivots = reduced
    equality_basis = equality_rref[: len(equality_pivots)]

    for row, target in zip(inequality_rows, inequality_values):
        if not any(row) and target < 0:
            return BoundedLinearSolution("infeasible")

    pool = _active_pool(width, upper_values, inequality_rows, inequality_values)
    remaining = width - len(equality_pivots)
    total_candidates = math.comb(len(pool), remaining)

    best_variables: tuple[Fraction, ...] | None = None
    best_objective: Fraction | None = None
    evaluated = 0

    for active in combinations(pool, remaining):
        if evaluated >= budget:
            break
        evaluated += 1
        candidate = _solve_vertex(
            equality_basis,
            [[*row, target] for row, target in active],
            width,
        )
        if candidate is None:
            continue
        if _primal_violation(
            candidate,
            upper_values,
            equality_rows,
            equality_values,
            inequality_rows,
            inequality_values,
        ) is not None:
            continue
        value = _dot(objective_values, candidate)
        if (
            best_objective is None
            or value > best_objective
            or (value == best_objective and candidate < best_variables)
        ):
            best_objective = value
            best_variables = candidate

    exhausted = total_candidates > budget
    if exhausted:
        status: Status = "budget_exhausted"
    elif best_variables is not None:
        status = "optimal"
    else:
        status = "infeasible"

    if best_variables is not None:
        violation = _primal_violation(
            best_variables,
            upper_values,
            equality_rows,
            equality_values,
            inequality_rows,
            inequality_values,
        )
        if violation is not None:
            raise RuntimeError(f"candidate failed independent primal validation: {violation}")
        revalidated = _dot(objective_values, best_variables)
        if revalidated != best_objective:
            raise RuntimeError("candidate objective failed independent revalidation")

    return BoundedLinearSolution(
        status,
        best_objective,
        best_variables,
        evaluated,
    )


def _exact(value: object, label: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, Fraction)):
        raise TypeError(f"{label} must be an int or Fraction, not {type(value).__name__}")
    return Fraction(value)


def _exact_values(values: object, label: str) -> tuple[Fraction, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{label} must be a sequence of ints or Fractions")
    return tuple(_exact(value, f"{label}[{index}]") for index, value in enumerate(values))


def _exact_matrix(
    rows: object,
    width: int,
    label: str,
) -> tuple[tuple[Fraction, ...], ...]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise TypeError(f"{label} must be a sequence of rows")
    result: list[tuple[Fraction, ...]] = []
    for row_index, row in enumerate(rows):
        if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
            raise TypeError(f"{label}[{row_index}] must be a sequence of ints or Fractions")
        if len(row) != width:
            raise ValueError(f"{label}[{row_index}] must have width {width}")
        result.append(
            tuple(
                _exact(value, f"{label}[{row_index}][{column}]")
                for column, value in enumerate(row)
            )
        )
    return tuple(result)


def _exact_budget(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_candidate_solves must be an int")
    if value < 0:
        raise ValueError("max_candidate_solves must be nonnegative")
    if value > MAX_CANDIDATE_BUDGET:
        raise ValueError(f"max_candidate_solves may not exceed {MAX_CANDIDATE_BUDGET}")
    return value


def _rref(
    matrix: list[list[Fraction]],
    width: int,
) -> tuple[list[list[Fraction]], list[int]] | None:
    rows = [row[:] for row in matrix]
    pivot_columns: list[int] = []
    pivot_row = 0
    for column in range(width):
        selected = next(
            (
                row_index
                for row_index in range(pivot_row, len(rows))
                if rows[row_index][column] != 0
            ),
            None,
        )
        if selected is None:
            continue
        rows[pivot_row], rows[selected] = rows[selected], rows[pivot_row]
        pivot = rows[pivot_row][column]
        rows[pivot_row] = [value / pivot for value in rows[pivot_row]]
        for row_index in range(len(rows)):
            if row_index == pivot_row:
                continue
            factor = rows[row_index][column]
            if factor:
                rows[row_index] = [
                    value - factor * pivot_value
                    for value, pivot_value in zip(rows[row_index], rows[pivot_row])
                ]
        pivot_columns.append(column)
        pivot_row += 1
        if pivot_row == len(rows):
            break
    for row in rows:
        if all(value == 0 for value in row[:width]) and row[width] != 0:
            return None
    return rows, pivot_columns


def _solve_vertex(
    equality_basis: list[list[Fraction]],
    active_rows: list[list[Fraction]],
    width: int,
) -> tuple[Fraction, ...] | None:
    reduced = _rref([*equality_basis, *active_rows], width)
    if reduced is None:
        return None
    rows, pivot_columns = reduced
    if len(pivot_columns) != width:
        return None
    solution = [Fraction(0)] * width
    for row_index, column in enumerate(pivot_columns):
        solution[column] = rows[row_index][width]
    return tuple(solution)


def _active_pool(
    width: int,
    upper_bounds: tuple[Fraction, ...],
    inequality_rows: tuple[tuple[Fraction, ...], ...],
    inequality_targets: tuple[Fraction, ...],
) -> tuple[tuple[tuple[Fraction, ...], Fraction], ...]:
    candidates: list[tuple[tuple[Fraction, ...], Fraction]] = []
    for index in range(width):
        unit = tuple(Fraction(1) if column == index else Fraction(0) for column in range(width))
        candidates.append((unit, Fraction(0)))
    for index in range(width):
        if upper_bounds[index] != 0:
            unit = tuple(
                Fraction(1) if column == index else Fraction(0) for column in range(width)
            )
            candidates.append((unit, upper_bounds[index]))
    for row, target in zip(inequality_rows, inequality_targets):
        if any(row):
            candidates.append((row, target))

    unique: list[tuple[tuple[Fraction, ...], Fraction]] = []
    seen: set[tuple[tuple[Fraction, ...], Fraction]] = set()
    for constraint in candidates:
        if constraint not in seen:
            seen.add(constraint)
            unique.append(constraint)
    return tuple(unique)


def _dot(row: tuple[Fraction, ...], values: tuple[Fraction, ...]) -> Fraction:
    total = Fraction(0)
    for coefficient, value in zip(row, values):
        total += coefficient * value
    return total


def _primal_violation(
    variables: tuple[Fraction, ...],
    upper_bounds: tuple[Fraction, ...],
    equality_rows: tuple[tuple[Fraction, ...], ...],
    equality_targets: tuple[Fraction, ...],
    inequality_rows: tuple[tuple[Fraction, ...], ...],
    inequality_targets: tuple[Fraction, ...],
) -> str | None:
    if len(variables) != len(upper_bounds):
        return "variable width"
    for index, value in enumerate(variables):
        if value < 0:
            return f"negative variable {index}"
        if value > upper_bounds[index]:
            return f"variable {index} exceeds its cap"
    for row, target in zip(equality_rows, equality_targets):
        if _dot(row, variables) != target:
            return "equality not satisfied"
    for row, target in zip(inequality_rows, inequality_targets):
        if _dot(row, variables) > target:
            return "inequality violated"
    return None
