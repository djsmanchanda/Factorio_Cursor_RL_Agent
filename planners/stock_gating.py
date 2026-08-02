# Path: planners/stock_gating.py
# Purpose: Build the logistic condition that stops a mall machine crafting once the base already holds enough of what it makes.

from __future__ import annotations

# Comparators the game accepts on input. It returns the unicode forms when read
# back, so anything comparing a stored condition has to expect either.
COMPARATORS = frozenset({"<", ">", "=", "≥", "≤", "≠", ">=", "<=", "!="})

# The one this planner uses: craft while the network holds FEWER than the
# target. Not "≤", which would let a full network craft one more.
BELOW = "<"


def stock_gate(item: str, target: int) -> dict:
    """Craft only while the logistic network holds fewer than `target`.

    This is a LOGISTIC condition, not a circuit one. An assembling machine can
    read the logistic network directly -- see LuaGenericOnOffControlBehavior's
    `connect_to_logistic_network` -- so the machine needs no wire, no roboport
    connection, and no combinator. It only has to stand inside roboport
    coverage, which a requester-fed mall cell does by construction.

    The gate is what stops the mall rebuilding stock it already has. Before it,
    the only brake was the planner noticing on a later pass and dropping the
    target, which cost a pass and did nothing in between.
    """
    if not item:
        raise ValueError("A stock gate needs an item to watch")
    if target < 1:
        raise ValueError(f"A stock gate target must be positive, got {target}")
    return {"signal": item, "comparator": BELOW, "constant": int(target)}


def gate_matches(condition: dict | None, item: str, target: int) -> bool:
    """Whether a stored condition already says what we would set.

    Used to leave a correct machine alone. The comparator is compared loosely
    because the game returns unicode forms for the two-character operators.
    """
    if not condition:
        return False
    return (
        condition.get("signal") == item
        and int(condition.get("constant", -1)) == int(target)
        and condition.get("comparator") in {BELOW}
    )
