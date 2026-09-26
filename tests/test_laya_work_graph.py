# Path: tests/test_laya_work_graph.py | Purpose: Verify measured branch lifecycle and dependency projection.

import json
from dataclasses import replace

import pytest

from training.laya_work_graph import (
    Blocker,
    CompletionMeasurement,
    CompletionTarget,
    WorkBranch,
    WorkGraph,
    WorkGraphError,
    project_work_graph,
    reconcile_work_graph,
)


def _ids(projection, key):
    return [record["branch_id"] for record in projection[key]]


def _factory_graph():
    return WorkGraph((
        WorkBranch("belts", "goal.transport", CompletionTarget("belt_per_s", 2, 60),
                   requires=("gears",), reserved_materials=(("iron_plate", 40),)),
        WorkBranch("gears", "goal.transport", CompletionTarget("gear_per_s", 1, 60),
                   requires=("iron",)),
        WorkBranch("iron", "goal.transport", CompletionTarget("plate_per_s", 4, 60)),
        WorkBranch("construction", "goal.coverage", CompletionTarget("covered_tiles", 100)),
    ))


def test_dependency_chain_preserves_independent_peer_and_reservations():
    graph = _factory_graph()
    projection = project_work_graph(graph)
    assert _ids(projection, "runnable") == ["construction", "iron"]
    assert _ids(projection, "blocked") == ["belts", "gears"]
    assert projection["blocked"][0]["reserved_materials"] == {"iron_plate": 40}
    assert projection["blocked"][0]["unmet_prerequisites"] == ["gears"]
    json.dumps(projection)

    graph = reconcile_work_graph(graph, {"iron": CompletionMeasurement("plate_per_s", 4, 60)})
    assert _ids(project_work_graph(graph), "runnable") == ["construction", "gears"]
    graph = reconcile_work_graph(graph, {"gears": CompletionMeasurement("gear_per_s", 1, 60)})
    assert _ids(project_work_graph(graph), "runnable") == ["belts", "construction"]


def test_blocker_requires_external_clear_not_just_wake_hint():
    graph = reconcile_work_graph(
        _factory_graph(), {}, blockers={"construction": Blocker("bots unavailable", "robots_arrived")}
    )
    projection = project_work_graph(graph)
    assert _ids(projection, "eligible") == ["construction", "iron"]
    assert _ids(projection, "runnable") == ["iron"]
    assert projection["blocked"][1]["blocker"]["wake_condition"] == "robots_arrived"
    graph = reconcile_work_graph(graph, {}, blockers={"construction": None})
    assert _ids(project_work_graph(graph), "runnable") == ["construction", "iron"]


def test_ghosts_or_unsustained_output_cannot_close_and_demand_can_reopen():
    graph = _factory_graph()
    assert _ids(project_work_graph(graph), "closed") == []  # Submitted ghosts provide no measurement.
    graph = reconcile_work_graph(graph, {"construction": CompletionMeasurement("covered_tiles", 100)})
    assert _ids(project_work_graph(graph), "closed") == ["construction"]
    graph = reconcile_work_graph(graph, {"belts": CompletionMeasurement("belt_per_s", 2, 10)})
    assert "belts" not in _ids(project_work_graph(graph), "closed")
    graph = reconcile_work_graph(graph, {"belts": CompletionMeasurement("belt_per_s", 2, 60)})
    assert "belts" in _ids(project_work_graph(graph), "closed")
    graph = reconcile_work_graph(graph, {}, targets={"belts": CompletionTarget("belt_per_s", 3, 60)})
    assert "belts" not in _ids(project_work_graph(graph), "closed")
    assert "belts" in _ids(project_work_graph(graph), "blocked")


def test_malformed_graph_and_ambiguous_completion_fail_closed():
    target = CompletionTarget("rate", 1)
    with pytest.raises(WorkGraphError, match="cyclic"):
        WorkGraph((WorkBranch("a", "goal", target, requires=("b",)),
                   WorkBranch("b", "goal", target, requires=("a",))))
    with pytest.raises(WorkGraphError, match="unknown"):
        WorkGraph((WorkBranch("a", "goal", target, requires=("missing",)),))
    with pytest.raises(WorkGraphError, match="ambiguous completion"):
        WorkBranch("a", "goal", target, status="closed")
    with pytest.raises(WorkGraphError, match="positive"):
        CompletionTarget("pending_ghosts", 0)
    with pytest.raises(WorkGraphError, match="does not match"):
        reconcile_work_graph(WorkGraph((WorkBranch("a", "goal", target),)),
                             {"a": CompletionMeasurement("pending_ghosts", 0)})
    with pytest.raises(WorkGraphError, match="invalid reservations"):
        replace(WorkBranch("a", "goal", target), reserved_materials=(("iron", -1),))
