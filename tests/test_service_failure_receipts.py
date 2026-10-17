# Path: tests/test_service_failure_receipts.py | Purpose: Preserve route blockers through the Laya receipt boundary without weakening collision safety.

import pytest

from orchestrator import stage_services as services
from training.laya_ops import DeterministicSession

pytestmark = [pytest.mark.deterministic, pytest.mark.fast]


def test_unroutable_power_keeps_typed_geometry_evidence_and_submits_nothing(monkeypatch):
    monkeypatch.setattr(services.live_base, "pole_network_id", lambda *_a: 3)
    monkeypatch.setattr(services.live_base, "nearest_powered_pole",
                        lambda *_a, **_k: ((0.5, 0.5), "medium-electric-pole"))
    monkeypatch.setattr(services.live_base, "entity_at", lambda *_a: {"name": "medium-electric-pole"})
    monkeypatch.setattr(services.live_base, "occupied_tiles", lambda *_a, **_k: {(5, 5)})
    routes = []

    def no_route(source, endpoint, spacing, blocked, **kwargs):
        routes.append((source, endpoint, set(blocked)))
        raise ValueError("no legal hop around occupied corridor")

    monkeypatch.setattr(services, "_power_bridge_hops", no_route)
    monkeypatch.setattr(services, "_submit", lambda *_a, **_k: pytest.fail("blocked route must not submit"))
    session = DeterministicSession.__new__(DeterministicSession)
    monkeypatch.setattr(session, "_maintain_mall", lambda: None)
    monkeypatch.setattr(session, "_operations", lambda: {
        "coverage": lambda **_k: services.extend_power(
            object(), object(), "nauvis", "player", (40.5, 0.5), lambda _: None,
            reserved_tiles={(4, 4)}),
    })
    receipt = session.run("coverage", x=40.5, y=0.5)
    assert receipt["ok"] is False
    assert receipt["code"] == "power_route_unavailable"
    assert receipt["classification"] == "bug" and receipt["state"] == "failed"
    assert receipt["details"] == {
        "surface": "nauvis", "force": "player", "consumer_position": [40.5, 0.5],
        "source_position": [0.5, 0.5], "endpoint": [40.5, 0.5],
        "bridge_pole": "medium-electric-pole", "blocked_tiles": 2,
        "reason": "no legal hop around occupied corridor",
    }
    assert routes == [((0.5, 0.5), (40.5, 0.5), {(5, 5), (4, 4)})]
