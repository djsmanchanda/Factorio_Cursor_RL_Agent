# Path: tests/test_laya_clear_obstacles.py | Purpose: Trees and minable rocks in a footprint are cleared by robots, not refused as collisions.
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from tests.test_atomic_removal_preflight import _HARNESS as EXECUTOR_HARNESS, EXECUTOR
from tests.test_laya_saved_world import _SURVEY_LUA_FIXTURE
from training import laya_clean_runner as runner
from training.laya_saved_world import submission_plan

SAVED_WORLD = Path("training/laya_saved_world.lua")

# One roboport network with a funded chest, a 3x1 machine ("wide") and a 1x1 belt, and a fake surface where
# can_place_entity fails whenever any obstacle is listed. Obstacles are (type, minable, x, y, w, h).
_WORLD = r"""
request.bounds={x_min=-50,x_max_exclusive=50,y_min=-50,y_max_exclusive=50}
local mask={layers={object=true,player=true}}
prototypes.entity['wide']={type='assembling-machine',items_to_place_this={{name='wide',count=1}},
  collision_box={left_top={x=-1.4,y=-0.4},right_bottom={x=1.4,y=0.4}},collision_mask=mask}
local obstacles={}
local function obstacle(kind,minable,x,y,layers)
  return {valid=true,type=kind,minable=minable,position={x=x,y=y},name=kind,
    prototype={collision_mask={layers=layers or {object=true}}}}
end
__OBSTACLES__
local roboport={name='roboport',type='roboport',energy=1,position={x=0,y=0},
  logistic_network={network_id=1,all_construction_robots=5,available_construction_robots=5,all_logistic_robots=5},
  prototype={construction_radius=40,logistic_radius=40}}
local chest={name='chest',type='logistic-container',position={x=1,y=1},prototype={logistic_mode='passive-provider'},
  get_inventory=function() return {get_contents=function() return {wide=5} end} end}
local function inside(e,area) return e.position.x>=area[1][1] and e.position.x<=area[2][1]
  and e.position.y>=area[1][2] and e.position.y<=area[2][2] end
local water=__WATER__
surface.find_entities_filtered=function(f)
  if f.area and not f.type and not f.name then
    local found={}
    for _,e in ipairs(obstacles) do if inside(e,f.area) then found[#found+1]=e end end
    return found
  end
  if f.type=='logistic-container' then return {chest} end
  if f.type=='entity-ghost' or f.type=='resource' or f.position then return {} end
  if f.force then return {roboport,chest} end
  return {}
end
surface.can_place_entity=function() return #obstacles==0 and not water end
surface.get_tile=function() return {valid=true,name=water and 'water' or 'dry-dirt',
  collides_with=function(layer) return water and layer=='object' end} end
surface.find_logistic_network_by_position=function() return {network_id=1} end
surface.find_logistic_networks_by_construction_area=function() return {} end
request.candidates={{option_id='c',network='1',plan={phases={{actions={
  {action_type='place_ghost',entity='wide',position={x=10.5,y=10.5},direction='__DIRECTION__'}}}}}}}
"""

_TREE_AT = "obstacles[#obstacles+1]=obstacle('tree',true,%s,%s)"


def _run_lua(obstacles="", *, water=False, direction="north", assertions="", patched=False):
    lua = shutil.which("lua")
    if lua is None:
        pytest.skip("Lua interpreter required")
    fixture = _SURVEY_LUA_FIXTURE.replace("__EXTRA_RECIPES__", "{}").replace("__MALFORMED_RECIPES__", "")
    world = (_WORLD.replace("__OBSTACLES__", obstacles).replace("__WATER__", "true" if water else "false")
             .replace("__DIRECTION__", direction))
    source = SAVED_WORLD.read_text()
    if patched:  # The live runner's collision patch, as LiveWorld applies it.
        assert source.count(runner.COLLISION_RETURN) == 1
        source = source.replace(runner.COLLISION_RETURN, runner.COLLISION_RETURN_LIVE)
    body = fixture + world + "\nlocal facts=(function()\n" + source + "\nend)()\n" + assertions
    result = subprocess.run([lua, "-"], input=body, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


REASON = "local r=facts.validations.c.reason\n"


@pytest.mark.lua
def test_footprint_without_obstacles_is_unchanged():
    _run_lua(assertions=REASON + "assert(r==nil, tostring(r))")


@pytest.mark.lua
def test_tree_only_footprint_is_placeable():
    _run_lua(_TREE_AT % (10.5, 10.5), assertions=REASON + "assert(r==nil, tostring(r))")


@pytest.mark.lua
def test_minable_rock_only_footprint_is_placeable():
    _run_lua("obstacles[#obstacles+1]=obstacle('simple-entity',true,11,10.5)", assertions=REASON + "assert(r==nil, tostring(r))")


@pytest.mark.lua
def test_unminable_simple_entity_still_collides():
    _run_lua("obstacles[#obstacles+1]=obstacle('simple-entity',false,11,10.5)",
             assertions=REASON + "assert(r=='collision', tostring(r))")


@pytest.mark.lua
@pytest.mark.parametrize("kind", ["assembling-machine", "transport-belt", "cliff", "entity-ghost"])
def test_tree_plus_any_other_blocker_still_collides(kind):
    _run_lua(_TREE_AT % (10.5, 10.5) + f"\nobstacles[#obstacles+1]=obstacle('{kind}',false,9.8,10.5)",
             assertions=REASON + "assert(r=='collision', tostring(r))")


@pytest.mark.lua
def test_tree_on_water_still_collides():
    _run_lua(_TREE_AT % (10.5, 10.5), water=True, assertions=REASON + "assert(r=='collision', tostring(r))")


@pytest.mark.lua
def test_refusal_with_no_obstacle_in_the_footprint_still_collides():
    """A tree near but outside the footprint cannot explain the refusal, so it stays refused."""
    _run_lua(_TREE_AT % (10.5, 12.5), assertions=REASON + "assert(r=='collision', tostring(r))")


@pytest.mark.lua
def test_footprint_is_rotated_with_the_ghost():
    tree_beside = _TREE_AT % (10.5, 11.5)  # Inside the east-facing 1x3, outside the north-facing 3x1.
    _run_lua(tree_beside, direction="north", assertions=REASON + "assert(r=='collision', tostring(r))")
    _run_lua(tree_beside, direction="east", assertions=REASON + "assert(r==nil, tostring(r))")


@pytest.mark.lua
def test_live_collision_patch_names_rocks_and_trees_and_keeps_real_blockers():
    refused = "obstacles[#obstacles+1]=obstacle('cliff',false,9.8,10.5)"
    _run_lua(_TREE_AT % (10.5, 10.5) + "\n" + refused + "\nobstacles[#obstacles+1]=obstacle('simple-entity',true,11,10.5)",
             patched=True, assertions=REASON + "assert(r=='collision:wide@10.5,10.5#dry-dirt|cliff+rock+tree', tostring(r))")


def test_clearable_blockers_are_not_remembered(tmp_path):
    validations = {name: {"reason": f"collision:wide@1.5,2.5#{blocker}"} for name, blocker in {
        "tree": "dry-dirt|tree", "rocks": "dry-dirt|rock+tree", "machine": "dry-dirt|assembling-machine+tree",
        "cliff": "dry-dirt|cliff", "water": "water|tree", "bare": "water", "unknown": "?"}.items()}
    plain, refused = runner.split_refused_sites(validations)
    assert {v["reason"] for v in plain.values()} == {"collision:wide"}
    assert {r["blocker"] for r in refused} == {"dry-dirt|assembling-machine+tree", "dry-dirt|cliff", "water|tree",
                                                 "water", "?"}
    runner.remember_refused_sites(tmp_path, [r for r in refused])
    assert len((tmp_path / "evidence" / runner.BLOCKED_FOOTPRINTS).read_text().splitlines()) == 1


def test_saved_world_submissions_ask_the_executor_to_clear_obstacles():
    plan = {"surface": "nauvis", "force": "player", "atomic": True,
            "phases": [{"name": "p", "actions": [{"action_type": "place_ghost", "entity": "wide",
                                                  "position": {"x": 1.5, "y": 1.5}}]}]}
    assert submission_plan(plan)["clear_natural_obstacles"] is True
    assert "clear_natural_obstacles" not in plan


# --- Executor (factorio_mod/layout_executor.lua) --------------------------------------------------------------

def _executor(tmp_path, *, flag, extra="", assertions):
    lua = shutil.which("lua")
    if lua is None:
        pytest.skip("Lua interpreter required")
    scenario = f"""
        dofile("{EXECUTOR.as_posix()}")
        local function tree(x, y, kind, minable)
          local e = add_entity("tree-1", x, y, {{ name = "neutral" }})
          e.type = kind or "tree"; e.minable = minable ~= false
          e.prototype = {{ collision_mask = {{ layers = {{ object = true }} }} }}
          e.to_be_deconstructed = function() return e.marked == true end
          e.order_deconstruction = function(f)
            mutation_log[#mutation_log + 1] = "order:" .. e.type .. ":" .. f.name
            e.marked = true
            return true
          end
          return e
        end
        {extra}
        payload = {{
          authorization = {{ approved_actions = {{ "project_more_ghosts" }} }},
          build_plan = {{ surface = "nauvis", force = "planner", atomic = true,
            {'clear_natural_obstacles = true,' if flag else ''} phases = {{
            {{ name = "build", actions = {{
              {{ action_type = "place_ghost_no_reconcile", entity = "transport-belt", position = {{ x = 0, y = 0 }}, direction = "north" }},
            }} }},
          }} }},
        }}
        registered["build_layout_plan"]({{ parameter = "ignored" }})
        {assertions}
    """
    script = tmp_path / "clear_obstacles.lua"
    script.write_text(EXECUTOR_HARNESS + scenario, encoding="utf-8")
    result = subprocess.run([lua, str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.lua
def test_executor_orders_clearing_before_creating_the_ghost(tmp_path):
    _executor(tmp_path, flag=True, extra="local t = tree(0.1, 0.1)", assertions="""
      assert(captured_report.ok == true, tostring(captured_report.error))
      assert(mutation_log[1] == "preflight:transport-belt")
      assert(mutation_log[2] == "order:tree:planner", mutation_log[2])
      assert(mutation_log[3] == "create:entity-ghost" and #mutation_log == 3)
    """)


@pytest.mark.lua
def test_executor_clears_minable_rocks_too(tmp_path):
    _executor(tmp_path, flag=True, extra="tree(0.1, 0.1, 'simple-entity')", assertions="""
      assert(captured_report.ok == true and mutation_log[2] == "order:simple-entity:planner")
    """)


@pytest.mark.lua
def test_executor_without_the_flag_still_refuses_trees(tmp_path):
    _executor(tmp_path, flag=False, extra="tree(0.1, 0.1)", assertions="""
      assert(captured_report.ok == false and captured_report.error == "atomic_footprint_blocked")
      assert(#mutation_log == 1)
    """)


@pytest.mark.lua
@pytest.mark.parametrize("other", ["add_entity('stone-furnace', 0.25, 0)", "tree(0.2, 0.2, 'simple-entity', false)"])
def test_executor_keeps_machines_and_unminable_entities_blocking(tmp_path, other):
    _executor(tmp_path, flag=True, extra=f"tree(0.1, 0.1)\n{other}", assertions="""
      assert(captured_report.ok == false and captured_report.error == "atomic_footprint_blocked")
      assert(#mutation_log == 1)
    """)
