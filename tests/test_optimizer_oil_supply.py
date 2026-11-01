# Path: tests/test_optimizer_oil_supply.py | Purpose: Check shared oil inputs without claiming physical supply.
from dataclasses import replace
import pytest

from core.block_flow import ExistingAllocation, Material, Provenance, SharedCapacity
from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from planners.oil_block_artifact import build_oil_block_artifact
from tests.test_oil_block_artifact import oil_arguments
from tools.optimizer_oil_supply import compare_oil_input_capacity


@pytest.fixture(scope="module")
def projects(oil_arguments):
    artifact = build_oil_block_artifact(**oil_arguments)["artifact"]
    scope = ConstructionScope("synthetic", "a" * 64, "nauvis", "player")
    return tuple(bind_artifact_to_construction_plan(
        artifact, scope=scope, project_id=project, transaction_id=project,
        throughput_output="petroleum-gas", throughput_target=195, throughput_unit="fluid/second")
        for project in ("oil-a", "oil-b"))


def capacity(name, fluid, rate, allocated=()):
    return SharedCapacity(name, Material(fluid, "fluid"), rate, allocated,
                          Provenance("test", "caller declaration"))


def test_shared_crude_counts_both_projects_and_existing_commitments(projects):
    crude = capacity("crude-source", "crude-oil", 410,
                     (ExistingAllocation("other-consumer", 20, Provenance("test")),))
    assignments = {("oil-a", "crude-oil"): "crude-source", ("oil-b", "crude-oil"): "crude-source"}
    result = compare_oil_input_capacity(projects, source_assignments=assignments, capacities=[crude])
    assert result["status"] == "shortage"
    assert result["sources"][0]["proposed_per_second"] == "400"
    assert result["sources"][0]["existing_allocated_per_second"] == "20"
    assert result["sources"][0]["existing_allocations"][0]["allocation_id"] == "other-consumer"
    assert result["sources"][0]["shortage_per_second"] == "10"
    assert result["source_assignments"] == [
        {"project_id": project, "fluid": "crude-oil", "source_id": "crude-source"}
        for project in ("oil-a", "oil-b")]
    assert result["unassigned_inputs"] == [{"project_id": "oil-a", "fluid": "water"},
                                           {"project_id": "oil-b", "fluid": "water"}]
    assert result["dispatch_authorized"] is False


def test_unknown_supply_and_allocations_do_not_become_zero(projects):
    assignments = {(project, fluid): fluid for project in ("oil-a", "oil-b")
                   for fluid in ("crude-oil", "water")}
    result = compare_oil_input_capacity(projects, source_assignments=assignments,
        capacities=[capacity("crude-oil", "crude-oil", None), capacity("water", "water", 600, None)])
    assert result["status"] == "conditional"
    sources = {row["fluid"]: row for row in result["sources"]}
    assert sources["crude-oil"]["declared_rate_per_second"] is None
    assert sources["water"]["existing_allocated_per_second"] is None
    assert sources["water"]["proposed_per_second"] == "530"
    assert sources["water"]["shortage_per_second"] is None


def test_distinct_explicit_sources_can_support_projects_conditionally_on_assertions(projects):
    assignments = {(project, fluid): f"{project}:{fluid}" for project in ("oil-a", "oil-b")
                   for fluid in ("crude-oil", "water")}
    capacities = [capacity(source, fluid, 300) for (_, fluid), source in assignments.items()]
    result = compare_oil_input_capacity(projects, source_assignments=assignments, capacities=capacities)
    assert result["status"] == "conserved"
    assert result["unassigned_inputs"] == []
    assert "distinctness are caller assertions" in result["non_claims"][1]
    assert result["dispatch_authorized"] is False


def test_missing_wrong_or_unused_sources_are_rejected(projects):
    assignment = {("oil-a", "crude-oil"): "source"}
    with pytest.raises(ValueError):
        compare_oil_input_capacity(projects, source_assignments=assignment, capacities=[])
    with pytest.raises(ValueError):
        compare_oil_input_capacity(projects, source_assignments=assignment,
                                   capacities=[capacity("source", "water", 1000)])
    with pytest.raises(ValueError, match="not assigned"):
        compare_oil_input_capacity(projects, source_assignments={}, capacities=[capacity("source", "water", 1000)])
    with pytest.raises(ValueError, match="positive oil input"):
        compare_oil_input_capacity(projects, source_assignments={("oil-a", "petroleum-gas"): "source"}, capacities=[])


def test_project_duplication_scope_or_binding_drift_is_rejected(projects):
    with pytest.raises(ValueError, match="unique"):
        compare_oil_input_capacity([projects[0], projects[0]], source_assignments={}, capacities=[])
    bad = replace(projects[0], canonical_binding=projects[0].canonical_binding + b" ")
    with pytest.raises(ValueError, match="binding is inconsistent"):
        compare_oil_input_capacity([bad], source_assignments={}, capacities=[])
    plan = projects[1].plan
    other_scope = ConstructionScope("different", "a" * 64, "nauvis", "player")
    other = bind_artifact_to_construction_plan(projects[1].artifact, scope=other_scope,
        project_id="other", transaction_id="other", throughput_output=plan.throughput_output,
        throughput_target=plan.throughput_target, throughput_unit=plan.throughput_unit)
    with pytest.raises(ValueError, match="share one construction scope"):
        compare_oil_input_capacity([projects[0], other], source_assignments={}, capacities=[])
