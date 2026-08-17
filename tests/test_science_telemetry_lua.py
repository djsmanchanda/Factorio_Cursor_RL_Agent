# Path: tests/test_science_telemetry_lua.py
# Purpose: Exercise the read-only ScienceStatus Lua command against focused Factorio API stubs.

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "factorio_mod" / "science_telemetry.lua"
CONTROL = ROOT / "factorio_mod" / "control.lua"

try:
    import lupa
except ImportError:  # pragma: no cover - static coverage remains useful without Lua.
    lupa = None


_STUB = r'''
defines = {
  entity_status = { working = 1, no_power = 2 },
  inventory = { lab_input = 1 }
}

commands = { registered = {} }
function commands.add_command(name, _, callback)
  commands.registered[name] = callback
end

written_path, written_json, written_append, captured_report = nil, nil, nil, nil
helpers = {
  json_to_table = function(_) return next_payload end,
  table_to_json = function(report)
    captured_report = report
    return '{"queue":{},"entries":{},"current":"__science_status_null__"}'
  end,
  write_file = function(path, json, append)
    written_path, written_json, written_append = path, json, append
  end
}

first_contents = {
  { name = "automation-science-pack", quality = "normal", count = 3 }
}
second_contents = {
  { name = "automation-science-pack", quality = "normal", count = 2 },
  { name = "automation-science-pack", quality = "uncommon", count = 1 }
}
inventory_reads, inventory_mutations = 0, 0

local function new_lab(unit_number, status, contents)
  local inventory = { valid = true }
  function inventory.get_contents()
    inventory_reads = inventory_reads + 1
    return contents
  end
  function inventory.insert() inventory_mutations = inventory_mutations + 1 end
  function inventory.remove() inventory_mutations = inventory_mutations + 1 end
  function inventory.clear() inventory_mutations = inventory_mutations + 1 end

  return {
    valid = true,
    unit_number = unit_number,
    position = { x = unit_number + 0.5, y = -unit_number },
    status = status,
    get_inventory = function(_) return inventory end
  }
end

labs = { new_lab(41, 1, first_contents), new_lab(9, 2, second_contents) }
surface = {
  name = "nauvis",
  find_entities_filtered = function(filter)
    assert(filter.type == "lab")
    assert(filter.force == player_force)
    return labs
  end
}
player_force = {
  name = "player",
  current_research = {
    name = "automation",
    research_unit_count = 10,
    research_unit_ingredients = {
      { name = "automation-science-pack", amount = 1 }
    }
  },
  research_progress = 0.42,
  research_queue = {}
}
game = {
  tick = 123456,
  surfaces = { nauvis = surface },
  forces = { player = player_force }
}
next_payload = { surface = "nauvis", force = "player" }
'''


def test_science_status_module_is_registered_from_control() -> None:
    """Keep this narrow static check when the optional Lua interpreter is absent."""
    source = MODULE.read_text(encoding="utf-8")
    control = CONTROL.read_text(encoding="utf-8")

    assert 'commands.add_command(\n  "science_status"' in source
    assert 'require("science_telemetry")' in control


@pytest.fixture
def lua():
    if lupa is None:
        pytest.skip("pip install lupa for behavioural Lua validation")
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(_STUB)
    runtime.execute(f'dofile("{MODULE.as_posix()}")')
    return runtime


def _run_status(lua) -> None:
    lua.execute('commands.registered["science_status"]({ parameter = "{}" })')


def test_science_status_sorts_labs_and_aggregates_quality_aware_input(lua) -> None:
    _run_status(lua)

    report = lua.globals().captured_report
    assert report["schema_version"] == "1.0.0"
    assert report["ok"] is True
    assert report["tick"] == 123456
    assert report["surface"] == "nauvis"
    assert report["force"] == "player"
    assert report["research"]["current"] == "automation"
    assert report["research"]["progress"] == pytest.approx(0.42)
    assert report["research"]["current_science_packs"]["automation-science-pack"] == 1
    assert report["research"]["current_research_unit_count"] == 10

    labs = report["labs"]
    assert (labs["total"], labs["working"]) == (2, 1)
    assert labs["status_counts"]["working"] == 1
    assert labs["status_counts"]["no_power"] == 1
    assert labs["input_inventory"]["automation-science-pack"] == 6
    assert [labs["entries"][index]["unit_number"] for index in (1, 2)] == [9, 41]
    assert labs["entries"][1]["input_inventory"]["automation-science-pack"] == 3
    assert labs["entries"][2]["input_inventory"]["automation-science-pack"] == 3

    assert lua.globals().written_path == "factorio_mod/science_reports/science_status_123456.json"
    assert lua.globals().written_append is False


def test_science_status_invalid_target_writes_only_an_error_report(lua) -> None:
    lua.execute('next_payload = { surface = "missing-surface", force = "player" }')
    _run_status(lua)

    report = lua.globals().captured_report
    assert report["ok"] is False
    assert report["surface"] == "missing-surface"
    assert report["force"] == "player"
    assert "Unknown surface: missing-surface" in report["error"]
    assert report["research"] is None
    assert report["labs"] is None
    assert lua.globals().written_path == "factorio_mod/science_reports/science_status_123456.json"


def test_science_status_only_reads_lab_inventories(lua) -> None:
    _run_status(lua)

    assert lua.globals().inventory_reads == 2
    assert lua.globals().inventory_mutations == 0
    assert lua.globals().first_contents[1]["count"] == 3
    assert lua.globals().second_contents[1]["count"] == 2
    assert lua.globals().second_contents[2]["count"] == 1
