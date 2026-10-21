# Path: tests/test_mined_plan_site.py | Purpose: Exercise complete mining-layout siting against Lua placement, ore and ownership constraints.
import json
import shutil
import subprocess

import pytest

from orchestrator import live_base
from planners.steel_bootstrap import steel_seed


def _lua(value):
    if isinstance(value, dict):
        return "{" + ",".join(f"[{json.dumps(k)}]={_lua(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "{" + ",".join(map(_lua, value)) + "}"
    return json.dumps(value)


class SurveyClient:
    def __init__(self, mode):
        self.mode = mode

    def command(self, command):
        # Execute the actual survey, with an engine fixture that has two ore sites.
        encoded, _ = json.JSONDecoder().raw_decode(command.split("helpers.json_to_table(", 1)[1])
        spec = json.loads(encoded)
        fixture = "local spec=" + _lua(spec) + ";local mode=" + json.dumps(self.mode) + r''';
local s={}
s.can_place_entity=function(a)
  if mode=='blocked' then return false end
  if mode=='later_site' and a.name=='electric-furnace' and a.position[1]==17.5 then return false end
  if mode=='other_side' and a.name=='medium-electric-pole' and a.position[2]>0.5 then return false end
  return true
end
s.find_entities_filtered=function(q)
  if q.name=='iron-ore' then
    return {{name='iron-ore',position={x=10.5,y=0.5}},{name='iron-ore',position={x=30.5,y=0.5}}}
  end
  if q.type=='resource' then
    if mode=='mixed' and q.area[1][1]<20 then return {{name='iron-ore'},{name='copper-ore'}} end
    if mode=='no_ore' then return {} end
    return {{name='iron-ore'}}
  end
  if mode=='ghost' and q.type=='entity-ghost' and q.area[1][1]<20 then return {{type='entity-ghost'}} end
  return {}
end
game={surfaces={nauvis=s},forces={player={}}}
helpers={json_to_table=function(_) return spec end}
defines={direction={north=0,east=4,south=8,west=12},build_check_type={manual=1}}
prototypes={entity=setmetatable({}, {__index=function() return {
  collision_box={left_top={x=-0.4,y=-0.4},right_bottom={x=0.4,y=0.4}}} end})}
rcon={print=print}
'''
        result = subprocess.run([shutil.which("lua"), "-"], input=fixture + command.removeprefix("/sc "),
                                text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        return result.stdout


@pytest.mark.lua
@pytest.mark.skipif(shutil.which("lua") is None, reason="Lua interpreter unavailable")
@pytest.mark.parametrize("mode,reserved,expected", [
    ("later_site", set(), (0, (30.5, 0.5))),
    ("other_side", set(), (1, (10.5, 0.5))),
    ("mixed", set(), (0, (30.5, 0.5))),
    ("ghost", set(), (0, (30.5, 0.5))),
    ("clear", {(10, 0)}, (0, (30.5, 0.5))),
    ("blocked", set(), None),
    ("no_ore", set(), None),
])
def test_complete_layout_survey_skips_bad_sites_and_preserves_legality(mode, reserved, expected):
    templates = [steel_seed((0.5, 0.5), "east", mined=True, pole_side=side) for side in (1, -1)]
    assert live_base.mined_plan_site(SurveyClient(mode), "nauvis", "player", "iron-ore", (0, 0),
                                     templates, reserved_tiles=reserved) == expected


@pytest.mark.parametrize("response", ["0 10.5 0.5", "2 10.5 0.5", "1 nan 0.5", "1 2 3 4", "garbled"])
def test_survey_rejects_unknown_or_malformed_placement_identity(response):
    class Client:
        def command(self, _):
            return response
    with pytest.raises(live_base.TelemetryError):
        live_base.mined_plan_site(Client(), "nauvis", "player", "iron-ore", (0, 0),
                                 [steel_seed((0.5, 0.5), "east", mined=True)])
