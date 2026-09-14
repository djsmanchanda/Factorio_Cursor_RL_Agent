# Path: tests/test_transport_occupancy_snapshot.py | Purpose: Keep transient flying robots out of static route occupancy.

from orchestrator import live_base


def test_snapshot_ignores_stale_flying_robot_records_and_keeps_real_occupant(monkeypatch):
    response = (
        "live_entity|logistic-robot|north||33.000|27.000|;"
        "live_entity|construction-robot|north||33.000|27.000|;"
        "live_entity|combat-robot|north||33.000|27.000|;"
        "live_entity|transport-belt|east||34.500|27.500|34,27"
    )
    monkeypatch.setattr(live_base, "_sc", lambda *_: response)

    snapshot = live_base.transport_occupancy_snapshot(
        object(), "nauvis", (0, 0), (40, 40),
    )

    assert len(snapshot.observations) == 1
    assert snapshot.observations[0].occupant.name == "transport-belt"


def test_snapshot_query_excludes_transient_robots_before_they_reach_parser(monkeypatch):
    captured = []
    monkeypatch.setattr(live_base, "_sc", lambda _client, lua: captured.append(lua) or "")

    live_base.transport_occupancy_snapshot(object(), "nauvis", (0, 0), (1, 1))

    assert "e.type~='logistic-robot'" in captured[0]
    assert "e.type~='construction-robot'" in captured[0]
    assert "e.type~='combat-robot'" in captured[0]
