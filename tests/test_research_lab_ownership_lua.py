# Path: tests/test_research_lab_ownership_lua.py | Purpose: Verify scoped robot-build evidence and replacement identity without a live runtime.
from pathlib import Path
import json

import jsonschema
import pytest

lupa = pytest.importorskip("lupa")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def lua():
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute('''
        storage = {}
        game = {tick=200, surfaces={nauvis={name='nauvis'}}, forces={player={name='player'}}}
        commands = {registered={}}
        function commands.add_command(name, _, callback) commands.registered[name]=callback end
        written = {}
        helpers = {
          json_to_table=function() return payload end,
          table_to_json=function(report) last_report=report; return '{"entities":{}}' end,
          write_file=function(path, text, append) written={path=path,text=text,append=append} end,
        }
        function make_entity(unit, name)
          return {valid=true, name=name or 'requester-chest', unit_number=unit,
            surface=game.surfaces.nauvis, force=game.forces.player, position={x=1.5,y=2.5}}
        end
        function event_for(entity)
          return {entity=entity, robot={valid=true}, tick=190,
            tags={research_lab_episode='episode/1', research_lab_packet='packet-1',
                  research_lab_transaction=string.rep('a',32)}}
        end
        payload={request_id='req-1', surface='nauvis', force='player',
          episode_id='episode/1', packet_id='packet-1', transaction_id=string.rep('a',32)}
    ''')
    runtime.execute(f'M = dofile("{ROOT}/factorio_mod/research_lab_ownership.lua")')
    return runtime


def test_robot_built_record_uses_replacement_entity_identity(lua):
    lua.execute('''
        old=make_entity(41); replacement=make_entity(42)
        captured=M.capture(event_for(old)); old.valid=false
        M.record(captured,replacement)
        commands.registered.research_lab_ownership({parameter='{}'})
    ''')
    report = lua.globals().last_report
    assert report["ok"] is True
    assert report["ownership_claimed"] is False
    assert report["dispatch_authorized"] is False
    assert report["transaction_id"] == "a" * 32
    assert report["entities"][1]["unit_number"] == 42
    assert report["entities"][1]["tick"] == 190
    assert lua.globals().written["path"].endswith("research_lab_ownership_req-1.json")
    entity = report["entities"][1]
    converted = {key: report[key] for key in (
        "version", "source", "request_id", "tick", "ok", "ownership_claimed",
        "dispatch_authorized", "surface", "force", "episode_id", "packet_id", "transaction_id",
    )}
    converted["entities"] = [{
        "entity": entity["entity"], "unit_number": entity["unit_number"], "tick": entity["tick"],
        "position": dict(entity["position"].items()), "tags": dict(entity["tags"].items()),
    }]
    schema = json.loads((ROOT / "schemas/research_lab_ownership.schema.json").read_text())
    jsonschema.validate(converted, schema)


@pytest.mark.parametrize("mutation", [
    "event.tags=nil", "event.robot.valid=false", "event.entity.unit_number=nil",
    "event.tags.research_lab_transaction='../unsafe'",
    "event.tags.research_lab_transaction='nonce_1'",
    "event.tags.research_lab_transaction=string.rep('A',32)", "event.tick=-1",
])
def test_capture_rejects_incomplete_event_evidence(lua, mutation):
    lua.execute(f"event=event_for(make_entity(41)); {mutation}; captured=M.capture(event)")
    assert lua.globals().captured is None


def test_unrelated_replacement_and_transaction_are_not_attributed(lua):
    lua.execute('''
        captured=M.capture(event_for(make_entity(41)))
        other=make_entity(42); other.position.x=3.5; M.record(captured,other)
        M.record(captured,make_entity(43))
        payload.transaction_id=string.rep('b',32)
        commands.registered.research_lab_ownership({parameter='{}'})
    ''')
    assert len(lua.globals().last_report["entities"]) == 0
    assert lua.globals().written["text"] == '{"entities":[]}'


def test_capacity_exhaustion_and_unsafe_request_fail_closed(lua):
    lua.execute('''
        storage.research_lab_ownership={records={},overflow=true}
        commands.registered.research_lab_ownership({parameter='{}'})
    ''')
    assert lua.globals().last_report["ok"] is False
    assert lua.globals().last_report["error"] == "robot_build_record_capacity_exceeded"
    lua.execute('written=nil; payload.request_id="../unsafe"; commands.registered.research_lab_ownership({parameter="{}"})')
    assert lua.globals().written is None


def test_control_captures_before_and_records_after_requester_replacement():
    source = (ROOT / "factorio_mod/control.lua").read_text(encoding="utf-8")
    assert source.index("research_lab_ownership.capture(event)") < source.index("trash_requesters.revive_with_trash")
    assert source.index("trash_requesters.revive_with_trash") < source.index("research_lab_ownership.record(ownership, ownership_entity)")
    assert source.count("script.on_event(defines.events.on_robot_built_entity") == 1
