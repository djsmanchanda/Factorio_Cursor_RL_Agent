# Path: tests/test_power_energy_units.py | Purpose: Execute real generated Lua telemetry with Factorio 2.1 J/tick values.
import shutil
import subprocess

import pytest

from orchestrator import live_base, power_district


class LuaClient:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path

    def command(self, query):
        executable = shutil.which('lua')
        if not executable:
            pytest.skip('Lua interpreter unavailable')
        fixture = '''
local pole={type='electric-pole',name='medium-electric-pole',position={x=0,y=0},electric_network_id=1}
local source={type='electric-energy-interface',name='electric-energy-interface',position={x=0,y=0},electric_network_id=1,power_production=10000000/60,power_usage=0}
local solar={type='solar-panel',name='solar-panel',position={x=10,y=0},electric_network_id=1,prototype={get_max_energy_production=function() return 1000 end}}
local engine={type='generator',name='steam-engine',position={x=20,y=0},electric_network_id=1,prototype={get_max_energy_production=function() return 15000 end}}
local machine={type='assembling-machine',name='assembling-machine-1',position={x=2,y=0},electric_network_id=1,prototype={get_max_energy_usage=function() return 1250 end}}
local entities={pole,source,solar,engine,machine}
pole.electric_network_statistics={input_counts={roboport=1},get_flow_count=function(q)
 assert(q.category=='input');return 7000000/60 end}
defines={flow_precision_index={one_minute=1}}
local surface={find_entities_filtered=function(filter)
 local out={};for _,e in ipairs(entities) do
  local match=filter.type==nil or filter.type==e.type
  if type(filter.type)=='table' then for _,t in ipairs(filter.type) do if t==e.type then match=true end end end
  if match then out[#out+1]=e end
 end;return out
end}
game={surfaces={nauvis=surface},forces={player={}}}
rcon={print=function(v) print(v) end}
'''
        path = self.tmp_path / 'power.lua'
        path.write_text(fixture + query.removeprefix('/sc '))
        result = subprocess.run([executable, str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()


def test_generation_converts_ticks_and_excludes_solar_from_firm(tmp_path):
    client = LuaClient(tmp_path)
    assert live_base.network_firm_generation_kw(client, 'nauvis', 'player', (0, 0)) == 10900
    assert live_base.network_generation_kw(client, 'nauvis', 'player', (0, 0)) == 10960


def test_assembler_demand_is_watts_not_joules_per_tick(tmp_path):
    assert power_district.network_peak_consumption_kw(LuaClient(tmp_path), 'nauvis', 'player', (0, 0)) == 75


def test_bootstrap_uses_actual_one_minute_consumption(tmp_path):
    from orchestrator.bootstrap_steam_power import recent_consumption_kw
    assert recent_consumption_kw(LuaClient(tmp_path), 'nauvis', 'player', (0, 0)) == pytest.approx(7000)
