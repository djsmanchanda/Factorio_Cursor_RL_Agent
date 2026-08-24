# Path: tests/test_live_transport_occupancy.py
# Purpose: Protect bounded live transport occupancy and belt-reach telemetry.

from __future__ import annotations

import pytest

from orchestrator.live_base import (
    TelemetryError,
    belt_underground_reach,
    transport_occupancy_snapshot,
)
from planners.transport_occupancy import Occupant, OccupantIdentity


class FakeRcon:
    def __init__(self, response: str) -> None:
        self.response = response
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return self.response


def _claim(category: str, tile: tuple[int, int], entity_id: str) -> Occupant:
    return Occupant(
        category=category,
        tiles=frozenset({tile}),
        identity=OccupantIdentity("iron-district", entity_id),
    )


def test_snapshot_preserves_complete_overlapping_footprints_and_exact_facts() -> None:
    response = ";".join((
        "live_entity|pipe|east||10.500|4.500|10,4:10,5:11,4:11,5",
        "live_entity|underground-belt|east|input|12.500|4.500|12,4",
        "entity_ghost|transport-belt|east||13.500|4.500|13,4",
        "tile_ghost|landfill|||14.500|4.500|14,4",
        "terrain|water|||||15,4",
        "live_entity|stone-furnace|north||16.500|4.500|16,4:17,4",
        "deconstruction_order|stone-furnace|north||16.500|4.500|16,4:17,4",
    ))
    client = FakeRcon(response)
    owned = OccupantIdentity("iron-district", "underground-entry-1")

    snapshot = transport_occupancy_snapshot(
        client, "nauvis", (10.0, 4.0), (18.0, 6.0),
        exact_identities={
            ("live_entity", "underground-belt", 12.5, 4.5): owned,
        },
        pending_claims=(_claim("pending_plan", (18, 4), "pending-1"),),
        district_reservations=(
            _claim("district_reservation", (19, 4), "reservation-1"),
        ),
        interfaces=(
            Occupant(
                category="source_interface", tiles=frozenset({(10, 4)}),
                name="transport-belt", direction="east",
                identity=OccupantIdentity("iron-district", "source"),
            ),
        ),
    )

    assert snapshot.occupancy.at((10, 4))[0].tiles == frozenset(
        {(10, 4), (10, 5), (11, 4), (11, 5)},
    )
    assert {occupant.category for occupant in snapshot.occupancy.at((16, 4))} == {
        "live_entity", "deconstruction_order",
    }
    assert snapshot.occupancy.at((12, 4))[0].identity == owned
    assert snapshot.occupancy.at((13, 4))[0].identity is None
    assert snapshot.occupancy.at((18, 4))[0].category == "pending_plan"
    assert snapshot.occupancy.at((19, 4))[0].category == "district_reservation"
    assert any(item.underground_type == "input" for item in snapshot.observations)
    assert "area={{10.0,4.0},{18.0,6.0}}" in client.commands[0]
    assert "find_tiles_filtered{area=area" in client.commands[0]


def test_snapshot_rejects_inferred_or_malformed_live_facts() -> None:
    client = FakeRcon("live_entity|transport-belt|diagonal||1.5|2.5|1,2")

    with pytest.raises(TelemetryError, match="direction"):
        transport_occupancy_snapshot(client, "nauvis", (0, 0), (3, 3))


def test_snapshot_rejects_claims_in_the_wrong_channel() -> None:
    client = FakeRcon("")

    with pytest.raises(TelemetryError, match="pending-plan claim"):
        transport_occupancy_snapshot(
            client, "nauvis", (0, 0), (3, 3),
            pending_claims=(_claim("district_reservation", (1, 1), "wrong"),),
        )


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "0", "-2", "NONE", "wat"])
def test_belt_underground_reach_rejects_unusable_telemetry(raw: str) -> None:
    client = FakeRcon(raw)

    with pytest.raises(TelemetryError, match="underground reach.*transport-belt"):
        belt_underground_reach(client, "transport-belt")


def test_belt_underground_reach_reads_selected_tier_live() -> None:
    client = FakeRcon("7")

    assert belt_underground_reach(client, "fast-transport-belt") == 7
    assert "prototypes.entity['fast-transport-belt']" in client.commands[0]
    assert "max_underground_distance" in client.commands[0]
