# Path: tests/test_oil_block_artifact.py | Purpose: Exercise bounded oil balance through routed, recipe-bound ghost geometry.
from __future__ import annotations

from fractions import Fraction
import copy
import hashlib
from dataclasses import replace
import json
from pathlib import Path

import pytest

from core.bounded_recipe_balance import optimize_bounded_recipe_balance
from core.optimizer_artifact_binding import ArtifactBindingError, bind_artifact_to_construction_plan
from core.optimizer_construction_state import (
    ConstructionPhase, ConstructionReceipt, ConstructionScope, FundingAction,
    ObservationAction, OrderAction, ThroughputEvidence, reduce_construction, resume_construction,
)
from orchestrator.material_reservations import MaterialReservationLedger
from planners.oil_block_artifact import _check_capture_catalog_consistency, _check_spans, build_and_bind_oil_block, build_oil_block_artifact

CATALOG = (Path(__file__).resolve().parents[1]
           / "data/factorio-2.1.21/probes/deterministic/server/script-output/factorio_mod"
           / "recipe_catalogs/recipe_catalog_243688.json")
RECIPES = ("advanced-oil-processing", "heavy-oil-cracking", "light-oil-cracking")
ORDER = ("heavy-oil", "light-oil", "petroleum-gas", "water")


@pytest.fixture(scope="module")
def oil_arguments():
    if not CATALOG.exists():
        pytest.skip("historical recipe export is unavailable")
    catalog = json.loads(CATALOG.read_text())
    balance = optimize_bounded_recipe_balance(
        catalog, recipes={recipe: 10 for recipe in RECIPES},
        sources={"crude-oil": 300, "water": 400}, demand={"petroleum-gas": 195},
        disposals={}, objective={"recipes": {recipe: 1 for recipe in RECIPES},
                                 "sources": {"crude-oil": -1, "water": -1},
                                 "disposals": {}},
    )
    return {
        "catalog": catalog, "balance": balance,
        "machine_speeds": {recipe: Fraction(1) for recipe in RECIPES},
        "rows": {recipe: {"origin": (0, index * 60), "direction": "north", "mirror": None}
                 for index, recipe in enumerate(RECIPES)},
        "trunk_columns": {fluid: -10 - 6 * index for index, fluid in enumerate(ORDER)},
        "route_order": ORDER, "max_machines": 22,
        "item_mapping": {"provenance": "synthetic prototype mapping", "tiles": {},
                         "entities": {name: name for name in (
                             "pipe", "pipe-to-ground", "medium-electric-pole",
                             "oil-refinery", "chemical-plant")}},
    }


def test_oil_balance_routes_to_bound_recipe_aware_ghosts(oil_arguments):
    result = build_oil_block_artifact(**oil_arguments)
    facts = {fact.recipe: fact.machine_count for fact in result["machine_facts"].facts}
    assert facts == dict(zip(RECIPES, (10, 3, 9)))
    sources = {source.fluid: source for source in result["machine_facts"].fluid_sources}
    assert sources["crude-oil"].external_source_rate == 200
    assert sources["water"].external_source_rate == 265
    assert set(result["pipeline_spans"]) == set(result["ports"])
    assert all(span["width"] <= 320 and span["height"] <= 320
               for span in result["pipeline_spans"].values())
    assert {fluid: len(ports) for fluid, ports in result["ports"].items()} == {
        "heavy-oil": 2, "light-oil": 3, "petroleum-gas": 2, "water": 3, "crude-oil": 1,
    }
    artifact = result["artifact"]
    assert artifact["executable"] is False
    assert artifact["counts"]["direct_placements"] == 0
    assert artifact["declared_item_bill"]["oil-refinery"] == 10
    assert artifact["declared_item_bill"]["chemical-plant"] == 12
    bound = bind_artifact_to_construction_plan(
        artifact, scope=ConstructionScope("synthetic", "a" * 64, "synthetic", "synthetic"),
        project_id="oil", transaction_id="synthetic-oil", throughput_output="petroleum-gas",
        throughput_target=195, throughput_unit="fluid/second",
    )
    machines = [entity for entity in bound.plan.required_entities if entity.recipe]
    assert len(machines) == 22
    assert {entity.recipe for entity in machines} == set(RECIPES)
    assert bound.plan.throughput_unit == "fluid/second"
    assert any("not verified" in claim for claim in result["non_claims"])
    context = bound.artifact["planning_context"]
    assert context["trust"] == "unverified"
    rates = {entry["fluid"]: entry for entry in context["data"]["fluid_rates"]}
    assert rates["crude-oil"]["external_source_per_second"] == "200"
    assert rates["water"]["external_source_per_second"] == "265"
    assert {"fluid_sources", "fluid_transport", "fluid_measurement"} <= set(bound.artifact["pending_checks"])
    changed = copy.deepcopy(artifact)
    changed["planning_context"]["data"]["fluid_rates"][0]["external_source_per_second"] = "999"
    with pytest.raises(ArtifactBindingError):
        bind_artifact_to_construction_plan(
            changed, scope=bound.plan.binding.scope, project_id="oil", transaction_id="synthetic-oil",
            throughput_output="petroleum-gas", throughput_target=195, throughput_unit="fluid/second",
            expected_plan_hash=bound.binding_sha256,
        )


@pytest.mark.parametrize("changes", [
    {"max_machines": 21},
    {"route_order": ("heavy-oil", "light-oil", "water", "water")},
    {"trunk_columns": {"heavy-oil": -10}},
    {"rows": {}},
])
def test_incomplete_or_over_budget_candidate_is_rejected(oil_arguments, changes):
    with pytest.raises(ValueError):
        build_oil_block_artifact(**(oil_arguments | changes))


def test_unsafe_trunk_is_not_silently_rerouted(oil_arguments):
    columns = dict(oil_arguments["trunk_columns"], **{"heavy-oil": 0})
    with pytest.raises(ValueError):
        build_oil_block_artifact(**(oil_arguments | {"trunk_columns": columns}))


def test_colliding_rows_are_rejected(oil_arguments):
    rows = {name: dict(row) for name, row in oil_arguments["rows"].items()}
    rows[RECIPES[1]]["origin"] = rows[RECIPES[0]]["origin"]
    with pytest.raises(ValueError):
        build_oil_block_artifact(**(oil_arguments | {"rows": rows}))


def test_oversized_connected_pipeline_is_rejected(oil_arguments):
    rows = {name: dict(row) for name, row in oil_arguments["rows"].items()}
    rows[RECIPES[1]]["origin"] = (0, 160)
    rows[RECIPES[2]]["origin"] = (0, 330)
    with pytest.raises(ValueError, match="exceeds MAX_PIPELINE_SPAN"):
        build_oil_block_artifact(**(oil_arguments | {"rows": rows}))


def test_span_check_includes_machine_footprints_and_separates_fluids():
    segments = [{"fluid": "water", "tiles": [(0, 0), (319, 0)]},
                {"fluid": "crude-oil", "tiles": [(900, 0)]}]
    machines = {"water": {(320, 0)}, "crude-oil": {(901, 0)}}
    assert _check_spans(segments, machines) == {
        "crude-oil": {"width": 1, "height": 0}, "water": {"width": 320, "height": 0},
    }
    machines["water"].add((321, 0))
    with pytest.raises(ValueError, match="MAX_PIPELINE_SPAN"):
        _check_spans(segments, machines)


def test_missing_bill_mapping_is_rejected(oil_arguments):
    mapping = {"provenance": "incomplete", "entities": {}, "tiles": {}}
    with pytest.raises(ValueError):
        build_oil_block_artifact(**(oil_arguments | {"item_mapping": mapping}))


def test_oil_artifact_is_rejected_when_refinery_cannot_craft_recipe(oil_arguments):
    catalog = copy.deepcopy(oil_arguments["catalog"])
    machine = next(machine for machine in catalog["machines"] if machine["name"] == "oil-refinery")
    machine["categories"] = ["crafting"]
    with pytest.raises(ValueError, match="does not support recipe category"):
        build_oil_block_artifact(**{**oil_arguments, "catalog": catalog})


def _placement_capture(oil_arguments):
    names = oil_arguments["item_mapping"]["entities"]
    mods = {"base": "2.1.21"}
    report = {"kind": "runtime_catalog", "schema_version": "1.1.0", "ok": True, "tick": 42,
              "provenance": {"export_command": "export_runtime_catalog", "export_tick": 42,
                             "active_mods": mods, "active_mods_count": 1,
                             "scope": {"surface_requested": True, "surface": "training/oil",
                                       "force": "training-oil", "entity_scope": "selected_prototype_types",
                                       "entity_types": ["synthetic"]}},
              "entities": [{"name": name, "type": "synthetic", "placement": {"items": [
                  {"name": "captured-pipe" if name == "pipe" else name, "count": 1}]}}
                           for name in names]}
    raw = json.dumps(report).encode()
    return {"catalog_bytes": raw, "expected_sha256": hashlib.sha256(raw).hexdigest(),
            "surface": "training/oil", "force": "training-oil", "active_mods": mods}


def test_oil_capture_mapping_is_used_in_bound_artifact(oil_arguments):
    capture = _placement_capture(oil_arguments)
    recipe_catalog = {**oil_arguments["catalog"], "version": "1.3.0", "force": "training-oil",
                      "active_mods": capture["active_mods"], "active_mods_count": 1}
    args = {**oil_arguments, "catalog": recipe_catalog, "item_mapping": None, "placement_capture": capture}
    scope = ConstructionScope("synthetic", "a" * 64, "training/oil", "training-oil")
    result = build_and_bind_oil_block(**args, scope=scope, project_id="oil",
                                      transaction_id="oil-transaction", throughput_output="petroleum-gas")
    bound = result["bound"]
    assert bound.artifact["declared_item_bill"]["captured-pipe"] > 0
    assert "pipe" not in bound.artifact["declared_item_bill"]
    assert dict(bound.plan.required_materials) == bound.artifact["declared_item_bill"]
    assert capture["expected_sha256"] in bound.artifact["item_mapping"]["provenance"]
    assert not bound.execution_authorized


def test_oil_capture_scope_and_ambiguous_sources_are_rejected(oil_arguments):
    capture = _placement_capture(oil_arguments)
    scope = ConstructionScope("synthetic", "a" * 64, "training/other", "training-other")
    args = {**oil_arguments, "item_mapping": None, "placement_capture": capture}
    with pytest.raises(ValueError, match="scope must match"):
        build_and_bind_oil_block(**args, scope=scope, project_id="oil",
                                 transaction_id="oil-transaction", throughput_output="petroleum-gas")
    with pytest.raises(ValueError, match="exactly one"):
        build_oil_block_artifact(**{**oil_arguments, "placement_capture": capture})


@pytest.mark.parametrize("change", [
    {"version": "1.2.0"}, {"force": "player"}, {"active_mods": {"base": "other"}},
    {"active_mods_count": True}, {"active_mods_count": 2}, {"active_mods": {}},
])
def test_incompatible_recipe_and_placement_claims_are_rejected(oil_arguments, change):
    capture = _placement_capture(oil_arguments)
    catalog = {**oil_arguments["catalog"], "version": "1.3.0", "force": "training-oil",
               "active_mods": capture["active_mods"], "active_mods_count": 1, **change}
    with pytest.raises(ValueError, match="force/mod provenance mismatch"):
        _check_capture_catalog_consistency(catalog, capture)


@pytest.fixture(scope="module")
def bound_oil(oil_arguments):
    return build_and_bind_oil_block(
        **oil_arguments, scope=ConstructionScope("synthetic", "a" * 64,
                                                  "training/oil", "training-oil"),
        project_id="oil", transaction_id="oil-transaction", throughput_output="petroleum-gas",
    )


def test_oil_target_is_derived_from_net_fluid_demand(bound_oil):
    bound = bound_oil["bound"]
    assert bound.plan.throughput_output == "petroleum-gas"
    assert bound.plan.throughput_target == Fraction(195)
    assert bound.plan.throughput_unit == "fluid/second"
    assert not bound.execution_authorized


def test_oil_commission_output_rejects_external_source(bound_oil, oil_arguments, monkeypatch):
    from planners import oil_block_artifact as module

    candidate = bound_oil["candidate"].copy()
    sizing = candidate["machine_facts"]
    sources = tuple(replace(source, external_source_rate=Fraction(1))
                    if source.fluid == "petroleum-gas" else source for source in sizing.fluid_sources)
    candidate["machine_facts"] = replace(sizing, fluid_sources=sources)
    monkeypatch.setattr(module, "build_oil_block_artifact", lambda *args, **kwargs: candidate)
    with pytest.raises(ValueError, match="without external source"):
        build_and_bind_oil_block(**oil_arguments, scope=bound_oil["bound"].plan.binding.scope,
                                 project_id="oil", transaction_id="oil-transaction",
                                 throughput_output="petroleum-gas")


@pytest.mark.parametrize("output", ["crude-oil", "heavy-oil", "missing", ""])
def test_oil_commission_output_rejects_non_demand(bound_oil, oil_arguments, monkeypatch, output):
    from planners import oil_block_artifact as module

    monkeypatch.setattr(module, "build_oil_block_artifact", lambda *args, **kwargs: bound_oil["candidate"])
    with pytest.raises(ValueError, match="throughput_output|commissioning output"):
        build_and_bind_oil_block(**oil_arguments, scope=bound_oil["bound"].plan.binding.scope,
                                 project_id="oil", transaction_id="oil-transaction",
                                 throughput_output=output)


def test_oil_replay_requires_recipe_and_measured_output(bound_oil, tmp_path):
    plan = bound_oil["bound"].plan
    bill = dict(plan.required_materials)
    ledger = MaterialReservationLedger(tmp_path / "script-output", episode_id="oil-synthetic",
                                       surface=plan.binding.scope.surface, force=plan.binding.scope.force)
    ledger.declare("oil", bill, bill, target_item="petroleum-gas", priority=100,
                   hold_until_producing=True)
    ledger.mark_constructing("oil", bill)
    assert ledger.projects["oil"].reserved == bill
    machine = next(entity for entity in plan.required_entities if entity.recipe)
    wrong = tuple(replace(entity, recipe="wrong-recipe") if entity == machine else entity
                  for entity in plan.required_entities)
    binding = plan.binding
    def receipt(name, sequence, action):
        return ConstructionReceipt(name, sequence, binding, action)
    prefix = (
        receipt("order", 1, OrderAction(plan.required_entities)),
        receipt("fund", 2, FundingAction(bill)),
        receipt("wrong-recipe", 3, ObservationAction(wrong, complete=True, transaction_owned=True)),
    )
    partial = reduce_construction(plan, prefix)
    assert partial.phase is ConstructionPhase.PARTIAL
    complete = receipt("complete", 4, ObservationAction(plan.required_entities, complete=True,
                                                         transaction_owned=True))
    built = resume_construction(plan, partial, (complete,))
    assert built.phase is ConstructionPhase.BUILT
    def measure(name, sequence, count):
        return receipt(name, sequence, ThroughputEvidence(
            name, binding, "petroleum-gas", "fluid/second", "synthetic-consumer",
            0, 60, 0, count,
        ))
    below = resume_construction(plan, built, (measure("below", 5, 194),))
    assert below.phase is ConstructionPhase.BUILT
    commissioned = resume_construction(plan, below, (measure("target", 6, 195),))
    assert commissioned.phase is ConstructionPhase.COMMISSIONED
    assert commissioned.throughput_fact.rate == 195
    assert dict(commissioned.held_reservations) == bill
    assert ledger.projects["oil"].reserved == bill
