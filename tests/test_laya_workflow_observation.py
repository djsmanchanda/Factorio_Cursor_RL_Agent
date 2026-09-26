# Path: tests/test_laya_workflow_observation.py | Purpose: Verify disposable report adaptation and measured branch lifecycle.

import pytest

from training.laya_work_graph import project_work_graph
from training.laya_workflow_observation import WorkflowObservationError, observe_workflow


def _spec(branch_id, item, minimum, requires=(), goal="factory"):
    return {
        "branch_id": branch_id,
        "parent_goal_id": goal,
        "item": item,
        "metric": "production_rate",
        "target_minimum": minimum,
        "required_sustained_ticks": 60,
        "requires": list(requires),
    }


def _report():
    return {
        "runtime": "disposable_training",
        "surface": "training/connected-001",
        "force": "training-connected-001",
        "items": {
            "iron-plate": {"stock": 30, "production_rate": 2, "production_sustained_ticks": 30},
            "iron-gear-wheel": {"stock": 4, "production_rate": None},
            "transport-belt": {"stock": 3, "production_rate": None},
            "small-electric-pole": {"stock": 2, "production_rate": None},
        },
        "committed_bills": [{"bill_id": "job-1", "requirements": {"transport-belt": 10}}],
        "reservations": [{"bill_id": "job-1", "item": "transport-belt", "amount": 2}],
    }


def _specs():
    return [
        _spec("belts", "transport-belt", 1, ("gears",)),
        _spec("gears", "iron-gear-wheel", 1, ("iron",)),
        _spec("iron", "iron-plate", 2),
        {
            "branch_id": "construction", "parent_goal_id": "coverage",
            "item": "small-electric-pole", "metric": "covered_tiles",
            "target_minimum": 100, "requires": [],
        },
    ]


def _ids(graph, category):
    return [record["branch_id"] for record in project_work_graph(graph)[category]]


def test_measured_chain_preserves_unknown_rates_and_independent_peer():
    report = _report()
    report["blockers"] = {"iron": {"reason": "ore starved", "wake_condition": "ore delivered"}}
    graph, supply = observe_workflow(report, _specs())

    assert _ids(graph, "runnable") == ["construction"]
    assert _ids(graph, "blocked") == ["belts", "gears", "iron"]
    assert supply["items"]["transport-belt"]["stock"]["accessible"] == 1
    assert supply["items"]["transport-belt"]["demand"]["committed"]["remaining"] == 8
    assert supply["items"]["iron-gear-wheel"]["production"]["observed_rate"] is None


def test_sustained_output_closes_then_changed_demand_or_unknown_reopens():
    report = _report()
    report["items"]["iron-plate"]["production_sustained_ticks"] = 60
    graph, _ = observe_workflow(report, _specs())
    assert _ids(graph, "closed") == ["iron"]
    assert "gears" in _ids(graph, "runnable")

    updated = _specs()
    updated[2]["target_minimum"] = 3
    graph, _ = observe_workflow(report, updated, graph)
    assert "iron" in _ids(graph, "runnable")
    assert "gears" in _ids(graph, "blocked")

    report["items"]["iron-plate"]["production_rate"] = None
    graph, _ = observe_workflow(report, _specs(), graph)
    assert "iron" not in _ids(graph, "closed")


def test_nonproduction_metric_requires_explicit_measured_value():
    report = _report()
    report["items"]["small-electric-pole"]["metrics"] = {
        "covered_tiles": {"value": 120, "sustained_ticks": 0},
    }
    graph, _ = observe_workflow(report, _specs())
    assert "construction" in _ids(graph, "closed")
    del report["items"]["small-electric-pole"]["metrics"]
    graph, _ = observe_workflow(report, _specs(), graph)
    assert "construction" in _ids(graph, "runnable")


@pytest.mark.parametrize("surface,force,runtime", [
    ("nauvis", "player", "disposable_training"),
    ("training/connected-001", "player", "disposable_training"),
    ("training/connected-001", "training-other-001", "disposable_training"),
    ("training/connected-001", "training-connected-001", "production"),
])
def test_rejects_non_disposable_or_mismatched_identity(surface, force, runtime):
    report = _report()
    report.update(surface=surface, force=force, runtime=runtime)
    with pytest.raises(WorkflowObservationError):
        observe_workflow(report, _specs())


def test_branch_identity_and_measurement_validation_fail_closed():
    report = _report()
    graph, _ = observe_workflow(report, _specs())
    renamed = _specs()
    renamed[0]["branch_id"] = "new-belts"
    with pytest.raises(WorkflowObservationError, match="identities"):
        observe_workflow(report, renamed, graph)

    report["items"]["iron-plate"]["production_sustained_ticks"] = -1
    with pytest.raises(WorkflowObservationError, match="sustained_ticks"):
        observe_workflow(report, _specs())

    no_window = _specs()
    del no_window[2]["required_sustained_ticks"]
    with pytest.raises(WorkflowObservationError, match="positive sustained window"):
        observe_workflow(_report(), no_window)
