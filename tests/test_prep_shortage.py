# Path: tests/test_prep_shortage.py
# Purpose: Prove a prep pass that runs out of drills queues them with the mall instead of ending the run, since prep is the first thing that ever asks for a whole drill phase at once.

from __future__ import annotations

import inspect
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator import autonomous_builder  # noqa: E402
from orchestrator.parts_mall import MaterialShortage, add_demands  # noqa: E402

_RUN = inspect.getsource(autonomous_builder.run)
_PREP = inspect.getsource(autonomous_builder._prep_plate_extraction)
_LOOP = _RUN + "".join(
    inspect.getsource(helper) for helper in (
        autonomous_builder._ensure_mall_item,
        autonomous_builder._serve_mall_task,
        autonomous_builder._prep_plate_extraction,
        autonomous_builder._prep_intermediate,
    )
)


def test_a_material_shortage_in_plate_prep_is_not_fatal() -> None:
    """The live fault: 'electric-mining-drill: need 14, short 6' propagated out
    of build_mining_stage and ended the run."""
    assert "except MaterialShortage as shortage:" in _PREP


def test_the_shortfall_becomes_a_mall_target() -> None:
    """Raising a drill phase needs drills, and drills come from the mall --
    the bottleneck has to be pushed back a step, not just survived."""
    assert "add_demands(mall_targets, shortage)" in _PREP


def test_a_shortage_is_not_mistaken_for_a_permanent_deferral() -> None:
    """PREP DEFERRED marks a plate as done for the run. A shortage is temporary
    -- the mall is about to fix it -- so it must not take that path."""
    shortage_clause = _PREP[_PREP.index("except MaterialShortage"):]
    shortage_clause = shortage_clause[:shortage_clause.index("except (StuckError")]

    assert "prepped.add" not in shortage_clause


def test_material_shortage_is_not_caught_by_the_deferral_clause() -> None:
    """The bug in one line: MaterialShortage is a RuntimeError, so the
    (StuckError, ValueError) clause never saw it."""
    assert issubclass(MaterialShortage, RuntimeError)
    assert not issubclass(MaterialShortage, (ValueError,))
    assert not issubclass(MaterialShortage, autonomous_builder.StuckError)


def test_every_build_call_in_the_run_loop_handles_a_shortage() -> None:
    """One unguarded call is enough to end a run, and this one was."""
    guarded = _LOOP.count("except MaterialShortage as shortage:")

    assert guarded >= 4, f"only {guarded} shortage handlers across the run loop"


def test_demands_keep_the_largest_target() -> None:
    targets = {"electric-mining-drill": 6}
    shortage = MaterialShortage(
        "mining_iron-ore", {"electric-mining-drill": 14}, {"electric-mining-drill": 8},
    )
    add_demands(targets, shortage)

    assert targets["electric-mining-drill"] == 14
    assert "need 14, short 6" in str(shortage), "the message the live run printed"
