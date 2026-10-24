# Path: tests/test_production_startup_lua.py | Purpose: Execute the game-clock sampler with the installed Lua interpreter.
from pathlib import Path
import shutil
import subprocess

MODULE = Path(__file__).resolve().parents[1] / "factorio_mod/production_startup.lua"
HARNESS = '''
storage = {}; callbacks = {}; output = 0; reads = 0
script = {on_nth_tick = function(period, fn) assert(period == 60); poll = fn end}
remote = {add_interface = function(name, methods) callbacks[name] = methods end}
local surface = {name = "nauvis"}
game = {tick = 0, surfaces = {nauvis = surface}, forces = {player = {
  get_item_production_statistics = function(s)
    assert(s == surface)
    return {get_input_count = function(item)
      assert(item == "plastic-bar"); reads = reads + 1; return output
    end}
  end
}}}
function read(id) return callbacks.production_startup.read("nauvis", "player", "plastic-bar", id) end
function advance(tick, count) game.tick = tick; output = count; poll({tick = tick}) end
'''


def run_lua(checks):
    lua = shutil.which("lua")
    assert lua, "Lua interpreter required for the C5 fast regression fixtures"
    result = subprocess.run([lua, "-"], input=HARNESS + MODULE.read_text() + checks,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_game_clock_captures_first_window_without_python_polling():
    run_lua('''
      read("episode"); advance(600, 0)
      assert(read("episode").start_tick == nil)
      advance(660, 2); advance(2460, 302); advance(4260, 602)
      advance(6060, 602); advance(7860, 602)
      local report = read("episode")
      assert(report.complete and report.start_tick == 660 and #report.samples == 5)
      assert(report.samples[3].tick == 4260 and report.samples[3].produced == 602)
      local previous = reads; advance(9000, 602); assert(reads == previous)
    ''')


def test_resume_preserves_samples_and_late_new_watch_is_unknown():
    run_lua('''
      read("episode"); advance(60, 2)
    ''' + MODULE.read_text() + '''
      assert(read("episode").start_tick == 60)
      local report = read("next-episode")
      assert(report.start_tick == nil and report.baseline_count == 2)
      assert(report.error == "startup_armed_after_production")
      advance(1860, 302); assert(#report.samples == 0)
      local n = 0; for _ in pairs(storage.production_startup_v1) do n=n+1 end
      assert(n == 1)
    ''')


def test_counter_reset_stops_sampling_but_preserves_proven_samples():
    run_lua('''
      read("episode"); advance(60, 2); advance(1860, 302); advance(3660, 602)
      advance(3720, 0)
      local report = read("episode")
      assert(report.error == "production_counter_reset" and #report.samples == 3)
      assert(report.samples[3].produced == 602)
      local previous = reads; advance(5520, 300); assert(reads == previous)
      local ok = pcall(function() callbacks.production_startup.read("other", "player", "plastic-bar", "x") end)
      assert(not ok)
    ''')
