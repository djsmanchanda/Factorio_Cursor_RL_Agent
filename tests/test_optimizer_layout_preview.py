# Path: tests/test_optimizer_layout_preview.py | Purpose: Verify scoped optimizer previews never grant dispatch authority.

from dataclasses import replace
from fractions import Fraction
from pathlib import Path
import shutil
import subprocess
import json

import pytest
from jsonschema import Draft7Validator

from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from orchestrator.game_bridge import GameBridge
from orchestrator.material_reservations import MaterialReservationError
from orchestrator.optimizer_layout_preview import prepare_optimizer_layout_preview
from orchestrator.optimizer_reservations import reserve_optimizer_artifact
from tests.test_optimizer_reservations import _ledger, bound
from tests.test_silo_component_link import bundle_case


def _fund(bound, ledger):
    return reserve_optimizer_artifact(bound, ledger, episode_id="synthetic-episode", stock=dict(bound.plan.required_materials))


def test_funded_bundle_creates_scoped_request_that_denies_all_actions(bound, tmp_path):
    ledger = _ledger(tmp_path)
    _fund(bound, ledger)
    before = ledger.path.read_bytes()
    preview = prepare_optimizer_layout_preview(bound, ledger, episode_id="synthetic-episode")
    plan, authorization = preview["build_plan"], preview["authorization"]
    assert (plan["surface"], plan["force"], plan["atomic"]) == ("test-surface", "test-force", True)
    assert plan["phases"] == bound.artifact["plan"]["phases"]
    assert authorization["approved_actions"] == []
    assert authorization["denied_actions"] == [{"action": "project_more_ghosts", "reason": "not_authorized"}]
    assert authorization["scope_limits"] == {"surface": "test-surface", "force": "test-force",
                                              "max_count": len(bound.plan.required_entities)}
    assert preview["binding"] == bound.plan.binding.to_dict() and preview["execution_authorized"] is False
    assert ledger.path.read_bytes() == before
    plan["phases"][0]["actions"][0]["entity"] = "changed"
    assert bound.artifact["plan"]["phases"][0]["actions"][0]["entity"] != "changed"


def test_tagged_preview_derives_nonreconciling_actions_from_bound_plan(bound, tmp_path, monkeypatch):
    tagged = bind_artifact_to_construction_plan(
        bound.artifact, scope=bound.plan.binding.scope, project_id="silo", transaction_id="a" * 32,
        throughput_output="rocket-part", throughput_target=Fraction(1, 4), throughput_unit="items/second")
    ledger = _ledger(tmp_path)
    _fund(tagged, ledger)
    before = ledger.path.read_bytes()
    preview = prepare_optimizer_layout_preview(tagged, ledger, episode_id="synthetic-episode",
                                               include_evidence_tags=True)
    expected_tags = {"optimizer_episode": "synthetic-episode", "optimizer_project": "silo",
                     "optimizer_transaction": "a" * 32, "optimizer_plan_hash": tagged.binding_sha256}
    for phase, original_phase in zip(preview["build_plan"]["phases"], tagged.artifact["plan"]["phases"]):
        for action, original in zip(phase["actions"], original_phase["actions"]):
            assert action["optimizer_ownership"] == expected_tags
            assert {**action, "action_type": "place_ghost", "optimizer_ownership": None} == {
                **original, "optimizer_ownership": None}
            assert action["action_type"] == "place_ghost_no_reconcile"
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/build_plan.schema.json").read_text())
    Draft7Validator(schema).validate(preview["build_plan"])
    assert preview["authorization"]["approved_actions"] == []
    assert preview["execution_authorized"] is False
    assert ledger.path.read_bytes() == before
    monkeypatch.setattr("orchestrator.game_bridge.RconClient", lambda *_args, **_kwargs: object())
    bridge = GameBridge(tmp_path, poll_interval=0)
    def collect(*, command, **_kwargs):
        request = json.loads(command.split(" ", 1)[1])
        return b"", {**request, "version": "optimizer-build-evidence-v2", "source": "robot_built_entity_tags",
                     "ok": True, "tick": 100, "ownership_claimed": False, "dispatch_authorized": False,
                     "entities": []}
    monkeypatch.setattr(bridge, "_collect_correlated_catalog", collect)
    report = bridge.optimizer_build_evidence(
        surface=tagged.plan.binding.scope.surface, force=tagged.plan.binding.scope.force,
        episode_id=expected_tags["optimizer_episode"], project_id=expected_tags["optimizer_project"],
        transaction_id=expected_tags["optimizer_transaction"], plan_hash=expected_tags["optimizer_plan_hash"])
    assert report["tags"] == expected_tags and report["ownership_claimed"] is False


def test_tagged_preview_rejects_incompatible_transaction(bound, tmp_path):
    ledger = _ledger(tmp_path)
    _fund(bound, ledger)
    before = ledger.path.read_bytes()
    with pytest.raises(ValueError, match="hex transaction_id"):
        prepare_optimizer_layout_preview(bound, ledger, episode_id="synthetic-episode",
                                         include_evidence_tags=True)
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("change", ["missing", "short", "constructing", "scope", "transaction", "bill", "hold", "episode"])
def test_preview_rejects_incomplete_or_mismatched_ledger(bound, tmp_path, change):
    ledger = _ledger(tmp_path)
    if change != "missing":
        _fund(bound, ledger)
    project_id = "optimizer:silo"
    if change == "short":
        ledger.projects[project_id] = replace(ledger.projects[project_id], reserved={})
    elif change == "constructing":
        ledger.mark_constructing(project_id, dict(bound.plan.required_materials))
    elif change == "scope":
        ledger.force = "other"
    elif change == "transaction":
        modified = replace(bound.plan.binding, transaction_id="different")
        ledger.projects[project_id] = replace(ledger.projects[project_id], construction_binding=modified)
    elif change == "bill":
        required = dict(ledger.projects[project_id].required)
        required["transport-belt"] += 1
        ledger.projects[project_id] = replace(ledger.projects[project_id], required=required)
    elif change == "hold":
        ledger.projects[project_id] = replace(ledger.projects[project_id], state="supply_wait")
    episode = "other" if change == "episode" else "synthetic-episode"
    with pytest.raises(MaterialReservationError):
        prepare_optimizer_layout_preview(bound, ledger, episode_id=episode)


def test_preview_rejects_altered_bound_artifact(bound, tmp_path):
    ledger = _ledger(tmp_path)
    _fund(bound, ledger)
    other = bind_artifact_to_construction_plan(
        bound.artifact, scope=bound.plan.binding.scope, project_id="silo", transaction_id="different",
        throughput_output="rocket-part", throughput_target=Fraction(1, 4), throughput_unit="items/second")
    with pytest.raises(MaterialReservationError):
        prepare_optimizer_layout_preview(other, ledger, episode_id="synthetic-episode")
    with pytest.raises(MaterialReservationError):
        prepare_optimizer_layout_preview(replace(bound, canonical_artifact=bound.canonical_artifact + b" "),
                                         ledger, episode_id="synthetic-episode")


@pytest.mark.skipif(shutil.which("lua") is None, reason="Lua interpreter unavailable")
def test_preview_denial_stops_executor_before_world_access(bound, tmp_path):
    ledger = _ledger(tmp_path)
    _fund(bound, ledger)
    preview = prepare_optimizer_layout_preview(bound, ledger, episode_id="synthetic-episode")
    first = preview["build_plan"]["phases"][0]["actions"][0]
    scope = preview["authorization"]["scope_limits"]
    root = Path(__file__).resolve().parents[1]
    script = '''
        package.path = %s .. package.path
        accessed=0
        package.loaded.sandbox_shared={get_or_create_sandbox_surface=function()
          accessed=accessed+1; error('unexpected access') end}
        game={tick=1}; defines={direction={north=0,east=4,south=8,west=12}}
        commands={add_command=function(_, _, fn) execute=fn end}
        helpers={json_to_table=function() return payload end,
          table_to_json=function(report) result=report; return '{}' end, write_file=function() end}
        %s
        local action={action_type=%s,entity=%s,position={x=%s,y=%s}}
        local limits={surface=%s,force=%s,max_count=%s}
        local plan={surface=limits.surface,force=limits.force,atomic=true,phases={{name='test',actions={action}}}}
        payload={authorization={approved_actions={},scope_limits=limits},build_plan=plan}
        execute({parameter='{}'})
        assert(result.ok == false and string.find(result.error, 'Authorization does not permit', 1, true))
        assert(accessed == 0)
    ''' % (json.dumps(str(root / "factorio_mod/?.lua") + ";"),
           (root / "factorio_mod/layout_executor.lua").read_text(),
           json.dumps(first["action_type"]), json.dumps(first["entity"]), first["position"]["x"],
           first["position"]["y"], json.dumps(scope["surface"]), json.dumps(scope["force"]), scope["max_count"])
    result = subprocess.run([shutil.which("lua"), "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
