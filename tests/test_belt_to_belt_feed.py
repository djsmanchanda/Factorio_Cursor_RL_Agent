# Path: tests/test_belt_to_belt_feed.py
# Purpose: Prove a belt-fed stage is BUILT the way the preflight approved -- one continuous belt with no inserter spliced into it -- and that running short of belt queues more rather than ending the run.

from __future__ import annotations

import inspect
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator import stage_transport  # noqa: E402
from orchestrator.parts_mall import MaterialShortage  # noqa: E402
from planners.belt_bridge import (  # noqa: E402
    bridge_belt_to_belt,
    bridge_belt_to_chest,
    bridge_chest_to_chest,
)

_PLAN = inspect.getsource(stage_transport._plan_belt_transport)


def _shape(fn, **kwargs) -> Counter:
    return Counter(
        action["entity"]
        for action in fn((0.5, 0.5), (20.5, 0.5), belt_type="transport-belt", **kwargs)
    )


def test_a_belt_to_belt_join_needs_no_inserter() -> None:
    """The join a mine belt makes with a furnace row's input belt is one
    continuous belt. An inserter spliced into it is a throughput cap and an
    extra hop for no reason."""
    assert _shape(bridge_belt_to_belt, entry_direction="west")["inserter"] == 0


def test_the_chest_shaped_bridges_do_need_them() -> None:
    """Which is why using one for a belt destination was the defect: the
    inserters are correct there, and wrong here."""
    assert _shape(
        bridge_belt_to_chest, entry_direction="west", inserter_type="inserter",
    )["inserter"] == 1
    assert _shape(
        bridge_chest_to_chest, exit_direction="east", entry_direction="west",
        inserter_type="inserter",
    )["inserter"] == 2


def test_the_build_path_knows_when_the_destination_is_a_belt() -> None:
    """It did not. The preflight took `destination_is_belt` and planned a
    belt-to-belt join; the build path had no such parameter and laid a
    chest-shaped bridge instead -- a different plan from the one approved."""
    assert "destination_is_belt" in inspect.signature(
        stage_transport._plan_belt_transport
    ).parameters


def test_both_belt_ends_produce_a_belt_to_belt_bridge() -> None:
    assert "if belt_source is not None and destination_is_belt:" in _PLAN
    assert "bridge_belt_to_belt(" in _PLAN


def test_a_chest_destination_still_gets_its_inserter() -> None:
    assert "elif belt_source is not None:" in _PLAN
    assert "bridge_belt_to_chest(" in _PLAN


def test_the_conversion_stage_passes_the_flag_to_the_build_not_only_the_preflight() -> None:
    """The flag existed and was threaded to the preflight alone, which is why
    the two disagreed."""
    connect = inspect.getsource(builder._connect_stage_feeds)

    assert "destination_is_belt=direct_belt_input" in connect


def test_running_short_of_belt_is_recoverable() -> None:
    """Every other build path turns a shortage into a mall target and retries.
    Raising StuckError here ended whole runs on a bridge the base could have
    supplied minutes later -- 'short transport-belt by 96' while the mall held
    an unfilled 4800-belt target."""
    tail = _PLAN[_PLAN.index("if not shortfalls and route_error is not None:"):]

    assert "raise MaterialShortage(" in tail


def test_the_shortage_asks_for_the_cheapest_tier() -> None:
    """It is the tier the mall can actually produce; asking for a faster one
    would queue a part the base may have no recipe for."""
    tail = _PLAN[_PLAN.index("raise MaterialShortage("):]
    ordering = _PLAN[_PLAN.index("for tier in _BELT_TIERS_CHEAPEST_FIRST:"):]

    assert ordering.index("raise MaterialShortage(") < len(ordering)
    assert "requirements[tier]" in tail


def test_a_shortage_carries_a_real_requirement() -> None:
    shortage = MaterialShortage(
        "belt bridge for iron-ore", {"transport-belt": 120}, {"transport-belt": 24},
    )

    assert shortage.required == {"transport-belt": 120}
    assert "short 96" in str(shortage)


def test_an_unroutable_bridge_is_still_a_hard_failure() -> None:
    """No route at all is a geometry problem; more belt cannot fix it."""
    assert "no belt route is available for this bridge" in _PLAN
    assert "raise StuckError(" in _PLAN
