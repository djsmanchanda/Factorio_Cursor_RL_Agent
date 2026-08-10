# Path: planners/pipe_bridge.py
# Purpose: Deterministic, purity-safe pipe connections between real-base fluid endpoints.

from __future__ import annotations

from collections.abc import Iterable, Sequence

from planners.fluid_routing import generate_shortest_fluid_chain_link

PipeTile = tuple[int, int]

# docs/reference/factorio_mechanics.md: a continuous pipeline beyond 320x320 tiles can
# stop flowing. This bridge has one source and one destination, so its path
# length is bounded directly instead of inserting unplanned pumps.
MAX_PIPE_ROUTE_LENGTH = 320


def _pipe_tile(point: tuple[float, float], name: str) -> PipeTile:
    """Normalize a pipe tile while refusing off-grid endpoint coordinates."""
    if len(point) != 2:
        raise ValueError(f"{name} must be a two-coordinate pipe tile")
    x, y = point
    if isinstance(x, bool) or isinstance(y, bool) or int(x) != x or int(y) != y:
        raise ValueError(f"{name} must be an integer pipe tile, got {point!r}")
    return int(x), int(y)


def bridge_pipe_to_pipe(
    source_tile: tuple[float, float],
    destination_tile: tuple[float, float],
    *,
    fluid: str,
    foreign_segments: Sequence[dict] = (),
    hard_tiles: Iterable[tuple[float, float]] = (),
) -> dict:
    """Return one real-base pipe route between two surveyed pipe tiles.

    The caller provides only endpoints that are known to be valid fluid
    connectors or row-header attachments. Existing foreign fluid segments and
    real-base obstacles are delegated to the existing deterministic shortest
    router; it schema-validates the plan and rejects fluid mixing. This bridge
    intentionally does not permit tunnelable live pipes: joining or crossing an
    unknown player network is unsafe.
    """
    source = _pipe_tile(source_tile, "source_tile")
    destination = _pipe_tile(destination_tile, "destination_tile")
    if not fluid:
        raise ValueError("fluid must be non-empty")
    if source == destination:
        raise ValueError("source_tile and destination_tile must differ")

    plan = generate_shortest_fluid_chain_link(
        source,
        [destination],
        fluid,
        foreign=list(foreign_segments),
        hard_tiles=hard_tiles,
    )
    route_length = sum(
        1
        for phase in plan["phases"]
        for action in phase["actions"]
        if action["entity"] in {"pipe", "pipe-to-ground"}
    ) - 1
    if route_length > MAX_PIPE_ROUTE_LENGTH:
        raise ValueError(
            f"Pipe route is {route_length} tiles, beyond the {MAX_PIPE_ROUTE_LENGTH}-tile "
            "continuous-pipeline limit; a pump-staged design is required"
        )
    return plan
