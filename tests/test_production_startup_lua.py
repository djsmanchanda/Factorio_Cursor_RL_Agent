# Path: tests/test_production_startup_lua.py | Purpose: Execute the game-clock sampler with the installed Lua interpreter.
from pathlib import Path
import json

import pytest
import shutil
import subprocess

MODULE = Path(__file__).resolve().parents[1] / "factorio_mod/production_startup.lua"
LUA = shutil.which("lua") or shutil.which("lua5.4")
pytestmark = pytest.mark.skipif(LUA is None, reason="Lua interpreter unavailable")

HARNESS = '''
storage = {}; callbacks = {}; output = 0; reads = 0
script = {on_nth_tick = function(period, fn) assert(period == 60); poll = fn end}
local surface = {name = "nauvis"}
game = {tick = 0, surfaces = {nauvis = surface}, forces = {player = {
  get_item_production_statistics = function(s)
    assert(s == surface)
    return {get_input_count = function(item)
      assert(item == "plastic-bar"); reads = reads + 1; return output
    end}
  end
}}}
function arm(id) return callbacks.production_startup.arm("nauvis", "player", "plastic-bar", id) end
function read(id) return callbacks.production_startup.read("nauvis", "player", "plastic-bar", id) end
function advance(tick, count) game.tick = tick; output = count; poll({tick = tick}) end
'''


def run_lua(checks):
    loader = ("\nfunction reload_sampler() callbacks.production_startup = assert(load("
              + json.dumps(MODULE.read_text(), ensure_ascii=False) + "))() end\nreload_sampler()\n")
    result = subprocess.run([LUA, "-"], input=HARNESS + loader + checks,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_game_clock_captures_first_window_without_python_polling():
    run_lua('''
      arm("episode"); advance(600, 0)
      assert(read("episode").start_tick == nil)
      advance(660, 2); advance(2460, 302); advance(4260, 602)
      advance(6060, 602); advance(7860, 602)
      local report = read("episode")
      assert(report.complete and report.start_tick == 660 and #report.samples == 5)
      assert(report.samples[3].tick == 4260 and report.samples[3].produced == 602)
      local previous = reads; advance(9000, 602); assert(reads == previous)
    ''')


def test_resume_preserves_samples_and_reads_cannot_arm_late():
    run_lua('''
      output = 500
      assert(read("episode") == nil)
      arm("episode"); advance(60, 502)
      reload_sampler()
      assert(read("episode").start_tick == 60)
      assert(read("episode").baseline_count == 500)
      assert(read("episode").armed_at_episode_start)
      assert(read("next-episode") == nil)
      advance(1860, 802); assert(#read("episode").samples == 2)
      arm("episode"); assert(read("episode").start_tick == 60)
      arm("next-episode")
      assert(read("next-episode").baseline_count == 802)
      local n = 0; for _ in pairs(storage.production_startup_v2) do n=n+1 end
      assert(n == 1)
    ''')


def test_counter_reset_stops_sampling_but_preserves_proven_samples():
    run_lua('''
      arm("episode"); advance(60, 2); advance(1860, 302); advance(3660, 602)
      advance(3720, 0)
      local report = read("episode")
      assert(report.error == "production_counter_reset" and #report.samples == 3)
      assert(report.samples[3].produced == 602)
      local previous = reads; advance(5520, 300); assert(reads == previous)
      local ok = pcall(function() callbacks.production_startup.arm("other", "player", "plastic-bar", "x") end)
      assert(not ok)
    ''')
