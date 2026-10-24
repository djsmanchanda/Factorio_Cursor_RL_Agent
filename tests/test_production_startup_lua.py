# Path: tests/test_production_startup_lua.py | Purpose: Exercise game-clock startup capture and C5 replay.
from pathlib import Path

import pytest

from orchestrator.checkpoint_milestones import evaluate_milestone

lupa = pytest.importorskip("lupa")
MODULE = Path(__file__).resolve().parents[1] / "factorio_mod/production_startup.lua"


@pytest.fixture
def lua():
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute('''
      storage = {}; callbacks = {}; output = 500; reads = 0
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
    ''')
    runtime.execute(MODULE.read_text())
    return runtime


def plain(value):
    if lupa.lua_type(value) != "table":
        return value
    if value[1] is not None:
        return [plain(value[i]) for i in range(1, len(value) + 1)]
    return {k: plain(v) for k, v in value.items()}


def test_game_clock_captures_first_window_without_python_polling(lua):
    lua.execute('read("episode")')
    lua.execute('advance(600, 500)')  # Existing output in the save cannot start C5.
    assert lua.eval('read("episode").start_tick') is None
    lua.execute('advance(660, 502)')
    for tick, count in [(2460, 802), (4260, 1102), (6060, 1102), (7860, 1102)]:
        lua.globals().advance(tick, count)
    report = plain(lua.eval('read("episode")'))
    assert report["complete"]
    assert report["start_tick"] == 660
    assert len(report["samples"]) == 5
    result = evaluate_milestone("C5", {"tick": 7860, "plastic_startup": report})
    assert result.passed
    assert result.evidence["qualified_tick"] == 4260
    reads = lua.globals().reads
    lua.execute('advance(9000, 1102)')
    assert lua.globals().reads == reads  # No perpetual polling after the window.
    assert plain(lua.eval('read("episode")'))["samples"] == report["samples"]


def test_same_logical_run_preserves_samples_across_reload_new_run_rebaselines(lua):
    lua.execute('read("episode"); advance(60, 502)')
    lua.execute(MODULE.read_text())  # Save/load retains storage, re-registers handlers.
    assert lua.eval('read("episode").start_tick') == 60
    report = plain(lua.eval('read("next-episode")'))
    assert "start_tick" not in report
    assert report["baseline_count"] == 502
    assert len(list(lua.globals().storage.production_startup_v1.items())) == 1


def test_counter_reset_is_unknown_and_wrong_scope_is_rejected(lua):
    lua.execute('read("episode"); advance(60, 502); advance(120, 0)')
    report = plain(lua.eval('read("episode")'))
    assert report["error"] == "production_counter_reset"
    assert evaluate_milestone("C5", {"tick": 120, "plastic_startup": report}).unknown
    with pytest.raises(lupa.LuaError):
        lua.execute('callbacks.production_startup.read("other", "player", "plastic-bar", "x")')
