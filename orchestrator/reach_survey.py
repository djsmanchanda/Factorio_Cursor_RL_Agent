# Path: orchestrator/reach_survey.py
# Purpose: Bounded live reads yielded by the pure coverage/power reach planners, and how live drivers answer them.

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass

from orchestrator import live_base, resource_patches
from tools.rcon_client import RconClient

Point = tuple[float, float]


@dataclass(frozen=True)
class ReachSurvey:
    """One bounded read a reach planner needs before it can continue.

    Each kind names the `live_base` / `resource_patches` read it stands for;
    ``flags`` are that read's keyword arguments. ``chain_positions`` is the
    one composite survey: roboport chain clearance, answered live by
    `roboport_placement.clear_chain_positions` and offline by driving
    `roboport_placement.clear_chain_planner` against recorded surveys.
    """

    kind: str
    position: Point | None = None
    min_point: Point | None = None
    max_point: Point | None = None
    flags: tuple[tuple[str, object], ...] = ()


class MissingReachSurvey(LookupError):
    """The pure planner needs a survey the caller did not supply."""

    def __init__(self, survey: ReachSurvey) -> None:
        super().__init__(
            f"reach plan needs survey {survey.kind} {survey.position} "
            f"{survey.min_point}..{survey.max_point}"
        )
        self.survey = survey


def drive_reach_planner(
    planner: Generator[ReachSurvey, object, object],
    answer: Callable[[ReachSurvey], object],
) -> object:
    """Run a planner, answering each survey in the order it is asked.

    A failed read is raised inside the planner at its yield, exactly where
    the inline live read used to raise, so the planner's own error handling
    (best-effort diagnostics, ValueError -> StuckError) is unchanged.
    """
    try:
        survey = next(planner)
        while True:
            try:
                value = answer(survey)
            except Exception as error:  # re-raised at the planner's yield
                survey = planner.throw(error)
            else:
                survey = planner.send(value)
    except StopIteration as done:
        return done.value


def read_reach_survey(
    client: RconClient | None, surface: str, force: str | None,
    survey: ReachSurvey,
) -> object:
    """Answer one primitive reach survey from the live world."""
    flags = dict(survey.flags)
    kind = survey.kind
    if kind == "area_clear":
        return live_base.area_clear(
            client, surface, survey.min_point, survey.max_point, **flags,
        )
    if kind == "occupied_tiles":
        return live_base.occupied_tiles(
            client, surface, survey.min_point, survey.max_point, **flags,
        )
    if kind == "occupied_tile_owners":
        return live_base.occupied_tile_owners(
            client, surface, survey.min_point, survey.max_point, **flags,
        )
    if kind == "box_has_reserved_patch":
        return resource_patches.box_has_reserved_patch(
            client, surface, survey.min_point, survey.max_point, **flags,
        )
    if kind == "resource_tiles":
        return resource_patches.resource_tiles(
            client, surface, survey.min_point, survey.max_point,
        )
    if kind == "entity_at":
        return live_base.entity_at(client, surface, survey.position)
    if kind == "pole_network_id":
        return live_base.pole_network_id(client, surface, survey.position)
    if kind == "nearest_powered_pole":
        return live_base.nearest_powered_pole(
            client, surface, force, survey.position, **flags,
        )
    if kind == "network_generation_kw":
        return live_base.network_generation_kw(
            client, surface, force, survey.position,
        )
    if kind == "transferable_items":
        return live_base.transferable_items(client, surface, force)
    raise ValueError(f"unknown reach survey kind {kind!r}")
