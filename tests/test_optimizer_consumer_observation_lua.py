# Path: tests/test_optimizer_consumer_observation_lua.py | Purpose: Verify read-only scoped consumer observation with Lua stubs.
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

lupa = pytest.importorskip("lupa")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def lua():
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute("""
        game={tick=100,surfaces={},forces={player={name='player'}}}
        local force=game.forces.player
        local surface={name='nauvis'}; game.surfaces.nauvis=surface
        entities={}
        surface.find_entities_filtered=function(filter)
          assert(filter.force==force and filter.name=='chemical-plant' and filter.limit==65)
          return entities
        end
        function machine(unit)
          return {valid=true,name='chemical-plant',type='assembling-machine',unit_number=unit,
            position={x=1.5,y=2.5},surface=surface,force=force,products_finished=31,
            get_recipe=function() return {name='plastic-bar'} end}
        end
        payload={request_id='req-1',surface='nauvis',force='player',entity='chemical-plant',
                 position={x=1.5,y=2.5},unit_number=42}
        commands={registered={}}
        function commands.add_command(name, _, callback) commands.registered[name]=callback end
        helpers={json_to_table=function() return payload end,table_to_json=function(r) last_report=r; return '{}' end,
                 write_file=function(path,text,append) written={path=path,append=append} end}
    """)
    runtime.execute(f'dofile("{ROOT}/factorio_mod/optimizer_consumer_observation.lua")')
    return runtime


def py_report(report):
    result = {key: report[key] for key in ("version", "kind", "request_id", "tick", "ok",
                                            "ownership_claimed", "dispatch_authorized", "surface", "force")}
    requested = report["requested"]
    result["requested"] = {"entity": requested["entity"], "unit_number": requested["unit_number"],
                           "position": dict(requested["position"].items())}
    obs = report["observation"]
    result["observation"] = {"state": obs["state"]}
    if obs["state"] == "present":
        result["observation"]["recipe"] = dict(obs["recipe"].items())
        result["observation"]["products_finished"] = dict(obs["products_finished"].items())
    else:
        result["observation"]["reason"] = obs["reason"]
    return result


def test_consumer_reports_exact_unit_recipe_and_engine_counter(lua):
    lua.execute("entities={machine(42)}; commands.registered.optimizer_consumer_observation({parameter='{}'})")
    report = lua.globals().last_report
    assert report["ok"] is True and report["ownership_claimed"] is False and report["dispatch_authorized"] is False
    assert report["observation"]["recipe"]["value"] == "plastic-bar"
    assert report["observation"]["products_finished"]["value"] == 31
    assert lua.globals().written["path"].endswith("optimizer_consumer_req-1.json")
    schema = json.loads((ROOT / "schemas/optimizer_consumer_observation.schema.json").read_text())
    assert Draft7Validator(schema).is_valid(py_report(report))


@pytest.mark.parametrize("setup,reason", [
    ("entities={machine(99)}", "entity_not_found"),
    ("entities={machine(42),machine(42)}", "entity_identity_ambiguous"),
    ("entities={machine(42)}; entities[1].type='container'", "unsupported_consumer_type"),
    ("game.surfaces.nauvis.find_entities_filtered=function() error('unreadable') end", "entity_scan_unavailable_or_truncated"),
])
def test_unmatched_or_ambiguous_consumer_remains_unavailable(lua, setup, reason):
    lua.execute(setup + "; commands.registered.optimizer_consumer_observation({parameter='{}'})")
    report = lua.globals().last_report
    assert report["ok"] is True and report["observation"]["state"] == "unavailable"
    assert report["observation"]["reason"] == reason


def test_unreadable_recipe_and_counter_are_explicit(lua):
    lua.execute("""
        local e=machine(42); e.get_recipe=function() error('unavailable') end; e.products_finished=nil
        entities={e}; commands.registered.optimizer_consumer_observation({parameter='{}'})
    """)
    observation = lua.globals().last_report["observation"]
    assert observation["state"] == "present"
    assert observation["recipe"]["state"] == observation["products_finished"]["state"] == "unavailable"


def test_invalid_scope_or_request_never_observes_an_entity(lua):
    lua.execute("payload.surface='foreign'; commands.registered.optimizer_consumer_observation({parameter='{}'})")
    assert lua.globals().last_report["ok"] is False and lua.globals().last_report["error"] == "invalid_scope"
    lua.execute("written=nil; payload.request_id='../unsafe'; commands.registered.optimizer_consumer_observation({parameter='{}'})")
    assert lua.globals().written is None
