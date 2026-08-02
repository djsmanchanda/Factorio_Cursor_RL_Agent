# Path: tests/test_feed_endpoints.py
# Purpose: Prove a line's feed endpoint can always be filled by something, and that a stage which fails half-built says so.

from __future__ import annotations

import inspect
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator.stage_transport import _swap_infinity_chests  # noqa: E402
from planners.local_layout_planner import LocalLayoutPlanner  # noqa: E402
from planners.plan_validation import actions  # noqa: E402

_INGREDIENTS = ("iron-gear-wheel", "iron-plate")


def _feeds(modes: dict[str, str]) -> list[dict]:
    plan = LocalLayoutPlanner().generate_line_layout(
        "transport-belt", 6, 0, 0, belt_type="transport-belt",
        inserter_type="fast-inserter", feed_style="chest",
        terminal_collector=True, flow_direction="east",
    )
    positions = _swap_infinity_chests(plan, modes)
    return [
        action for action in actions(plan)
        if (action["position"]["x"], action["position"]["y"]) in positions.values()
        and action.get("entity", "").endswith("chest")
    ]


def test_a_belt_fed_endpoint_is_still_a_requester() -> None:
    """A plain steel chest can only be filled by a belt. When the bridge for
    that ingredient failed, nothing could ever fill it and the line was
    permanently dead -- seven machines, zero working, and a repair pass that
    kept calling it merely 'supply-starved'."""
    feeds = _feeds({name: "belt" for name in _INGREDIENTS})

    assert feeds
    for chest in feeds:
        assert chest["entity"] == "requester-chest"
        assert chest["logistic_request"]["name"] in _INGREDIENTS


def test_a_bot_fed_endpoint_is_unchanged() -> None:
    feeds = _feeds({name: "logistic" for name in _INGREDIENTS})

    for chest in feeds:
        assert chest["entity"] == "requester-chest"


def test_every_feed_endpoint_asks_for_its_own_ingredient() -> None:
    """A requester asking for the wrong item starves the machine beside it."""
    plan = LocalLayoutPlanner().generate_line_layout(
        "transport-belt", 6, 0, 0, belt_type="transport-belt",
        inserter_type="fast-inserter", feed_style="chest",
        terminal_collector=True, flow_direction="east",
    )
    wanted = {
        (a["position"]["x"], a["position"]["y"]): a["infinity_filter"]
        for a in actions(plan) if a.get("entity") == "infinity-chest"
    }
    _swap_infinity_chests(plan, {name: "belt" for name in _INGREDIENTS})

    for action in actions(plan):
        if action.get("entity") == "requester-chest":
            key = (action["position"]["x"], action["position"]["y"])
            assert action["logistic_request"]["name"] == wanted[key]


def test_no_feed_endpoint_is_left_as_a_plain_chest() -> None:
    """The whole point: nothing on the feed side depends on a belt existing."""
    for mode in ("belt", "logistic"):
        for chest in _feeds({name: mode for name in _INGREDIENTS}):
            assert chest["entity"] != "steel-chest"


def test_a_half_built_stage_says_why_it_stopped() -> None:
    """It was silent, so a stage that failed part-way looked identical in the
    log to one nobody had started -- the iron-plate belt simply never appeared,
    with no line explaining it."""
    served = inspect.getsource(builder._serve_mall_task)
    clause = served[served.index("except MaterialShortage"):]

    assert "emit(" in clause[:clause.index("return")]
    assert "MALL DEMAND" in clause


def test_the_shortage_names_what_is_missing() -> None:
    served = inspect.getsource(builder._serve_mall_task)

    assert "shortage.required.items()" in served
    assert "add_demands(mall_targets, shortage)" in served
