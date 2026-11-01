# Path: tests/test_optimizer_build_evidence_lua.py | Purpose: Verify tagged robot-build evidence without a live server.

import copy
import json
from pathlib import Path

import jsonschema
import pytest

lupa = pytest.importorskip("lupa")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("change, valid", [
    (None, True), ("bad_hash", False), ("bad_transaction", False),
    ("trailing_newline", False), ("reconciling_ghost", False), ("lab_tags", False),
])
def test_build_plan_schema_constrains_optimizer_tags(change, valid):
    schema = json.loads((ROOT / "schemas/build_plan.schema.json").read_text())
    tags = {"optimizer_episode": "episode/1", "optimizer_project": "project-1",
            "optimizer_transaction": "a" * 32, "optimizer_plan_hash": "b" * 64}
    action = {"action_type": "place_ghost_no_reconcile", "entity": "lab",
              "position": {"x": 1.5, "y": 2.5}, "optimizer_ownership": copy.deepcopy(tags)}
    if change == "bad_hash":
        action["optimizer_ownership"]["optimizer_plan_hash"] = "B" * 64
    elif change == "bad_transaction":
        action["optimizer_ownership"]["optimizer_transaction"] = "a" * 31
    elif change == "trailing_newline":
        action["optimizer_ownership"]["optimizer_plan_hash"] += "\n"
    elif change == "reconciling_ghost":
        action["action_type"] = "place_ghost"
    elif change == "lab_tags":
        action["research_lab_ownership"] = {"research_lab_episode": "episode/1",
                                            "research_lab_packet": "packet",
                                            "research_lab_transaction": "c" * 32}
    plan = {"phases": [{"name": "build", "actions": [action]}]}
    assert jsonschema.Draft7Validator(schema).is_valid(plan) is valid


@pytest.fixture
def lua():
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute('''
        storage={}
        game={tick=200,surfaces={nauvis={name='nauvis'}},forces={player={name='player'}}}
        units={}
        function game.get_entity_by_unit_number(unit) return units[unit] end
        game.surfaces.nauvis.find_entities_filtered=function() return {} end
        commands={registered={}}
        function commands.add_command(name, _, callback) commands.registered[name]=callback end
        helpers={json_to_table=function() return payload end,
          table_to_json=function(report) last_report=report; return '{"entities":{}}' end,
          write_file=function(path,text,append) written={path=path,text=text,append=append} end}
        tags={optimizer_episode='episode/1',optimizer_project='project-1',
          optimizer_transaction=string.rep('a',32),optimizer_plan_hash=string.rep('b',64)}
        function make_entity(unit,name)
          return {valid=true,name=name or 'assembling-machine-2',unit_number=unit,
            surface=game.surfaces.nauvis,force=game.forces.player,position={x=1.5,y=2.5},
            direction=4,quality={name='normal'},get_recipe=function() return {name='iron-gear-wheel'} end}
        end
        function event_for(entity)
          return {entity=entity,robot={valid=true},tick=190,tags=tags}
        end
        payload={request_id='req-1',surface='nauvis',force='player',tags=tags}
    ''')
    runtime.execute(f'M = dofile("{ROOT}/factorio_mod/optimizer_build_evidence.lua")')
    return runtime


def test_robot_build_records_final_identity_and_scope(lua):
    lua.execute('''
        old=make_entity(41); replacement=make_entity(42)
        captured=M.capture(event_for(old)); old.valid=false
        M.record(captured,replacement); units[42]=replacement
        commands.registered.optimizer_build_evidence({parameter='{}'})
    ''')
    report = lua.globals().last_report
    assert report["ok"] is True and report["ownership_claimed"] is False
    assert report["dispatch_authorized"] is False
    entity = report["entities"][1]
    assert entity["unit_number"] == 42 and entity["tick"] == 190
    assert entity["current"]["state"] == "present"
    assert entity["current"]["direction"]["value"] == 4
    assert entity["current"]["quality"]["value"] == "normal"
    assert entity["current"]["recipe"]["value"] == "iron-gear-wheel"
    assert lua.globals().written["path"].endswith("optimizer_build_evidence_req-1.json")
    payload = {key: report[key] for key in ("version", "source", "request_id", "tick", "ok",
                                               "ownership_claimed", "dispatch_authorized", "surface", "force")}
    payload["tags"] = dict(report["tags"].items())
    payload["entities"] = [{"entity": entity["entity"], "position": dict(entity["position"].items()),
                            "unit_number": entity["unit_number"], "tick": entity["tick"],
                            "current": {"state": "present", **{
                                key: dict(entity["current"][key].items())
                                for key in ("direction", "quality", "recipe")}}}]
    schema = json.loads((ROOT / "schemas/optimizer_build_evidence.schema.json").read_text())
    jsonschema.validate(payload, schema)
    payload["tags"]["optimizer_transaction"] += "\n"
    assert not jsonschema.Draft7Validator(schema).is_valid(payload)


@pytest.mark.parametrize("mutation,state", [
    ("units[41]=nil", "not_found"),
    ("game.get_entity_by_unit_number=function() error('unavailable') end; "
     "game.surfaces.nauvis.find_entities_filtered=function() error('unavailable') end", "unavailable"),
    ("units[41].position.x=99", "mismatch"),
    ("units[41].valid=false", "unavailable"),
])
def test_current_lookup_does_not_confuse_absent_unknown_and_mismatch(lua, mutation, state):
    lua.execute(f'''
        local entity=make_entity(41); units[41]=entity
        M.record(M.capture(event_for(entity)), entity)
        {mutation}
        commands.registered.optimizer_build_evidence({{parameter='{{}}'}})
    ''')
    assert lua.globals().last_report["entities"][1]["current"]["state"] == state


def test_current_lookup_preserves_unknown_and_unset_configuration(lua):
    lua.execute('''
        local entity=make_entity(41); units[41]=entity
        M.record(M.capture(event_for(entity)), entity)
        entity.direction=nil; entity.quality=nil; entity.get_recipe=function() return nil end
        commands.registered.optimizer_build_evidence({parameter='{}'})
    ''')
    current = lua.globals().last_report["entities"][1]["current"]
    assert current["state"] == "present"
    assert current["direction"]["state"] == current["quality"]["state"] == "unavailable"
    assert current["recipe"]["state"] == "unset"


def test_lookup_falls_back_to_bounded_exact_unit_search(lua):
    lua.execute('''
        local entity=make_entity(41)
        M.record(M.capture(event_for(entity)), entity)
        game.surfaces.nauvis.find_entities_filtered=function(filter)
          assert(filter.name=='assembling-machine-2' and filter.force=='player' and filter.limit==64)
          assert(filter.position.x==1.5 and filter.position.y==2.5)
          local foreign=make_entity(42)
          return {foreign,entity}
        end
        commands.registered.optimizer_build_evidence({parameter='{}'})
    ''')
    assert lua.globals().last_report["entities"][1]["current"]["state"] == "present"


def test_fallback_rejects_same_unit_at_a_different_center(lua):
    lua.execute('''
        local entity=make_entity(41)
        M.record(M.capture(event_for(entity)), entity)
        local moved=make_entity(41); moved.position.x=2.5
        game.surfaces.nauvis.find_entities_filtered=function() return {moved} end
        commands.registered.optimizer_build_evidence({parameter='{}'})
    ''')
    assert lua.globals().last_report["entities"][1]["current"]["state"] == "not_found"


@pytest.mark.parametrize("mutation", [
    "event.tags=nil", "event.robot=nil", "event.robot.valid=false", "event.entity.unit_number=false",
    "event.entity.unit_number=0",
    "event.tags.optimizer_transaction='../unsafe'", "event.tags.optimizer_plan_hash=string.rep('A',64)",
    "event.tags.extra=true", "event.tick=-1",
])
def test_incomplete_or_forged_shape_cannot_be_recorded(lua, mutation):
    lua.execute(f"event=event_for(make_entity(41)); {mutation}; captured=M.capture(event)")
    assert lua.globals().captured is None


def test_replacement_mismatch_and_other_transaction_not_attributed(lua):
    lua.execute('''
        captured=M.capture(event_for(make_entity(41)))
        other=make_entity(42); other.position.x=3.5; M.record(captured,other)
        M.record(captured,make_entity(43))
        payload.tags={optimizer_episode='episode/1',optimizer_project='project-1',
          optimizer_transaction=string.rep('c',32),optimizer_plan_hash=string.rep('b',64)}
        commands.registered.optimizer_build_evidence({parameter='{}'})
    ''')
    assert len(lua.globals().last_report["entities"]) == 0
    assert lua.globals().written["text"] == '{"entities":[]}'


def test_overflow_and_unsafe_request_fail_closed(lua):
    lua.execute('''
        storage.optimizer_build_evidence={records={},overflow=true}
        commands.registered.optimizer_build_evidence({parameter='{}'})
    ''')
    assert lua.globals().last_report["ok"] is False
    assert lua.globals().last_report["error"] == "robot_build_record_capacity_exceeded"
    lua.execute('written=nil; payload.request_id="../unsafe"; commands.registered.optimizer_build_evidence({parameter="{}"})')
    assert lua.globals().written is None


def test_control_captures_before_requester_replacement():
    source = (ROOT / "factorio_mod/control.lua").read_text()
    assert source.index("optimizer_build_evidence.capture(event)") < source.index("trash_requesters.revive_with_trash")
    assert source.index("trash_requesters.revive_with_trash") < source.index("optimizer_build_evidence.record(optimizer_evidence, ownership_entity)")
    assert source.count("script.on_event(defines.events.on_robot_built_entity") == 1


def test_v3_preserves_unitless_event_without_claiming_current_identity(lua):
    lua.execute('''
        local entity=make_entity(nil, 'transport-belt')
        local capture=M.capture(event_for(entity)); assert(capture~=nil)
        M.record(capture,entity)
        commands.registered.optimizer_build_evidence({parameter='{}'})
        legacy=last_report
        commands.registered.optimizer_build_evidence_v3({parameter='{}'})
    ''')
    assert len(lua.globals().legacy["entities"]) == 0
    report = lua.globals().last_report
    row = report["entities"][1]
    assert report["version"] == "optimizer-build-evidence-v3"
    assert row["entity"] == "transport-belt" and row["unit_number"] is None
    assert row["current"]["state"] == "identity_unavailable"
    payload = {key: report[key] for key in ("version", "source", "request_id", "tick", "ok",
                                              "ownership_claimed", "dispatch_authorized", "surface", "force")}
    payload["tags"] = dict(report["tags"].items())
    payload["entities"] = [{"entity": row["entity"], "position": dict(row["position"].items()),
                            "tick": row["tick"], "current": {"state": "identity_unavailable"}}]
    schema = json.loads((ROOT / "schemas/optimizer_build_evidence_v3.schema.json").read_text())
    validator = jsonschema.Draft7Validator(schema)
    assert validator.is_valid(payload)
    payload["entities"][0]["current"] = {"state": "not_found"}
    assert not validator.is_valid(payload)
    payload["entities"][0]["current"] = {"state": "identity_unavailable"}
    payload["entities"][0]["unit_number"] = 7
    assert not validator.is_valid(payload)


def test_unitless_capture_does_not_allow_recording_a_different_site(lua):
    lua.execute('''
        local capture=M.capture(event_for(make_entity(nil,'transport-belt')))
        local other=make_entity(nil,'transport-belt'); other.position.x=9
        M.record(capture,other)
        commands.registered.optimizer_build_evidence_v3({parameter='{}'})
    ''')
    assert len(lua.globals().last_report["entities"]) == 0
