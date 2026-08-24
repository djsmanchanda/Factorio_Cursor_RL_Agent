# Path: planners/transport_occupancy.py
# Purpose: Typed, fail-closed occupancy for deterministic transport routing.

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping

Tile = tuple[int, int]
SurfaceDisposition = Literal["place", "reuse", "blocked"]

OCCUPANT_CATEGORIES = frozenset({
    "live_entity",
    "entity_ghost",
    "tile_ghost",
    "terrain",
    "deconstruction_order",
    "pending_plan",
    "district_reservation",
    "source_interface",
    "destination_interface",
})

_REUSABLE_BELT_CATEGORIES = frozenset({
    "live_entity",
    "entity_ghost",
    "pending_plan",
})
_DIRECTIONS = frozenset({"north", "south", "east", "west"})


@dataclass(frozen=True, slots=True)
class OccupantIdentity:
    """Stable planner ownership; force and prototype are not ownership."""

    district_id: str
    entity_id: str

    def __post_init__(self) -> None:
        if not self.district_id:
            raise ValueError("occupant district_id must be non-empty")
        if not self.entity_id:
            raise ValueError("occupant entity_id must be non-empty")


@dataclass(frozen=True, slots=True)
class Occupant:
    """One typed footprint present in a route-planning snapshot."""

    category: str
    tiles: frozenset[Tile]
    name: str | None = None
    direction: str | None = None
    underground_type: str | None = None
    identity: OccupantIdentity | None = None
    allowed_entities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.category not in OCCUPANT_CATEGORIES:
            raise ValueError(f"unknown route occupant category: {self.category!r}")
        if not self.tiles:
            raise ValueError("route occupant needs at least one tile")
        if self.direction is not None and self.direction not in _DIRECTIONS:
            raise ValueError(f"unknown route occupant direction: {self.direction!r}")
        if self.underground_type not in {None, "input", "output"}:
            raise ValueError(
                f"unknown route occupant underground type: {self.underground_type!r}"
            )
        if self.underground_type is not None and not (
            self.name and self.name.endswith("underground-belt")
        ):
            raise ValueError("only underground belts may declare underground_type")
        if self.allowed_entities and self.category != "district_reservation":
            raise ValueError(
                "allowed_entities applies only to a district reservation"
            )
        if any(
            not isinstance(tile, tuple)
            or len(tile) != 2
            or not all(isinstance(coordinate, int) for coordinate in tile)
            for tile in self.tiles
        ):
            raise ValueError("route occupant tiles must be (int, int) tuples")


@dataclass(frozen=True, slots=True)
class RouteOccupancy:
    """Immutable typed occupancy indexed by every tile in each footprint."""

    occupants: tuple[Occupant, ...] = ()
    _by_tile: Mapping[Tile, tuple[Occupant, ...]] = field(
        init=False, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        indexed: dict[Tile, list[Occupant]] = {}
        for occupant in self.occupants:
            if not isinstance(occupant, Occupant):
                raise TypeError("RouteOccupancy accepts only Occupant values")
            for tile in sorted(occupant.tiles):
                indexed.setdefault(tile, []).append(occupant)
        frozen_index = {
            tile: tuple(values) for tile, values in sorted(indexed.items())
        }
        object.__setattr__(self, "_by_tile", MappingProxyType(frozen_index))

    def at(self, tile: Tile) -> tuple[Occupant, ...]:
        return self._by_tile.get(tile, ())

    def surface_disposition(
        self,
        tile: Tile,
        *,
        district_id: str,
        belt_type: str,
        direction: str,
        interface: Literal["source", "destination"] | None = None,
    ) -> SurfaceDisposition:
        """Say whether a surface belt may be placed or exactly reused.

        An occupied tile is reusable only when it contains one unambiguous,
        identity-bearing belt owned by this district. Interface occupants are
        legal solely at their declared endpoint. Any overlapping observation
        fails closed rather than hiding a collision.
        """
        occupants = self.at(tile)
        if not occupants:
            return "place"
        physical = tuple(
            occupant for occupant in occupants
            if not (
                occupant.category == "district_reservation"
                and occupant.identity is not None
                and occupant.identity.district_id == district_id
                and belt_type in occupant.allowed_entities
            )
        )
        if not physical:
            return "place"
        if len(physical) != 1:
            return "blocked"
        occupant = physical[0]
        if occupant.identity is None or occupant.identity.district_id != district_id:
            return "blocked"
        if (
            occupant.name != belt_type
            or occupant.direction != direction
            or occupant.underground_type is not None
        ):
            return "blocked"
        if occupant.category in _REUSABLE_BELT_CATEGORIES:
            return "reuse"
        expected_interface = (
            f"{interface}_interface" if interface is not None else None
        )
        if occupant.category == expected_interface:
            return "reuse"
        return "blocked"

    def may_enter_surface(
        self,
        tile: Tile,
        *,
        district_id: str,
        belt_type: str,
        interface: Literal["source", "destination"] | None = None,
        required_direction: str | None = None,
    ) -> bool:
        """Cheap direction-aware pruning for a not-yet-selected surface tile."""
        occupants = self.at(tile)
        if not occupants:
            return True
        directions = (
            (required_direction,) if required_direction is not None
            else ("east", "south", "west", "north")
        )
        return any(
            self.surface_disposition(
                tile,
                district_id=district_id,
                belt_type=belt_type,
                direction=direction,
                interface=interface,
            ) != "blocked"
            for direction in directions
        )

    def underground_endpoint_is_clear(
        self,
        tile: Tile,
        *,
        district_id: str | None = None,
        belt_type: str | None = None,
    ) -> bool:
        """Underground entrances/exits require their complete tile to be free."""
        occupants = self.at(tile)
        if not occupants:
            return True
        return district_id is not None and all(
            occupant.category == "district_reservation"
            and occupant.identity is not None
            and occupant.identity.district_id == district_id
            and belt_type is not None
            and (
                belt_type in occupant.allowed_entities
                or belt_type.replace("transport-belt", "underground-belt")
                in occupant.allowed_entities
            )
            for occupant in occupants
        )

    def owned_underground_endpoint(
        self,
        tile: Tile,
        *,
        district_id: str,
        belt_type: str,
        direction: str,
        underground_type: Literal["input", "output"],
    ) -> bool:
        """Whether one exact district-owned underground endpoint occupies tile."""
        underground_name = (
            belt_type[:-len("transport-belt")] + "underground-belt"
            if belt_type.endswith("transport-belt") else ""
        )
        physical = tuple(
            occupant for occupant in self.at(tile)
            if occupant.category != "district_reservation"
        )
        if len(physical) != 1:
            return False
        occupant = physical[0]
        return (
            bool(underground_name)
            and occupant.identity is not None
            and occupant.identity.district_id == district_id
            and occupant.category in _REUSABLE_BELT_CATEGORIES
            and occupant.name == underground_name
            and occupant.direction == direction
            and occupant.underground_type == underground_type
        )
