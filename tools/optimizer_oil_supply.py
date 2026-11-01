# Path: tools/optimizer_oil_supply.py | Purpose: Account for shared external fluid demand across bound oil projects.
"""Offline rate accounting; neither a supply measurement nor dispatch authority."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from fractions import Fraction

from core.block_flow import Block, GlobalBalance, Material, Port, Provenance, SharedCapacity, check_block_flow
from core.optimizer_artifact_binding import BoundArtifactPlan, bind_artifact_to_construction_plan


def compare_oil_input_capacity(bound_projects, *, source_assignments, capacities):
    """Compare solved oil input rates with caller-declared shared sources.

    ``source_assignments`` maps ``(project_id, fluid_name)`` to a source ID.
    Missing assignments, measurements or allocations stay conditional. All
    inputs, source identities and capacities are assertions: this does not
    verify a physical connection, observe supply, reserve flow or authorize a
    project. Rates are the pinned solver operating point, not peak machine use.
    """
    if (not isinstance(bound_projects, Sequence) or isinstance(bound_projects, (str, bytes))
            or not bound_projects or not isinstance(source_assignments, Mapping)
            or not isinstance(capacities, Sequence) or isinstance(capacities, (str, bytes))):
        raise ValueError("projects, source assignments and capacity facts are required")
    if not all(isinstance(fact, SharedCapacity) for fact in capacities):
        raise ValueError("capacity facts must be SharedCapacity values")
    if any(not isinstance(key, tuple) or len(key) != 2
           or any(not isinstance(part, str) or not part for part in key)
           or not isinstance(source, str) or not source for key, source in source_assignments.items()):
        raise ValueError("source assignments require (project, fluid) keys and source IDs")

    projects = {}
    common_scope = None
    for bound in bound_projects:
        if not isinstance(bound, BoundArtifactPlan):
            raise ValueError("each project must be a bound artifact")
        plan, binding = bound.plan, bound.plan.binding
        rebound = bind_artifact_to_construction_plan(
            bound.artifact, scope=binding.scope, project_id=binding.project_id,
            transaction_id=binding.transaction_id, throughput_output=plan.throughput_output,
            throughput_target=plan.throughput_target, throughput_unit=plan.throughput_unit,
            expected_artifact_sha256=bound.artifact_sha256, expected_plan_hash=bound.binding_sha256)
        if rebound.plan != plan or rebound.canonical_binding != bound.canonical_binding:
            raise ValueError("oil project binding is inconsistent")
        if binding.project_id in projects:
            raise ValueError("project IDs must be unique")
        if common_scope is None:
            common_scope = binding.scope
        elif binding.scope != common_scope:
            raise ValueError("oil projects must share one construction scope")
        envelope = bound.artifact.get("planning_context")
        context = envelope.get("data") if isinstance(envelope, dict) else None
        if not isinstance(context, dict) or context.get("kind") != "bounded-oil-block-v1":
            raise ValueError("project is not a bounded oil artifact")
        rates = context.get("fluid_rates")
        if not isinstance(rates, list) or not rates:
            raise ValueError("oil artifact has no fluid rates")
        inputs = {}
        for row in rates:
            if not isinstance(row, dict):
                raise ValueError("oil fluid rate is invalid")
            fluid, rate = row.get("fluid"), row.get("external_source_per_second")
            if (not isinstance(fluid, str) or not fluid or fluid in inputs
                    or not isinstance(rate, str) or not rate or len(rate) > 128):
                raise ValueError("oil fluid source is invalid or duplicated")
            try:
                rate = Fraction(rate)
            except (ValueError, ZeroDivisionError) as error:
                raise ValueError("oil fluid source rate is invalid") from error
            if rate < 0:
                raise ValueError("oil fluid source rate must be nonnegative")
            inputs[fluid] = rate
        projects[binding.project_id] = (bound, inputs)

    actual_inputs = {(project, fluid) for project, (_, rates) in projects.items()
                     for fluid, rate in rates.items() if rate > 0}
    if set(source_assignments) - actual_inputs:
        raise ValueError("source assignment does not name a positive oil input")
    if not actual_inputs:
        raise ValueError("oil projects have no positive external fluid demand")
    used_sources = set(source_assignments.values())
    if any(capacity.source_id not in used_sources for capacity in capacities):
        raise ValueError("capacity fact is not assigned to a project input")

    provenance = Provenance("bound-oil-solver", "pinned model operating point, not measured demand")
    totals = defaultdict(Fraction)
    blocks = []
    for index, (project, (_, rates)) in enumerate(sorted(projects.items())):
        ports = []
        for fluid, rate in sorted(rates.items()):
            if rate == 0:
                continue
            material = Material(fluid, "fluid")
            totals[material] -= rate
            ports.append(Port(f"project-{index}:fluid:{fluid}", f"project-{index}", material,
                              "input", rate, None, source_assignments.get((project, fluid))))
        blocks.append(Block(f"project-{index}", tuple(ports), provenance))
    result = check_block_flow(blocks, global_balance=GlobalBalance.from_mapping(totals, provenance=provenance),
                              capacities=capacities)
    return {
        "version": "optimizer-oil-input-capacity-v1", "scope": common_scope.to_dict(),
        "status": result.status.value,
        "projects": [{"project_id": project, "plan_hash": bound.binding_sha256,
                      "inputs_per_second": {fluid: str(rate) for fluid, rate in sorted(rates.items()) if rate > 0}}
                     for project, (bound, rates) in sorted(projects.items())],
        "source_assignments": [{"project_id": project, "fluid": fluid, "source_id": source}
                               for (project, fluid), source in sorted(source_assignments.items())],
        "sources": [{"source_id": source.source_id, "fluid": source.material.name,
                     "declared_rate_per_second": None if source.measured_rate is None else str(source.measured_rate),
                     "existing_allocated_per_second": None if source.existing_total is None else str(source.existing_total),
                     "existing_allocations": None if source.existing_allocations is None else [
                         {"allocation_id": allocation.allocation_id, "rate_per_second": str(allocation.rate),
                          "provenance": {"source": allocation.provenance.source, "detail": allocation.provenance.detail}}
                         for allocation in source.existing_allocations],
                     "proposed_per_second": str(source.proposed_demand),
                     "shortage_per_second": None if source.shortage is None else str(source.shortage),
                     "minimum_shortage_per_second": str(source.minimum_shortage), "status": source.status.value,
                     "provenance": {"source": source.provenance.source, "detail": source.provenance.detail}}
                    for source in result.capacities],
        "unassigned_inputs": [{"project_id": project, "fluid": fluid} for project, fluid in sorted(actual_inputs)
                              if (project, fluid) not in source_assignments],
        "dispatch_authorized": False,
        "non_claims": ["solver operating points are not peak machine demand or observed throughput",
                       "source IDs, rates, allocations and distinctness are caller assertions",
                       "no runtime identity, physical connection, reservation or dispatch is established"],
    }
