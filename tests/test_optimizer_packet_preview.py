# Path: tests/test_optimizer_packet_preview.py | Purpose: Verify bounded ghost packets against partial held stock.
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from orchestrator.material_reservations import MaterialReservationError, MaterialReservationLedger
from orchestrator.optimizer_layout_preview import prepare_optimizer_packet_preview
from orchestrator.optimizer_reservations import reserve_optimizer_artifact
from planners.ghost_line_artifact import derive_ghost_artifact
from tests.test_optimizer_artifact_binding import _artifact, _scope


@pytest.fixture
def project(tmp_path):
    bound = bind_artifact_to_construction_plan(
        _artifact(), scope=_scope(), project_id="packet-test", transaction_id="a" * 32,
        throughput_output="iron-plate", throughput_target=Fraction(1, 2), throughput_unit="items/second")
    scope = bound.plan.binding.scope
    ledger = MaterialReservationLedger(tmp_path / "script-output" / "factorio_mod", episode_id="episode-1",
                                       surface=scope.surface, force=scope.force)
    return bound, ledger


def actions(plan):
    return [action for phase in plan["phases"] for action in phase["actions"]]


def test_partial_hold_produces_ordered_accounted_denied_packet(project):
    bound, ledger = project
    artifact = bound.artifact
    original = actions(artifact["plan"])
    item_map = artifact["item_mapping"]["entities"]
    stock = {item: 1 for item, _count in bound.plan.required_materials}
    reserved = reserve_optimizer_artifact(bound, ledger, episode_id="episode-1", stock=stock)
    assert reserved.state == "supply_wait"
    before = ledger.path.read_bytes()
    result = prepare_optimizer_packet_preview(bound, ledger, episode_id="episode-1", action_offset=0, max_actions=1000)
    packet = actions(result["build_plan"])
    assert result["status"] == "packet_preview" and 0 < len(packet) < len(original)
    assert result["next_action_offset"] == len(packet)
    assert result["material_bill"] == dict(Counter(item_map[action["entity"]] for action in packet))
    assert all(count <= reserved.reserved[item] for item, count in result["material_bill"].items())
    assert result["blocked_item"] == item_map[original[len(packet)]["entity"]]
    for actual, expected in zip(packet, original):
        assert actual["action_type"] == "place_ghost_no_reconcile"
        assert actual["optimizer_ownership"]["optimizer_plan_hash"] == bound.binding_sha256
        assert {**actual, "action_type": "place_ghost", "optimizer_ownership": None} == {
            **expected, "optimizer_ownership": None}
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/build_plan.schema.json").read_text())
    Draft7Validator(schema).validate(result["build_plan"])
    assert result["authorization"]["approved_actions"] == []
    assert result["authorization"]["scope_limits"]["max_count"] == len(packet)
    assert result["execution_authorized"] is False and ledger.path.read_bytes() == before


def test_cursor_cap_shortage_and_competing_reservations(project):
    bound, ledger = project
    artifact = bound.artifact
    original = actions(artifact["plan"])
    mapping = artifact["item_mapping"]["entities"]
    bill = dict(bound.plan.required_materials)
    first_item = mapping[original[0]["entity"]]
    ledger.declare("competitor", {first_item: bill[first_item]}, bill, priority=100, hold_until_producing=True)
    reserve_optimizer_artifact(bound, ledger, episode_id="episode-1", stock=bill)
    before = ledger.path.read_bytes()
    wait = prepare_optimizer_packet_preview(bound, ledger, episode_id="episode-1", action_offset=0, max_actions=10)
    assert wait["status"] == "supply_wait" and wait["blocked_item"] == first_item
    assert wait["build_plan"] is None and wait["authorization"] is None
    # A later cursor is only an assertion; it does not prove earlier work occurred.
    offset = next(i for i, action in enumerate(original) if mapping[action["entity"]] != first_item)
    packet = prepare_optimizer_packet_preview(bound, ledger, episode_id="episode-1", action_offset=offset, max_actions=1)
    assert packet["next_action_offset"] == offset + 1
    assert actions(packet["build_plan"])[0]["entity"] == original[offset]["entity"]
    end = prepare_optimizer_packet_preview(bound, ledger, episode_id="episode-1", action_offset=len(original), max_actions=1)
    assert end["status"] == "end_of_plan" and end["build_plan"] is None
    for offset, limit in ((-1, 1), (True, 1), (len(original) + 1, 1), (0, 0), (0, True)):
        with pytest.raises(ValueError):
            prepare_optimizer_packet_preview(bound, ledger, episode_id="episode-1", action_offset=offset, max_actions=limit)
    assert ledger.path.read_bytes() == before
    ledger.mark_constructing("optimizer:packet-test", bill)
    during_construction = ledger.path.read_bytes()
    with pytest.raises(MaterialReservationError, match="active held reservation"):
        prepare_optimizer_packet_preview(bound, ledger, episode_id="episode-1", action_offset=offset, max_actions=1)
    assert ledger.path.read_bytes() == during_construction


def test_different_prototypes_share_the_same_placement_item(project):
    _, ledger = project
    source = {"phases": [{"name": "containers", "actions": [
        {"action_type": "place_ghost", "entity": "wooden-chest", "position": {"x": 0.5, "y": 0.5}},
        {"action_type": "place_ghost", "entity": "iron-chest", "position": {"x": 2.5, "y": 0.5}},
    ]}]}
    artifact = derive_ghost_artifact(source, item_mapping={"provenance": "synthetic",
        "entities": {"wooden-chest": "container-kit", "iron-chest": "container-kit"}, "tiles": {}})
    bound = bind_artifact_to_construction_plan(
        artifact, scope=_scope(), project_id="shared-item", transaction_id="b" * 32,
        throughput_output="iron-plate", throughput_target=1, throughput_unit="items/second")
    reserve_optimizer_artifact(bound, ledger, episode_id="episode-1", stock={"container-kit": 1})
    packet = prepare_optimizer_packet_preview(bound, ledger, episode_id="episode-1", action_offset=0, max_actions=10)
    assert [action["entity"] for action in actions(packet["build_plan"])] == ["wooden-chest"]
    assert packet["material_bill"] == {"container-kit": 1} and packet["blocked_item"] == "container-kit"
