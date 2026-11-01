# Path: tests/test_rocket_silo_observation_lua.py
# Purpose: Exercise the read-only, request-correlated rocket-silo observation against focused Factorio API stubs.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "factorio_mod" / "rocket_silo_observation.lua"
CONTROL = ROOT / "factorio_mod" / "control.lua"
SCHEMA_PATH = ROOT / "schemas" / "rocket_silo_observation.schema.json"

try:
    import lupa
except ImportError:  # pragma: no cover - static coverage remains useful without Lua.
    lupa = None


_STUB = r'''
commands = { registered = {} }
function commands.add_command(name, _, callback)
  commands.registered[name] = callback
end

written = {}
last_payload = nil
helpers = {
  json_to_table = function(_) return next_payload end,
  table_to_json = function(payload)
    last_payload = payload
    if payload and payload.silos ~= nil and next(payload.silos) == nil then
      return '{"silos":{}}'
    end
    return "{}"
  end,
  write_file = function(path, json, append)
    written[#written + 1] = {
      path = path, payload = last_payload, json = json, append = append
    }
  end
}
function write_count() return #written end
function write_at(index) return written[index] end

defines = {
  rocket_silo_status = {
    building_rocket = 0, create_rocket = 1, lights_blinking_open = 2,
    doors_opening = 3, doors_opened = 4, rocket_rising = 5,
    arms_advance = 6, rocket_ready = 7, launch_starting = 8,
    engine_starting = 9, arms_retract = 10, rocket_flying = 11,
    lights_blinking_close = 12, doors_closing = 13, launch_started = 14
  },
  entity_status = { idle = 0, working = 1, no_power = 2 }
}

local function make_surface(name, index)
  local surface = { name = name, index = index, valid = true, entities = {} }
  function surface.find_entities_filtered(filter)
    local result = {}
    for _, entity in ipairs(surface.entities) do
      local type_match = not filter.type or entity.type == filter.type
      local force_match = not filter.force
        or (entity.force and entity.force.name == filter.force)
      if type_match and force_match then
        result[#result + 1] = entity
      end
    end
    return result
  end
  return surface
end

local nauvis = make_surface("nauvis", 1)
local planner_sandbox = make_surface("planner-sandbox", 2)
game = {
  tick = 1000,
  surfaces = { nauvis = nauvis, ["planner-sandbox"] = planner_sandbox },
  forces = {
    player = { name = "player", index = 1, valid = true },
    planner = { name = "planner", index = 2, valid = true }
  }
}
storage = {}
next_payload = nil

function entity(name, kind, surface, force, x, y, unit_number)
  return {
    valid = true,
    name = name,
    type = kind,
    surface = surface,
    force = force,
    position = { x = x, y = y },
    unit_number = unit_number
  }
end

function make_silo(surface, force, unit_number, x, y)
  local silo = entity("rocket-silo", "rocket-silo", surface, force, x, y, unit_number)
  silo.status = defines.entity_status.working
  silo.rocket_silo_status = defines.rocket_silo_status.building_rocket
  silo.rocket_parts = 42
  silo.send_to_orbit_automatically = false
  silo.prototype = {
    rocket_parts_required = 100,
    rocket_parts_storage_cap = 2000
  }
  silo.get_recipe = function() return { name = "rocket-part" } end
  silo.rocket = nil
  return silo
end

function make_rocket(surface, force, unit_number, x, y)
  local rocket = entity(
    "rocket-silo-rocket", "rocket-silo-rocket", surface, force, x, y, unit_number)
  rocket.attached_cargo_pod = nil
  return rocket
end

function make_pod(surface, force, unit_number, x, y)
  return entity("cargo-pod", "cargo-pod", surface, force, x, y, unit_number)
end

function last_silo(surface_name)
  local surface = game.surfaces[surface_name]
  return surface.entities[#surface.entities]
end

function attach_rocket(silo, surface_name, with_pod)
  local surface = game.surfaces[surface_name]
  local rocket = make_rocket(surface, silo.force, 12, 3.5, 4.5)
  if with_pod then
    rocket.attached_cargo_pod = make_pod(surface, silo.force, 13, 5.5, 6.5)
  end
  silo.rocket = rocket
end

function fail_recipe(silo)
  silo.get_recipe = function() error("recipe unsupported") end
end

function remove_required_parts(silo)
  silo.prototype.rocket_parts_required = nil
end

function remove_auto_launch(silo)
  silo.send_to_orbit_automatically = nil
end

function invalidate_rocket(silo)
  if silo.rocket then silo.rocket.valid = false end
end
'''


def test_rocket_silo_observation_is_registered_once_and_never_self_registers() -> None:
    source = MODULE.read_text(encoding="utf-8")
    control = CONTROL.read_text(encoding="utf-8")
    registration = 'commands.add_command(\n  "rocket_silo_observation"'

    assert 'require("rocket_silo_observation")' in control
    assert registration in source
    assert "script.on_event" not in source
    assert sum(
        path.read_text(encoding="utf-8").count(registration)
        for path in (ROOT / "factorio_mod").glob("*.lua")
    ) == 1


def test_rocket_silo_observation_has_no_mutation_or_launch_surface() -> None:
    source = MODULE.read_text(encoding="utf-8")

    for forbidden in (
        "create_entity", "create_force", "create_surface", "launch_rocket",
        "set_recipe", "insert_item", "remove_item", "destroy(", "revive(",
        "rocket_launch_events", "rocket_launch_observations",
    ):
        assert forbidden not in source
    assert source.count("write_file") == 1


def test_reports_use_a_directory_distinct_from_launch_events() -> None:
    source = MODULE.read_text(encoding="utf-8")
    assert 'REPORT_DIRECTORY = "factorio_mod/rocket_silo_observations/"' in source
    assert "rocket_silo_observations" not in (
        'factorio_mod/rocket_launch_events/'
        'factorio_mod/rocket_launch_observations/'
    )


@pytest.fixture
def lua():
    if lupa is None:
        pytest.skip("pip install lupa for behavioural Lua validation")
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(_STUB)
    runtime.execute(f'dofile("{MODULE.as_posix()}")')
    return runtime


def _set_payload(lua, fields: dict | None) -> None:
    if fields is None:
        lua.execute("next_payload = nil")
        return
    parts = []
    for key, value in fields.items():
        if isinstance(value, bool):
            encoded = "true" if value else "false"
        elif isinstance(value, (int, float)):
            encoded = str(value)
        elif isinstance(value, str):
            encoded = repr(value)
        else:
            raise TypeError(f"unsupported test payload value: {value!r}")
        parts.append(f"{key} = {encoded}")
    lua.execute("next_payload = { " + ", ".join(parts) + " }")


def _observe(lua) -> None:
    lua.execute(
        'commands.registered["rocket_silo_observation"]({ parameter = "{}" })'
    )


def _report(lua, index: int = 1):
    return lua.globals().write_at(index)["payload"]


def _add_silo(
    lua, surface: str = "nauvis", force: str = "player",
    unit: int = 7, x: float = 1.5, y: float = 2.5,
):
    lua.execute(
        "do "
        f"local surface = game.surfaces[{surface!r}] "
        f"local force = game.forces[{force!r}] "
        f"local silo = make_silo(surface, force, {unit}, {x}, {y}) "
        "surface.entities[#surface.entities + 1] = silo "
        "end"
    )
    return lua.globals().last_silo(surface)


def _to_python(value):
    if lupa is not None and lupa.lua_type(value) == "table":
        keys = list(value.keys())
        if keys and all(isinstance(key, int) for key in keys):
            if sorted(keys) == list(range(1, max(keys) + 1)):
                return [_to_python(value[index]) for index in range(1, max(keys) + 1)]
        return {str(key): _to_python(item) for key, item in value.items()}
    return value


def _validator() -> Draft7Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def test_observes_existing_silo_with_request_correlation(lua) -> None:
    silo = _add_silo(lua)
    lua.globals().attach_rocket(silo, "nauvis", True)
    _set_payload(lua, {
        "request_id": "req-42",
        "surface": "nauvis",
        "force": "player",
    })
    _observe(lua)

    assert lua.globals().write_count() == 1
    write = lua.globals().write_at(1)
    assert write["path"] == (
        "factorio_mod/rocket_silo_observations/rocket_silo_observation_req-42.json"
    )
    assert write["append"] is False
    report = _report(lua)
    assert report["schema_version"] == "1.0.0"
    assert report["kind"] == "rocket_silo_observation"
    assert report["ok"] is True
    assert report["request_id"] == "req-42"
    assert report["tick"] == 1000
    assert report["surface"] == "nauvis"
    assert report["force"] == "player"
    assert report["silo_count"] == 1
    assert report["requested"]["unit_number"]["state"] == "absent"

    observed = report["silos"][1]
    assert observed["prototype_name"]["state"] == "available"
    assert observed["prototype_name"]["value"] == "rocket-silo"
    assert observed["unit_number"]["state"] == "available"
    assert observed["unit_number"]["value"] == 7
    assert observed["position"]["state"] == "available"
    assert observed["position"]["value"]["x"] == 1.5
    assert observed["position"]["value"]["y"] == 2.5
    assert observed["status"]["rocket_silo"]["value"] == "building_rocket"
    assert observed["status"]["entity"]["value"] == "working"
    assert observed["parts"]["actual"]["value"] == 42
    assert observed["parts"]["required"]["value"] == 100
    assert observed["parts"]["storage_cap"]["value"] == 2000
    assert observed["recipe"]["state"] == "available"
    assert observed["recipe"]["value"] == "rocket-part"
    assert observed["auto_launch"]["source"] == "send_to_orbit_automatically"
    assert observed["auto_launch"]["fact"]["state"] == "available"
    assert observed["auto_launch"]["fact"]["value"] is False
    assert observed["rocket"]["state"] == "available"
    assert observed["rocket"]["value"]["name"] == "rocket-silo-rocket"
    assert observed["rocket"]["value"]["unit_number"] == 12
    assert observed["rocket"]["value"]["position"]["x"] == 3.5
    assert observed["cargo_pod"]["state"] == "available"
    assert observed["cargo_pod"]["value"]["name"] == "cargo-pod"
    assert observed["cargo_pod"]["value"]["unit_number"] == 13
    assert report["provenance"]["runtime_api_reference"] == "2.1.21"
    assert report["provenance"]["runtime_api_version"] == 6
    assert report["provenance"]["live_engine_version_verified"] is False

    assert list(_validator().iter_errors(_to_python(report))) == []


def test_absent_and_unavailable_facts_are_explicit(lua) -> None:
    silo = _add_silo(lua)
    lua.globals().fail_recipe(silo)
    lua.globals().remove_required_parts(silo)
    lua.globals().remove_auto_launch(silo)
    _set_payload(lua, {
        "request_id": "absent-facts",
        "surface": "nauvis",
        "force": "player",
    })
    _observe(lua)

    observed = _report(lua)["silos"][1]
    assert observed["recipe"]["state"] == "unavailable"
    assert "recipe unsupported" in observed["recipe"]["reason"]
    assert observed["parts"]["required"]["state"] == "absent"
    assert observed["auto_launch"]["fact"]["state"] == "absent"
    assert observed["rocket"]["state"] == "absent"
    assert observed["cargo_pod"]["state"] == "absent"
    assert "value" not in observed["rocket"]
    assert "value" not in observed["cargo_pod"]


def test_invalid_rocket_and_missing_pod_are_absent_or_unavailable(lua) -> None:
    silo = _add_silo(lua)
    lua.globals().attach_rocket(silo, "nauvis", False)
    _set_payload(lua, {
        "request_id": "no-pod",
        "surface": "nauvis",
        "force": "player",
    })
    _observe(lua)
    observed = _report(lua)["silos"][1]
    assert observed["rocket"]["state"] == "available"
    assert observed["cargo_pod"]["state"] == "absent"

    lua.globals().invalidate_rocket(silo)
    _set_payload(lua, {
        "request_id": "invalid-rocket",
        "surface": "nauvis",
        "force": "player",
    })
    _observe(lua)
    observed = _report(lua, 2)["silos"][1]
    assert observed["rocket"]["state"] == "unavailable"
    assert "invalid entity" in observed["rocket"]["reason"]
    assert observed["cargo_pod"]["state"] == "absent"


def test_unit_number_filter_selects_one_silo_and_reports_the_request(lua) -> None:
    _add_silo(lua, unit=7, x=1.5, y=2.5)
    _add_silo(lua, unit=9, x=3.5, y=4.5)
    _set_payload(lua, {
        "request_id": "one-silo",
        "surface": "nauvis",
        "force": "player",
        "unit_number": 9,
    })
    _observe(lua)

    report = _report(lua)
    assert report["requested"]["unit_number"]["state"] == "available"
    assert report["requested"]["unit_number"]["value"] == 9
    assert report["silo_count"] == 1
    assert report["silos"][1]["unit_number"]["value"] == 9


def test_missing_requested_unit_number_reports_an_empty_scope(lua) -> None:
    _add_silo(lua, unit=7)
    _set_payload(lua, {
        "request_id": "missing-silo",
        "surface": "nauvis",
        "force": "player",
        "unit_number": 999,
    })
    _observe(lua)

    report = _report(lua)
    assert report["ok"] is True
    assert report["silo_count"] == 0
    assert len(report["silos"]) == 0
    assert report["requested"]["unit_number"]["value"] == 999


def test_empty_silo_scope_serialises_silos_as_an_array(lua) -> None:
    _set_payload(lua, {
        "request_id": "empty-scope",
        "surface": "nauvis",
        "force": "player",
    })
    _observe(lua)

    assert '"silos":[]' in lua.globals().write_at(1)["json"]


def test_silos_are_sorted_by_stable_unit_number(lua) -> None:
    _add_silo(lua, unit=9, x=1.5, y=2.5)
    _add_silo(lua, unit=7, x=3.5, y=4.5)
    _set_payload(lua, {
        "request_id": "sorted",
        "surface": "nauvis",
        "force": "player",
    })
    _observe(lua)

    silos = _report(lua)["silos"]
    assert [silos[1]["unit_number"]["value"], silos[2]["unit_number"]["value"]] == [7, 9]


def test_explicit_surface_and_force_scope_excludes_other_silos(lua) -> None:
    _add_silo(lua, surface="nauvis", force="player", unit=7)
    _add_silo(lua, surface="planner-sandbox", force="planner", unit=9)
    _set_payload(lua, {
        "request_id": "sandbox",
        "surface": "planner-sandbox",
        "force": "planner",
    })
    _observe(lua)

    report = _report(lua)
    assert report["surface"] == "planner-sandbox"
    assert report["force"] == "planner"
    assert report["silo_count"] == 1
    assert report["silos"][1]["unit_number"]["value"] == 9


@pytest.mark.parametrize(
    "fields, expected_error",
    [
        ({"request_id": "no-surface", "force": "player"}, "non-empty surface"),
        ({"request_id": "no-force", "surface": "nauvis"}, "non-empty force"),
        (
            {"request_id": "extra-field", "surface": "nauvis", "force": "player", "launch": True},
            "unsupported field",
        ),
        (
            {"request_id": "bad-unit", "surface": "nauvis", "force": "player", "unit_number": -1},
            "non-negative integer",
        ),
        (
            {"request_id": "float-unit", "surface": "nauvis", "force": "player", "unit_number": 1.5},
            "non-negative integer",
        ),
        (
            {"request_id": "string-unit", "surface": "nauvis", "force": "player", "unit_number": "7"},
            "non-negative integer",
        ),
    ],
)
def test_malformed_scope_writes_only_a_correlated_failure(lua, fields, expected_error) -> None:
    _set_payload(lua, fields)
    _observe(lua)

    assert lua.globals().write_count() == 1
    assert lua.globals().write_at(1)["path"] == (
        "factorio_mod/rocket_silo_observations/rocket_silo_observation_"
        + fields["request_id"] + ".json"
    )
    report = _report(lua)
    assert report["ok"] is False
    assert report["request_id"] == fields["request_id"]
    assert expected_error in report["error"]
    assert "silos" not in report


@pytest.mark.parametrize(
    "surface, force, expected_error",
    [
        ("missing", "player", "Unknown surface: missing"),
        ("nauvis", "missing", "Unknown force: missing"),
    ],
)
def test_unknown_scope_fails_closed(lua, surface, force, expected_error) -> None:
    _set_payload(lua, {
        "request_id": "unknown-scope",
        "surface": surface,
        "force": force,
    })
    _observe(lua)

    report = _report(lua)
    assert report["ok"] is False
    assert expected_error in report["error"]
    assert "silos" not in report


@pytest.mark.parametrize(
    "request_id",
    [None, "", "bad id!", "../escape", "a" * 65, "semi;colon"],
)
def test_missing_or_unsafe_request_id_writes_nothing(lua, request_id) -> None:
    fields = {"surface": "nauvis", "force": "player"}
    if request_id is not None:
        fields["request_id"] = request_id
    _set_payload(lua, fields)
    _observe(lua)

    assert lua.globals().write_count() == 0
    assert len(lua.globals().storage) == 0


def test_invalid_json_writes_nothing_because_there_is_no_safe_request(lua) -> None:
    lua.execute("next_payload = 'not-a-table'")
    _observe(lua)

    assert lua.globals().write_count() == 0
