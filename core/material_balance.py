# Path: core/material_balance.py
# Purpose: Solve fixed material-balance equations without choosing a production strategy.

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, Sequence


@dataclass(frozen=True)
class BalanceSolution:
    status: Literal["unique", "underdetermined", "inconsistent"]
    rates: tuple[Fraction, ...] | None = None


def solve_fixed_balance(
    coefficients: Sequence[Sequence[int | Fraction]],
    demand: Sequence[int | Fraction],
) -> BalanceSolution:
    """Solve A x = demand exactly; no nonnegativity or capacity constraints are applied.

    Rows represent material balances and columns represent fixed recipe or source
    rates. An underdetermined result does not establish a feasible nonnegative
    solution. A unique result must also be checked against physical constraints.
    """
    if not coefficients or len(coefficients) != len(demand):
        raise ValueError("material rows and demand must have the same nonzero length")
    width = len(coefficients[0])
    if not width or any(len(row) != width for row in coefficients):
        raise ValueError("material rows must have the same nonzero width")
    if any(not isinstance(value, (int, Fraction)) for row in coefficients for value in row) or any(
        not isinstance(value, (int, Fraction)) for value in demand
    ):
        raise TypeError("balance values must be integers or Fractions")

    matrix = [
        [Fraction(value) for value in row] + [Fraction(target)]
        for row, target in zip(coefficients, demand)
    ]
    pivot_columns: list[int] = []
    pivot_row = 0
    for column in range(width):
        selected = next((row for row in range(pivot_row, len(matrix)) if matrix[row][column]), None)
        if selected is None:
            continue
        matrix[pivot_row], matrix[selected] = matrix[selected], matrix[pivot_row]
        pivot = matrix[pivot_row][column]
        matrix[pivot_row] = [value / pivot for value in matrix[pivot_row]]
        for row in range(len(matrix)):
            if row != pivot_row and matrix[row][column]:
                multiplier = matrix[row][column]
                matrix[row] = [
                    value - multiplier * pivot_value
                    for value, pivot_value in zip(matrix[row], matrix[pivot_row])
                ]
        pivot_columns.append(column)
        pivot_row += 1
        if pivot_row == len(matrix):
            break

    if any(not any(row[:width]) and row[width] for row in matrix):
        return BalanceSolution("inconsistent")
    if len(pivot_columns) < width:
        return BalanceSolution("underdetermined")
    rates = [Fraction(0)] * width
    for row, column in enumerate(pivot_columns):
        rates[column] = matrix[row][width]
    return BalanceSolution("unique", tuple(rates))
