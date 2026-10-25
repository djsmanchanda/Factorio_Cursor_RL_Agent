# Path: tests/test_construction_coverage.py | Purpose: Protect the real coverage owner, shared run-state identity, and Laya coverage dispatch.

import pytest

from orchestrator import construction_coverage as coverage
from orchestrator import stage_services
from training import laya_ops


@pytest.mark.parametrize("name", [
    "PowerExtensionResult", "StuckError", "reset_coverage_progress",
    "coverage_progress_revision", "extend_power", "extend_roboport_coverage",
    "ensure_logistic_coverage", "roboport_chain", "service_distance",
])
def test_stage_exports_resolve_to_real_construction_definitions(name):
    implementation = getattr(coverage, name)
    assert getattr(stage_services, name) is implementation
    assert implementation.__module__ == "orchestrator.construction_coverage"


def test_coverage_frontier_state_is_shared_and_reset_in_place():
    state = coverage._COVERAGE_PROGRESS
    assert stage_services._COVERAGE_PROGRESS is state
    stage_services.reset_coverage_progress()
    target = (150.0, 0.0)
    assert not coverage._observe_coverage_frontier(
        "nauvis", "player", "construction", target, (0.0, 0.0),
    )
    assert stage_services._observe_coverage_frontier(
        "nauvis", "player", "construction", target, (42.0, 0.0),
    )
    assert coverage.coverage_progress_revision() == stage_services.coverage_progress_revision() == 1

    coverage.reset_coverage_progress()
    assert coverage._COVERAGE_PROGRESS is stage_services._COVERAGE_PROGRESS is state
    assert not state.frontiers
    assert stage_services.coverage_progress_revision() == 0


def test_pending_power_bridges_keep_one_shared_identity():
    pending = coverage._PENDING_POWER_BRIDGES
    assert stage_services._PENDING_POWER_BRIDGES is pending
    pending[("nauvis", "player")] = 123.0
    assert stage_services._PENDING_POWER_BRIDGES[("nauvis", "player")] == 123.0
    stage_services._PENDING_POWER_BRIDGES.clear()
    assert coverage._PENDING_POWER_BRIDGES is pending
    assert not pending


def test_laya_selected_coverage_calls_the_real_owner(monkeypatch):
    session = object.__new__(laya_ops.DeterministicSession)
    session.client, session.bridge = object(), object()
    session.reference_point, session.emit = (0.0, 0.0), lambda _message: None
    calls = []

    def extend(client, bridge, surface, force, target, emit, *, purpose):
        calls.append((client, bridge, surface, force, target, emit, purpose))
        return True

    monkeypatch.setattr(coverage, "extend_roboport_coverage", extend)
    assert session._operations()["coverage"](12.5, -20.5, purpose="logistic") is True
    assert calls == [(
        session.client, session.bridge, "nauvis", "player", (12.5, -20.5), session.emit, "logistic",
    )]
