# Path: tests/test_optimizer_submission_preview.py | Purpose: Verify optimizer ghost and deferred-configuration staging.
from fractions import Fraction
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from orchestrator.game_bridge import LAYOUT_REQUEST_MAX_BYTES, GameBridge
from orchestrator.material_reservations import MaterialReservationLedger
from orchestrator.material_reservations import MaterialReservationError
from orchestrator.optimizer_reservations import reserve_optimizer_artifact
from planners.oil_block_artifact import build_oil_block_artifact
from planners.plan_validation import actions
from tests.test_oil_block_artifact import oil_arguments  # noqa: F401 - module fixture
from tests.test_optimizer_reservations import _ledger, bound
from tests.test_silo_component_link import bundle_case
from tools.optimizer_submission_preview import prepare_saved_world_optimizer_submission
from training.laya_saved_world import CONFIGURATION_FIELDS, configuration_actions


def staged(bound, tmp_path):
    bound = bind_artifact_to_construction_plan(
        bound.artifact, scope=bound.plan.binding.scope, project_id="silo", transaction_id="a" * 32,
        throughput_output="rocket-part", throughput_target=Fraction(1, 4), throughput_unit="items/second")
    ledger = _ledger(tmp_path)
    reserve_optimizer_artifact(bound, ledger, episode_id="synthetic-episode", stock=dict(bound.plan.required_materials))
    return bound, ledger


def test_preview_tags_all_ghosts_and_defers_settings_without_dispatch(bound, tmp_path):
    bound, ledger = staged(bound, tmp_path)
    before = ledger.path.read_bytes()
    preview = prepare_saved_world_optimizer_submission(bound, ledger, episode_id="synthetic-episode")
    expected_tags = {"optimizer_episode": "synthetic-episode", "optimizer_project": "silo",
                     "optimizer_transaction": "a" * 32, "optimizer_plan_hash": bound.binding_sha256}
    original = list(actions(bound.artifact["plan"]))
    ghosts = list(actions(preview["build_plan"]))
    assert len(ghosts) == len(original) == len(bound.plan.required_entities)
    assert all(ghost["optimizer_ownership"] == expected_tags for ghost in ghosts)
    for ghost, source in zip(ghosts, original):
        assert ghost == {**{k: v for k, v in source.items() if k not in CONFIGURATION_FIELDS},
                         "action_type": "place_ghost_no_reconcile", "optimizer_ownership": expected_tags}
    expected = configuration_actions(bound.artifact["plan"])
    assert expected
    assert list(actions(preview["configuration_plan"])) == expected
    assert preview["authorization"]["approved_actions"] == []
    assert preview["execution_authorized"] is False
    assert ledger.path.read_bytes() == before
    ghosts[0]["optimizer_ownership"]["optimizer_project"] = "changed"
    assert bound.artifact["plan"]["phases"][0]["actions"][0].get("optimizer_ownership") is None


def test_preview_rejects_unfunded_project(bound, tmp_path):
    bound, ledger = staged(bound, tmp_path)
    ledger.mark_constructing("optimizer:silo", dict(bound.plan.required_materials))
    with pytest.raises(MaterialReservationError, match="fully funded"):
        prepare_saved_world_optimizer_submission(bound, ledger, episode_id="synthetic-episode")


def test_preview_rejects_artifact_settings_the_saved_world_cannot_apply(bound, tmp_path):
    artifact = bound.artifact
    next(iter(actions(artifact["plan"])))["inventory_limit"] = {"name": "iron-plate", "count": 1,
        "minimum_stacks_by_group": {"production": 1, "logistics": 1, "intermediate-products": 1}}
    modified = bind_artifact_to_construction_plan(
        artifact, scope=bound.plan.binding.scope, project_id="silo", transaction_id="a" * 32,
        throughput_output="rocket-part", throughput_target=Fraction(1, 4), throughput_unit="items/second")
    ledger = _ledger(tmp_path)
    reserve_optimizer_artifact(modified, ledger, episode_id="synthetic-episode", stock=dict(modified.plan.required_materials))
    before = ledger.path.read_bytes()
    with pytest.raises(ValueError, match="unsupported"):
        prepare_saved_world_optimizer_submission(modified, ledger, episode_id="synthetic-episode")
    assert ledger.path.read_bytes() == before


ROOT = Path(__file__).resolve().parents[1]
OIL_SCOPE = ConstructionScope("synthetic", "a" * 64, "training/oil", "training-oil")


@pytest.fixture(scope="module")
def oil_submissions(oil_arguments, tmp_path_factory):
    """The real 1,257-ghost synthetic oil block, funded and staged both ways."""
    oil = bind_artifact_to_construction_plan(
        build_oil_block_artifact(**oil_arguments)["artifact"], scope=OIL_SCOPE, project_id="oil",
        transaction_id="a" * 32, throughput_output="petroleum-gas", throughput_target=195,
        throughput_unit="fluid/second")
    ledger = MaterialReservationLedger(tmp_path_factory.mktemp("oil-ledger"), episode_id="oil-episode",
                                       surface="training/oil", force="training-oil")
    reserve_optimizer_artifact(oil, ledger, episode_id="oil-episode", stock=dict(oil.plan.required_materials))
    before = ledger.path.read_bytes()
    expanded = prepare_saved_world_optimizer_submission(oil, ledger, episode_id="oil-episode")
    compact = prepare_saved_world_optimizer_submission(oil, ledger, episode_id="oil-episode", compact_tags=True)
    assert ledger.path.read_bytes() == before
    return oil, expanded, compact


def test_compact_oil_ghosts_match_expanded_tags_and_deferred_configuration(oil_submissions):
    oil, expanded, compact = oil_submissions
    tags = {"optimizer_episode": "oil-episode", "optimizer_project": "oil",
            "optimizer_transaction": "a" * 32, "optimizer_plan_hash": oil.binding_sha256}
    wide, narrow = list(actions(expanded["build_plan"])), list(actions(compact["build_plan"]))
    assert len(wide) == len(narrow) == 1257
    assert compact["build_plan"]["optimizer_ownership"] == tags
    assert "optimizer_ownership" not in expanded["build_plan"]
    assert all(action["action_type"] == "place_ghost_no_reconcile" and "optimizer_ownership" not in action
               for action in narrow)
    assert [{**action, "optimizer_ownership": tags} for action in narrow] == wide
    strip = lambda plan: {key: value for key, value in plan.items() if key not in {"phases", "optimizer_ownership"}}
    assert strip(compact["build_plan"]) == strip(expanded["build_plan"]) == {
        "surface": "training/oil", "force": "training-oil", "atomic": True}
    assert ([phase["name"] for phase in compact["build_plan"]["phases"]]
            == [phase["name"] for phase in expanded["build_plan"]["phases"]])
    assert compact["configuration_plan"] == expanded["configuration_plan"]
    assert len(list(actions(compact["configuration_plan"]))) == 22
    for preview in (expanded, compact):
        assert preview["execution_authorized"] is False
        assert preview["authorization"]["approved_actions"] == []


def test_only_compact_oil_request_fits_the_correlated_size_limit(oil_submissions):
    _oil, expanded, compact = oil_submissions
    authorization = compact["authorization"]
    with pytest.raises(ValueError, match="size limit") as error:
        GameBridge._layout_receipt_request(authorization, expanded["build_plan"], "b" * 32)
    expanded_bytes = int(re.search(r": (\d+) > ", str(error.value)).group(1))
    body, _digest, expected, _validator = GameBridge._layout_receipt_request(
        authorization, compact["build_plan"], "b" * 32)
    compact_bytes = len(body.encode("ascii"))
    assert compact_bytes < LAYOUT_REQUEST_MAX_BYTES < expanded_bytes
    assert compact_bytes * 3 < expanded_bytes
    sent = json.loads(body)
    assert sent["optimizer_tag_protocol"] == "optimizer-shared-tags-v1"
    assert sent["build_plan"] == compact["build_plan"] and expected == 1257


def _lua(value):
    """Render decoded JSON as a Lua literal, standing in for helpers.json_to_table."""
    if isinstance(value, dict):
        return "{" + ",".join(f"[{_lua(key)}]={_lua(item)}" for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "{" + ",".join(_lua(item) for item in value) + "}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    assert isinstance(value, str) and value.isascii() and "\n" not in value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


ORIGINAL_ENVELOPE_CHECK = '''  if (shared or protocol ~= nil) and
      (not correlated or not shared or protocol ~= "optimizer-shared-tags-v1") then'''
ORIGINAL_ALLOWED_KEY = ' and key ~= "optimizer_tag_protocol"'


def _run_oil_stub(source, body, tags):
    stub = f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path\n' + '''
        storage={}; accessed=0; created=0; tagged=0
        local surface={name='training/oil',find_entities_filtered=function() return {} end,
          can_place_entity=function() return true end,
          create_entity=function(p)
            created=created+1
            local count=0; for _ in pairs(p.tags or {}) do count=count+1 end
            if p.name=='entity-ghost' and count==4 and p.tags.optimizer_episode==expected.optimizer_episode
              and p.tags.optimizer_project==expected.optimizer_project
              and p.tags.optimizer_transaction==expected.optimizer_transaction
              and p.tags.optimizer_plan_hash==expected.optimizer_plan_hash then tagged=tagged+1 end
            return {valid=true,direction=p.direction,ghost_name=p.inner_name,name='entity-ghost'} end}
        local force={name='training-oil'}
        package.loaded.sandbox_shared={
          get_or_create_sandbox_surface=function() accessed=accessed+1; return surface end,
          get_or_create_planner_force=function() accessed=accessed+1; return force end,
          find_exact_entity=function() return nil end}
        local function box(r) return {collision_box={{-r,-r},{r,r}}} end
        prototypes={entity={pipe=box(0.29),['pipe-to-ground']=box(0.29),['oil-refinery']=box(2.4),
          ['chemical-plant']=box(1.2),['medium-electric-pole']=box(0.15)}}
        prototypes.entity['medium-electric-pole'].type='electric-pole'
        prototypes.entity['medium-electric-pole'].get_max_wire_distance=function() return 9 end
        defines={direction={north=0,east=4,south=8,west=12},build_check_type={manual=0}}
        game={tick=9,print=function(value) last_print=value end}
        commands={add_command=function(name, _, fn) if name=='build_layout_plan' then execute=fn end end}
        helpers={json_to_table=function() return payload end,
          table_to_json=function(value) report=value; return '{}' end,write_file=function() end}
    ''' + source + f'''
        expected={_lua(tags)}
        payload={_lua(json.loads(body))}
        execute({{parameter=[==[{body}]==]}})
        if report == nil then
          print("REJECTED " .. accessed .. " " .. created .. " " .. tostring(last_print))
        else
          print(string.format('{{"receipt_version":"%s","request_id":"%s","request_digest":"%s",'
            .. '"ok":%s,"tick":%d,"attempted_placements":%d,"succeeded_placements":%d,'
            .. '"already_present_placements":%d,"failed_placements":%d,"failures":%d,"created":%d,"tagged":%d,'
            .. '"accessed":%d}}', report.receipt_version, report.request_id, report.request_digest, tostring(report.ok), report.tick, report.attempted_placements or -1,
            report.succeeded_placements or -1, report.already_present_placements or -1,
            report.failed_placements or -1, #(report.placement_failures or {{}}), created, tagged, accessed))
        end
    '''
    result = subprocess.run([shutil.which("lua"), "-"], input=stub, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(shutil.which("lua") is None, reason="Lua interpreter unavailable")
def test_lua_stub_applies_shared_tags_to_every_real_oil_ghost(oil_submissions):
    oil, expanded, compact = oil_submissions
    tags = compact["build_plan"]["optimizer_ownership"]
    # Stub-only approval: the preview itself denies every action.
    authorization = {**compact["authorization"], "approved_actions": ["project_more_ghosts"]}
    request_id = "c" * 32
    body, digest, expected, validator = GameBridge._layout_receipt_request(
        authorization, compact["build_plan"], request_id)
    source = (ROOT / "factorio_mod/layout_executor.lua").read_text()
    outcome = json.loads(_run_oil_stub(source, body, tags))
    assert outcome["created"] == outcome["tagged"] == 1257 and outcome["accessed"] == 2
    assert outcome["failures"] == 0
    receipt = {key: outcome[key] for key in (
        "receipt_version", "request_id", "request_digest", "tick", "ok", "attempted_placements",
        "succeeded_placements", "already_present_placements", "failed_placements")}
    receipt["placement_failures"] = []
    assert GameBridge._validate_layout_receipt(receipt, request_id, digest, expected, validator)["ok"] is True

    # The expanded request also exceeds the executor's own parameter limit.
    expanded_body = json.dumps({"authorization": authorization, "build_plan": expanded["build_plan"],
                                "request_id": request_id, "request_digest": digest},
                               sort_keys=True, separators=(",", ":"))
    assert len(expanded_body) > LAYOUT_REQUEST_MAX_BYTES
    rejected = _run_oil_stub(source, expanded_body, tags)
    assert rejected.startswith("REJECTED 0 0") and "invalid correlated request" in rejected


@pytest.mark.skipif(shutil.which("lua") is None, reason="Lua interpreter unavailable")
def test_prior_correlated_executor_rejects_the_compact_oil_request(oil_submissions):
    """Emulate the correlated executor before the protocol: its allowed-key check
    rejects the envelope. Uncorrelated legacy executors have no such check."""
    _oil, _expanded, compact = oil_submissions
    authorization = {**compact["authorization"], "approved_actions": ["project_more_ghosts"]}
    body, *_ = GameBridge._layout_receipt_request(authorization, compact["build_plan"], "d" * 32)
    source = (ROOT / "factorio_mod/layout_executor.lua").read_text()
    assert source.count(ORIGINAL_ENVELOPE_CHECK) == source.count(ORIGINAL_ALLOWED_KEY) == 1
    prior = source.replace(ORIGINAL_ALLOWED_KEY, "").replace(ORIGINAL_ENVELOPE_CHECK, "  if false then")
    rejected = _run_oil_stub(prior, body, compact["build_plan"]["optimizer_ownership"])
    assert rejected.startswith("REJECTED 0 0") and "invalid correlated request" in rejected
