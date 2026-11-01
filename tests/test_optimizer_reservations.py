# Path: tests/test_optimizer_reservations.py | Purpose: Check bound optimizer material reservations and recovery.

from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from orchestrator.material_reservations import MaterialReservationError, MaterialReservationLedger
from orchestrator.optimizer_reservations import reserve_optimizer_artifact
from planners.silo_component_link import build_silo_component_bundle
from tests.test_silo_component_link import bundle_case


@pytest.fixture
def bound(bundle_case):
    artifact = build_silo_component_bundle(**bundle_case)
    scope = ConstructionScope("synthetic-instance", "a" * 64, "test-surface", "test-force")
    return bind_artifact_to_construction_plan(
        artifact, scope=scope, project_id="silo", transaction_id="transaction-1",
        throughput_output="rocket-part", throughput_target=Fraction(1, 4), throughput_unit="items/second")


def _ledger(tmp_path):
    return MaterialReservationLedger(tmp_path / "script-output" / "factorio_mod", episode_id="synthetic-episode",
                                     surface="test-surface", force="test-force")


def test_bundle_bill_reserves_and_reloads_without_dispatch(bound, tmp_path):
    ledger = _ledger(tmp_path)
    bill = dict(bound.plan.required_materials)
    short_stock = dict(bill)
    short_stock["transport-belt"] = 0
    first = reserve_optimizer_artifact(bound, ledger, episode_id="synthetic-episode", stock=short_stock)
    assert first.state == "supply_wait" and first.hold_until_producing
    assert first.construction_binding == bound.plan.binding and first.required == bill
    funded = reserve_optimizer_artifact(bound, _ledger(tmp_path), episode_id="synthetic-episode", stock=bill)
    assert funded.state == "ready" and funded.reserved == bill
    reloaded = _ledger(tmp_path)
    reloaded.mark_constructing(funded.project_id, bill)
    competitor = reloaded.declare("competitor", {"transport-belt": 1}, bill, hold_until_producing=True)
    assert competitor.state == "supply_wait" and not competitor.reserved
    assert _ledger(tmp_path).projects[funded.project_id].construction_binding == bound.plan.binding
    schema = json.loads(Path("schemas/material_reservation_ledger.schema.json").read_text())
    payload = json.loads(reloaded.path.read_text())
    assert payload["version"] == 2
    Draft7Validator(schema).validate(payload)
    assert bound.execution_authorized is False


def test_reused_project_rejects_transaction_plan_and_legacy_changes(bound, tmp_path):
    ledger = _ledger(tmp_path)
    bill = dict(bound.plan.required_materials)
    project = reserve_optimizer_artifact(bound, ledger, episode_id="synthetic-episode", stock=bill)
    before = ledger.path.read_bytes()
    other = bind_artifact_to_construction_plan(
        bound.artifact, scope=bound.plan.binding.scope, project_id="silo", transaction_id="other",
        throughput_output="rocket-part", throughput_target=Fraction(1, 4), throughput_unit="items/second")
    with pytest.raises(MaterialReservationError, match="identity, bill, or state"):
        reserve_optimizer_artifact(other, ledger, episode_id="synthetic-episode", stock=bill)
    with pytest.raises(MaterialReservationError, match="requires a construction binding"):
        ledger.declare(project.project_id, bill, bill)
    for release in (ledger.complete, ledger.fail):
        with pytest.raises(MaterialReservationError, match="verified release path"):
            release(project.project_id)
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("change", ["scope", "episode", "artifact", "plan"])
def test_mismatched_or_altered_binding_cannot_change_ledger(bound, tmp_path, change):
    ledger = _ledger(tmp_path)
    bill = dict(bound.plan.required_materials)
    reserve_optimizer_artifact(bound, ledger, episode_id="synthetic-episode", stock=bill)
    before = ledger.path.read_bytes()
    candidate, episode = bound, "synthetic-episode"
    if change == "scope":
        scope = replace(bound.plan.binding.scope, force="other")
        candidate = bind_artifact_to_construction_plan(
            bound.artifact, scope=scope, project_id="silo", transaction_id="transaction-1",
            throughput_output="rocket-part", throughput_target=Fraction(1, 4), throughput_unit="items/second")
    elif change == "episode":
        episode = "other"
    elif change == "artifact":
        candidate = replace(bound, canonical_artifact=bound.canonical_artifact + b" ")
    else:
        candidate = replace(bound, plan=replace(bound.plan, required_materials={"fake": 1}))
    with pytest.raises(MaterialReservationError):
        reserve_optimizer_artifact(candidate, ledger, episode_id=episode, stock=bill)
    assert ledger.path.read_bytes() == before


def test_persisted_scope_or_hold_tampering_fails_closed(bound, tmp_path):
    ledger = _ledger(tmp_path)
    reserve_optimizer_artifact(bound, ledger, episode_id="synthetic-episode", stock=dict(bound.plan.required_materials))
    baseline = json.loads(ledger.path.read_text())
    for field in ("scope", "hold", "binding"):
        payload = json.loads(json.dumps(baseline))
        project = payload["projects"][0]
        if field == "scope":
            project["construction_binding"]["scope"]["force"] = "other"
        elif field == "hold":
            project["hold_until_producing"] = False
        else:
            project["construction_binding"] = None
        ledger.path.write_text(json.dumps(payload))
        with pytest.raises(MaterialReservationError):
            _ledger(tmp_path)


def test_legacy_ledger_upgrades_only_when_bound_and_remains_version_two(bound, tmp_path):
    ledger = _ledger(tmp_path)
    ledger.declare("legacy", {"pipe": 1}, {"pipe": 1})
    assert json.loads(ledger.path.read_text())["version"] == 1
    ledger = _ledger(tmp_path)
    bill = dict(bound.plan.required_materials)
    bill["pipe"] = bill.get("pipe", 0) + 1
    reserve_optimizer_artifact(bound, ledger, episode_id="synthetic-episode", stock=bill)
    assert json.loads(ledger.path.read_text())["version"] == 2
    ledger = _ledger(tmp_path)
    ledger.declare("legacy", {"pipe": 1}, bill)
    assert json.loads(ledger.path.read_text())["version"] == 2


@pytest.mark.parametrize("mutation", ["version_one", "remove_binding", "bool_version"])
def test_invalid_bound_ledger_versions_fail_closed(bound, tmp_path, mutation):
    ledger = _ledger(tmp_path)
    reserve_optimizer_artifact(bound, ledger, episode_id="synthetic-episode", stock=dict(bound.plan.required_materials))
    payload = json.loads(ledger.path.read_text())
    if mutation == "version_one":
        payload["version"] = 1
    elif mutation == "remove_binding":
        del payload["projects"][0]["construction_binding"]
    else:
        payload["version"] = True
    ledger.path.write_text(json.dumps(payload))
    with pytest.raises(MaterialReservationError):
        _ledger(tmp_path)
    schema = json.loads(Path("schemas/material_reservation_ledger.schema.json").read_text())
    assert not Draft7Validator(schema).is_valid(payload)


def test_reserved_optimizer_namespace_fails_before_mutating_existing_ledger(bound, tmp_path):
    ledger = _ledger(tmp_path)
    ledger.declare("optimizer:legacy", {"pipe": 1}, {"pipe": 1})
    before = ledger.path.read_bytes()
    projects, revision = dict(ledger.projects), ledger.revision
    with pytest.raises(MaterialReservationError, match="missing its construction binding"):
        reserve_optimizer_artifact(bound, ledger, episode_id="synthetic-episode", stock=dict(bound.plan.required_materials))
    assert ledger.path.read_bytes() == before
    assert ledger.projects == projects and ledger.revision == revision


def test_version_two_rejects_one_stripped_binding_among_multiple(bound, tmp_path):
    ledger = _ledger(tmp_path)
    stock = {item: count * 2 for item, count in bound.plan.required_materials}
    reserve_optimizer_artifact(bound, ledger, episode_id="synthetic-episode", stock=stock)
    other = bind_artifact_to_construction_plan(
        bound.artifact, scope=bound.plan.binding.scope, project_id="silo-2", transaction_id="transaction-2",
        throughput_output="rocket-part", throughput_target=Fraction(1, 4), throughput_unit="items/second")
    reserve_optimizer_artifact(other, ledger, episode_id="synthetic-episode", stock=stock)
    payload = json.loads(ledger.path.read_text())
    del payload["projects"][0]["construction_binding"]
    ledger.path.write_text(json.dumps(payload))
    with pytest.raises(MaterialReservationError, match="missing its construction binding"):
        _ledger(tmp_path)
    schema = json.loads(Path("schemas/material_reservation_ledger.schema.json").read_text())
    assert not Draft7Validator(schema).is_valid(payload)
