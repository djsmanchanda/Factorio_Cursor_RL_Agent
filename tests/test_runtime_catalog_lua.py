# Path: tests/test_runtime_catalog_lua.py
# Purpose: Exercise the read-only runtime technology/prototype export against focused Factorio API stubs.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "factorio_mod" / "runtime_catalog.lua"
SCHEMA = ROOT / "schemas" / "runtime_catalog.schema.json"

try:
    import lupa
except ImportError:  # pragma: no cover - static coverage remains useful without Lua.
    lupa = None


pytestmark = pytest.mark.lua


_STUB = r'''
defines = { direction = { north = 0, east = 4, south = 8, west = 12 } }

commands = { registered = {} }
function commands.add_command(name, _, callback)
  commands.registered[name] = callback
end

written = {}
helpers = {
  write_file = function(path, json_text, append)
    written[#written + 1] = { path = path, json = json_text, append = append }
  end
}
rcon = { print = function(_) end }

script = {
  active_mods = {
    base = "2.1.24",
    ["space-age"] = "2.1.24",
    factorio_cursor_rl_agent = "0.2.0"
  }
}

local function new_tech(name, options)
  local prototype = {
    max_level = options.max_level or 1,
    upgrade = options.upgrade or false,
    allows_productivity = true,
    hidden = false,
    essential = false,
    visible_when_disabled = true,
    effects = options.effects or {},
    research_trigger = options.trigger,
    unit = options.unit,
    research_unit_count = options.unit_count or 0,
    research_unit_energy = options.unit_energy or 0,
    research_unit_count_formula = options.formula
  }
  local technology = {
    name = name,
    researched = options.researched or false,
    enabled = true,
    level = options.level or 1,
    prototype = prototype,
    prerequisites = {},
    successors = {},
    research_unit_count = options.unit_count or 0,
    research_unit_energy = options.unit_energy or 0,
    research_unit_ingredients = options.ingredients or {},
    research_unit_count_formula = options.formula
  }
  return technology
end

local technologies = {}
local function add_tech(name, options)
  local technology = new_tech(name, options)
  technologies[name] = technology
  return technology
end

local automation = add_tech("automation", {
  researched = true, unit_count = 10, unit_energy = 60000, unit = { time = 10 },
  ingredients = { { name = "automation-science-pack", amount = 1 } },
  effects = { { type = "unlock-recipe", recipe = "automation-science-pack" } }
})
local logistics = add_tech("logistics", {
  unit_count = 20, unit_energy = 120000, unit = { time = 10 },
  ingredients = { { name = "automation-science-pack", amount = 1 } },
  effects = { { type = "unlock-recipe", recipe = "transport-belt" } }
})
local steel = add_tech("steel-processing", {
  researched = true, unit_count = 50, unit_energy = 300000, unit = { time = 5 },
  ingredients = { { name = "automation-science-pack", amount = 1 } },
  effects = {
    { type = "unlock-recipe", recipe = "steel-plate" },
    { type = "unlock-recipe", recipe = "steel-chest" }
  }
})
local mining = add_tech("mining-productivity-3", {
  level = 3, unit_count = 500, unit_energy = 2400000,
  formula = "2500*(L-3)", max_level = 4294967295,
  ingredients = {
    { name = "automation-science-pack", amount = 1 },
    { name = "logistic-science-pack", amount = 1 },
    { name = "chemical-science-pack", amount = 1 }
  },
  effects = { { type = "mining-drill-productivity-bonus", modifier = 0.1 } }
})
local damage = add_tech("physical-projectile-damage-1", {
  unit_count = 100, unit_energy = 600000, unit = { time = 10 },
  ingredients = { { name = "automation-science-pack", amount = 1 } },
  effects = { { type = "ammo-damage", ammo_category = "bullet", modifier = 0.1 } }
})
local landfill = add_tech("landfill", {
  trigger = { type = "craft-item", item = "wood", count = 10 },
  effects = { { type = "unlock-recipe", recipe = "landfill" } }
})

automation.successors.logistics = logistics
logistics.prerequisites.automation = automation

local recipes = {
  ["automation-science-pack"] = { enabled = true },
  ["transport-belt"] = { enabled = true },
  ["steel-plate"] = { enabled = true },
  ["steel-chest"] = { enabled = false },
  ["landfill"] = { enabled = true },
  ["assembling-machine-2"] = { enabled = true },
  ["chemical-plant"] = { enabled = false },
  ["electric-furnace"] = { enabled = true },
  ["lab"] = { enabled = true },
  ["beacon"] = { enabled = true },
  ["offshore-pump"] = { enabled = true },
  ["electric-mining-drill"] = { enabled = true },
  ["pipe-to-ground"] = { enabled = true },
  ["transport-belt"] = { enabled = true }
}

local force = {
  name = "player",
  technologies = technologies,
  recipes = recipes,
  current_research = mining,
  previous_research = automation,
  research_progress = 0.25,
  research_queue = { mining },
  research_enabled = true,
  laboratory_speed_modifier = 2.5,
  mining_drill_productivity_bonus = 0.2,
  worker_robots_speed_modifier = 1.5
}

local function connection(x, y, direction, flow_direction, connection_type, category)
  return {
    positions = { { x = x, y = y }, { x = -y, y = x }, { x = -x, y = -y }, { x = y, y = -x } },
    direction = direction,
    flow_direction = flow_direction,
    connection_type = connection_type,
    connection_category = category
  }
end

local function fluidbox(index, production_type, volume, filter, connections)
  return setmetatable({
    index = index,
    production_type = production_type,
    filter = filter,
    pipe_connections = connections
  }, { __index = function(_, key)
    if key == "volume" then error("LuaFluidBoxPrototype has no volume property") end
  end })
end

local function new_entity(name, entity_type, options)
  local entity = {
    name = name,
    type = entity_type,
    tile_width = options.tile_width or 1,
    tile_height = options.tile_height or 1,
    collision_box = options.collision_box or { left_top = { x = -0.5, y = -0.5 }, right_bottom = { x = 0.5, y = 0.5 } },
    selection_box = { left_top = { x = -1, y = -1 }, right_bottom = { x = 1, y = 1 } },
    supports_direction = true,
    items_to_place_this = options.items_to_place_this or {},
    fluidbox_prototypes = options.fluidbox_prototypes or {},
    crafting_categories = options.crafting_categories,
    ingredient_count = options.ingredient_count,
    fixed_recipe = options.fixed_recipe,
    module_inventory_size = options.module_inventory_size,
    allowed_effects = options.allowed_effects,
    allowed_module_categories = options.allowed_module_categories,
    effect_receiver = options.effect_receiver,
    energy_usage = options.energy_usage,
    electric_energy_source_prototype = options.electric_energy_source_prototype,
    fluid_energy_source_prototype = options.fluid_energy_source_prototype,
    lab_inputs = options.lab_inputs,
    mining_speed = options.mining_speed,
    resource_categories = options.resource_categories,
    max_underground_distance = options.max_underground_distance,
    distribution_effectivity = options.distribution_effectivity,
    fast_replaceable_group = options.fast_replaceable_group
  }
  if options.crafting_speed then
    function entity.get_crafting_speed(...) assert(select('#', ...) == 0); return options.crafting_speed end
  end
  if options.max_energy_usage then
    function entity.get_max_energy_usage(...) assert(select('#', ...) == 0); return options.max_energy_usage end
  end
  if options.max_power_output then
    function entity.get_max_power_output(...) assert(select('#', ...) == 0); return options.max_power_output end
  end
  if options.researching_speed then
    function entity.get_researching_speed(...) assert(select('#', ...) == 0); return options.researching_speed end
  end
  if options.pumping_speed then
    function entity.get_pumping_speed(...) assert(select('#', ...) == 0); return options.pumping_speed end
  end
  if options.supply_area_distance then
    function entity.get_supply_area_distance(...) assert(select('#', ...) == 0); return options.supply_area_distance end
  end
  return setmetatable(entity, { __index = function(_, key)
    if key == "passive_usage" then error("LuaEntityPrototype has no passive_usage property") end
  end })
end

local entities = {
  new_entity("assembling-machine-2", "assembling-machine", {
    tile_width = 3, tile_height = 3,
    collision_box = { left_top = { x = -1.5, y = -1.5 }, right_bottom = { x = 1.5, y = 1.5 } },
    crafting_categories = { crafting = true, ["crafting-with-fluid-or-metallurgy"] = true },
    ingredient_count = 6, crafting_speed = 0.75, fixed_recipe = {name = "rocket-part"},
    energy_usage = 90, max_energy_usage = 90,
    electric_energy_source_prototype = { buffer_capacity = 5200, drain = 1000, usage_priority = "secondary-input" },
    module_inventory_size = 4,
    allowed_effects = { speed = true, productivity = true, consumption = true, pollution = true },
    allowed_module_categories = { speed = true, effectivity = true, quality = true },
    effect_receiver = {
      base_effect = {}, uses_beacon_effects = true,
      uses_module_effects = true, uses_surface_effects = true
    },
    items_to_place_this = { { name = "assembling-machine-2", count = 1 } },
    fast_replaceable_group = "assembling-machine"
  }),
  new_entity("chemical-plant", "assembling-machine", {
    tile_width = 3, tile_height = 3,
    collision_box = { left_top = { x = -1.5, y = -1.5 }, right_bottom = { x = 1.5, y = 1.5 } },
    crafting_categories = { chemistry = true },
    ingredient_count = 4, crafting_speed = 1.25,
    energy_usage = 250, max_energy_usage = 250,
    electric_energy_source_prototype = { buffer_capacity = 5200, drain = 1000, usage_priority = "secondary-input" },
    module_inventory_size = 2,
    allowed_effects = { productivity = true, consumption = true, pollution = true },
    allowed_module_categories = { speed = true, effectivity = true },
    fluidbox_prototypes = {
      fluidbox(1, "input", 200, nil, { connection(-1, 0.5, 12, "input", "normal", { "default" }) }),
      fluidbox(2, "output", 200, nil, { connection(1, 0.5, 4, "output", "normal", "default") })
    },
    items_to_place_this = { { name = "chemical-plant", count = 1 } }
  }),
  new_entity("electric-furnace", "furnace", {
    tile_width = 3, tile_height = 3,
    collision_box = { left_top = { x = -1.5, y = -1.5 }, right_bottom = { x = 1.5, y = 1.5 } },
    crafting_categories = { smelting = true },
    ingredient_count = 1, crafting_speed = 2,
    energy_usage = 180, max_energy_usage = 180,
    electric_energy_source_prototype = { buffer_capacity = 5200, drain = 1000, usage_priority = "secondary-input" },
    module_inventory_size = 2,
    allowed_effects = { productivity = true },
    items_to_place_this = { { name = "electric-furnace", count = 1 } }
  }),
  new_entity("lab", "lab", {
    tile_width = 3, tile_height = 3,
    collision_box = { left_top = { x = -1.5, y = -1.5 }, right_bottom = { x = 1.5, y = 1.5 } },
    lab_inputs = { "logistic-science-pack", "automation-science-pack" },
    researching_speed = 1,
    energy_usage = 60, max_energy_usage = 60,
    electric_energy_source_prototype = { buffer_capacity = 5200, drain = 1000, usage_priority = "secondary-input" },
    module_inventory_size = 2,
    allowed_effects = { speed = true, productivity = true },
    effect_receiver = {
      base_effect = { speed = 0.1 }, uses_beacon_effects = true,
      uses_module_effects = true, uses_surface_effects = true
    },
    items_to_place_this = { { name = "lab", count = 1 } }
  }),
  new_entity("beacon", "beacon", {
    tile_width = 3, tile_height = 3,
    collision_box = { left_top = { x = -1.5, y = -1.5 }, right_bottom = { x = 1.5, y = 1.5 } },
    supply_area_distance = 9, distribution_effectivity = 0.5,
    energy_usage = 480, max_energy_usage = 480,
    electric_energy_source_prototype = { buffer_capacity = 5200, drain = 1000, usage_priority = "secondary-input" },
    module_inventory_size = 2,
    allowed_effects = { speed = true, effectivity = true },
    effect_receiver = {
      base_effect = {}, uses_beacon_effects = false,
      uses_module_effects = true, uses_surface_effects = true
    },
    items_to_place_this = { { name = "beacon", count = 1 } }
  }),
  new_entity("steam-engine", "generator", {
    tile_width = 3, tile_height = 5,
    collision_box = { left_top = { x = -1.5, y = -2.5 }, right_bottom = { x = 1.5, y = 2.5 } },
    fluidbox_prototypes = {
      fluidbox(1, "input-output", 200, { name = "steam" }, {
        connection(0, -2.5, 0, "input-output", "normal", { "default" })
      })
    },
    fluid_energy_source_prototype = {
      burns_fluid = false, fluid_usage_per_tick = 0.1, effectivity = 1,
      maximum_temperature = 500, scale_fluid_usage = true,
      fluid_box = { filter = { name = "steam" } }
    },
    max_power_output = 900000,
    items_to_place_this = { { name = "steam-engine", count = 1 } }
  }),
  new_entity("offshore-pump", "offshore-pump", {
    fluidbox_prototypes = {
      fluidbox(1, "output", 200, { name = "water" }, {
        connection(0, 0.5, 4, "output", "normal", { "default" })
      })
    },
    pumping_speed = 1.2,
    items_to_place_this = { { name = "offshore-pump", count = 1 } }
  }),
  new_entity("electric-mining-drill", "mining-drill", {
    tile_width = 3, tile_height = 3,
    collision_box = { left_top = { x = -1.5, y = -1.5 }, right_bottom = { x = 1.5, y = 1.5 } },
    mining_speed = 0.5, resource_categories = { ["basic-solid"] = true },
    energy_usage = 90, max_energy_usage = 90,
    electric_energy_source_prototype = { buffer_capacity = 5200, drain = 1000, usage_priority = "secondary-input" },
    module_inventory_size = 3,
    allowed_effects = { speed = true, productivity = true },
    effect_receiver = {
      base_effect = {}, uses_beacon_effects = true,
      uses_module_effects = true, uses_surface_effects = true
    },
    items_to_place_this = { { name = "electric-mining-drill", count = 1 } }
  }),
  new_entity("pipe-to-ground", "pipe-to-ground", {
    max_underground_distance = 10,
    fluidbox_prototypes = {
      fluidbox(1, "input-output", 100, nil, {
        connection(0, -0.5, 0, "input-output", "underground", { "default" })
      })
    },
    items_to_place_this = { { name = "pipe-to-ground", count = 1 } }
  }),
  new_entity("transport-belt", "transport-belt", {
    items_to_place_this = { { name = "transport-belt", count = 1 } }
  })
}

prototypes = {
  entity = entities,
  fluid = {
    water = {
      default_temperature = 15, max_temperature = 100, heat_capacity = 1000,
      fuel_value = 0, gas_temperature = 100, emissions_multiplier = 1, hidden = false
    },
    steam = {
      default_temperature = 15, max_temperature = 500, heat_capacity = 200,
      fuel_value = 0, gas_temperature = 500, emissions_multiplier = 1, hidden = false
    }
  }
}

game = {
  tick = 4242,
  forces = { player = force },
  surfaces = { nauvis = { name = "nauvis", index = 1, valid = true } }
}
'''


def _lua_to_python(value):
    if lupa is not None and lupa.lua_type(value) == "table":
        keys = list(value.keys())
        if keys and all(isinstance(key, int) for key in keys):
            ordered = sorted(keys)
            if ordered == list(range(1, len(ordered) + 1)):
                return [_lua_to_python(value[index]) for index in ordered]
        return {str(key): _lua_to_python(value[key]) for key in keys}
    return value


def _python_to_lua(runtime, value):
    if isinstance(value, dict):
        table = runtime.table()
        for key, item in value.items():
            table[key] = _python_to_lua(runtime, item)
        return table
    if isinstance(value, list):
        table = runtime.table()
        for index, item in enumerate(value, 1):
            table[index] = _python_to_lua(runtime, item)
        return table
    return value


def _json_from_lua(value):
    return json.dumps(_lua_to_python(value), sort_keys=True, separators=(",", ":"))


@pytest.fixture
def lua():
    if lupa is None:
        pytest.skip("pip install lupa for behavioural Lua validation")
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(_STUB)
    runtime.globals().helpers.table_to_json = _json_from_lua
    runtime.globals().helpers.json_to_table = lambda text: _python_to_lua(runtime, json.loads(text))
    runtime.execute(f'dofile("{MODULE.as_posix()}")')
    return runtime


def _run(runtime, parameter: str) -> dict:
    runtime.globals().commands.registered["export_runtime_catalog"](runtime.table(parameter=parameter))
    written = runtime.globals().written
    assert len(written) == 1
    entry = written[1]
    assert entry["append"] is False
    return json.loads(entry["json"])


def _technology(report: dict, name: str) -> dict:
    return next(technology for technology in report["technologies"] if technology["name"] == name)


def _entity(report: dict, name: str) -> dict:
    return next(entity for entity in report["entities"] if entity["name"] == name)


@pytest.mark.parametrize("bad", [False, True])
def test_pole_reach_is_exported_for_normal_quality_or_marked_unknown(lua, bad):
    lua.execute('''
      prototypes.entity[#prototypes.entity+1]={
        name="test-pole", type="electric-pole", tile_width=1, tile_height=1,
        collision_box={left_top={x=-.4,y=-.4},right_bottom={x=.4,y=.4}},
        items_to_place_this={{name="test-pole",count=1}},fluidbox_prototypes={},
        get_max_wire_distance=function(quality) assert(quality=="normal"); return 9 end,
        get_supply_area_distance=function(quality) assert(quality=="normal"); return 3.5 end}
    ''')
    if bad:
        lua.execute('prototypes.entity[#prototypes.entity].get_supply_area_distance=function() error("unreadable") end')
    report = _run(lua, '{"request_id":"pole","surface":"nauvis","force":"player"}')
    assert not list(Draft7Validator(json.loads(SCHEMA.read_text())).iter_errors(report))
    pole = _entity(report, "test-pole")
    if bad:
        assert "electric_pole" not in pole
        assert "normal-quality electric pole reach unavailable" in pole["unknown_fields"]
    else:
        assert pole["electric_pole"] == {"quality": "normal", "max_wire_distance": 9, "supply_area_distance": 3.5}


@pytest.mark.parametrize("items", [
    [{"count": 1}, {"name": "later", "count": 1}],
    [{"name": "bad", "count": 0}, {"name": "later", "count": 1}],
    [{"name": "bad", "count": 1.5}, {"name": "later", "count": 1}],
    {"2": {"name": "later", "count": 1}},
])
def test_malformed_placement_array_never_promotes_later_item(lua, items):
    lua.globals().prototypes.entity[2].items_to_place_this = _python_to_lua(lua, items)
    report = _run(lua, '{"force":"player","surface":"nauvis"}')
    chemical = _entity(report, "chemical-plant")
    assert chemical["placement"]["items"] == []
    assert "placement items unavailable or malformed" in chemical["unknown_fields"]
    Draft7Validator(json.loads(SCHEMA.read_text())).validate(report)


def test_export_preserves_first_placement_item_order(lua):
    items = [{"name": "first-item", "count": 1}, {"name": "second-item", "count": 1}]
    lua.globals().prototypes.entity[2].items_to_place_this = _python_to_lua(lua, items)
    report = _run(lua, '{"force":"player","surface":"nauvis"}')
    assert _entity(report, "chemical-plant")["placement"]["items"] == items


@pytest.mark.parametrize("entity_type", ["container", "logistic-container", "roboport"])
def test_construction_support_types_are_exported(lua, entity_type):
    lua.globals().prototypes.entity[2].type = entity_type
    report = _run(lua, '{"force":"player","surface":"nauvis"}')
    assert _entity(report, "chemical-plant")["type"] == entity_type
    assert entity_type in report["provenance"]["scope"]["entity_types"]


def test_exported_placement_can_form_pinned_mapping(lua):
    import hashlib
    from core.runtime_placement_mapping import mapping_from_runtime_catalog

    report = _run(lua, '{"force":"player","surface":"nauvis"}')
    raw = json.dumps(report).encode()
    mapping = mapping_from_runtime_catalog(
        raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
        entity_names=["chemical-plant", "pipe-to-ground"], surface="nauvis", force="player",
        active_mods=report["provenance"]["active_mods"],
    )
    assert mapping["entities"] == {"chemical-plant": "chemical-plant", "pipe-to-ground": "pipe-to-ground"}


def test_exported_placement_flows_into_bound_ghost_artifact(lua):
    import hashlib
    from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
    from core.optimizer_construction_state import ConstructionScope
    from planners.ghost_line_artifact import derive_ghost_artifact_from_runtime_catalog

    report = _run(lua, '{"force":"player","surface":"nauvis","request_id":"fixture"}')
    raw = json.dumps(report).encode()
    capture = {"catalog_bytes": raw, "expected_sha256": hashlib.sha256(raw).hexdigest(),
               "surface": "nauvis", "force": "player", "active_mods": report["provenance"]["active_mods"]}
    plan = {"phases": [{"name": "test", "actions": [
        {"action_type": "place_ghost", "entity": "chemical-plant", "position": {"x": 0, "y": 0}}
    ]}]}
    artifact = derive_ghost_artifact_from_runtime_catalog(plan, placement_capture=capture)
    bound = bind_artifact_to_construction_plan(
        artifact, scope=ConstructionScope("synthetic", "a" * 64, "nauvis", "player"),
        project_id="test", transaction_id="test", throughput_output="test-output",
        throughput_target=1, throughput_unit="items/second",
    )
    assert dict(bound.plan.required_materials) == {"chemical-plant": 1}
    assert not bound.execution_authorized


def test_runtime_catalog_is_registered_once() -> None:
    control = (ROOT / "factorio_mod" / "control.lua").read_text(encoding="utf-8")
    assert control.count('require("runtime_catalog")') == 1


def test_runtime_catalog_module_is_read_only_by_construction() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert 'commands.add_command(\n  "export_runtime_catalog"' in source
    for forbidden in (
        "create_force", "create_entity", "create_inventory", "set_research",
        "add_research", "insert_item", "remove_item", "launch_rocket",
        "game.reset", "entity.destroy", ".revive(",
    ):
        assert forbidden not in source


def test_successful_export_validates_and_captures_technology_state(lua) -> None:
    report = _run(lua, "player")
    Draft7Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(report)

    assert report["ok"] is True
    assert report["kind"] == "runtime_catalog"
    provenance = report["provenance"]
    assert provenance["active_mods"] == {
        "base": "2.1.24",
        "factorio_cursor_rl_agent": "0.2.0",
        "space-age": "2.1.24",
    }
    assert provenance["mod_signature"] == (
        "base@2.1.24;factorio_cursor_rl_agent@0.2.0;space-age@2.1.24"
    )
    assert provenance["engine_version"]["available"] is False
    assert provenance["engine_version"]["base_mod_version"] == "2.1.24"
    assert provenance["scope"]["force"] == "player"
    assert provenance["scope"]["surface"] == "unspecified"
    assert provenance["scope"]["surface_requested"] is False

    force_state = report["force_state"]
    assert force_state["current_research"] == {"name": "mining-productivity-3", "level": 3}
    assert force_state["previous_research"] == {"name": "automation", "level": 1}
    assert force_state["research_queue"] == [{"name": "mining-productivity-3", "level": 3}]
    assert force_state["bonuses"]["laboratory_speed_modifier"] == 2.5

    steel = _technology(report, "steel-processing")
    assert steel["researched"] is True
    assert steel["level"] == 1
    assert steel["research"]["unit_count"] == 50
    assert steel["research"]["unit_energy"] == 300000
    assert steel["research"]["unit_time_seconds"] == 5
    assert steel["research"]["ingredients"] == [
        {"name": "automation-science-pack", "kind": "item", "amount": 1}
    ]
    assert steel["unlocked_recipes"] == ["steel-chest", "steel-plate"]
    effects = {effect["recipe"]: effect for effect in steel["effects"] if "recipe" in effect}
    assert effects["steel-plate"]["handled"] is True
    assert effects["steel-plate"]["recipe_enabled"] is True
    assert effects["steel-chest"]["recipe_enabled"] is False

    mining = _technology(report, "mining-productivity-3")
    assert mining["level"] == 3
    assert mining["max_level"] == 4294967295
    assert mining["research"]["unit_count_formula"] == "2500*(L-3)"
    assert mining["effects"][0]["handled"] is False
    assert mining["effects"][0]["details"] == {"modifier": 0.1}
    assert "not interpreted" in mining["effects"][0]["unsupported_reason"]

    landfill = _technology(report, "landfill")
    assert landfill["research"]["trigger"]["type"] == "craft-item"
    assert landfill["research"]["trigger"]["details"] == {"item": "wood", "count": 10}

    automation = _technology(report, "automation")
    logistics = _technology(report, "logistics")
    assert automation["successors"] == ["logistics"]
    assert logistics["prerequisites"] == ["automation"]


def test_successful_export_captures_machine_geometry_energy_fluids_and_modules(lua) -> None:
    report = _run(lua, "player")

    assert _entity(report, "assembling-machine-2")["crafting"]["fixed_recipe"] == "rocket-part"

    chemical = _entity(report, "chemical-plant")
    assert chemical["tile_width"] == 3
    assert chemical["tile_height"] == 3
    assert chemical["crafting"]["categories"] == ["chemistry"]
    assert chemical["crafting"]["speed"] == 1.25
    assert chemical["modules"]["inventory_size"] == 2
    assert chemical["modules"]["allowed_effects"] == ["consumption", "pollution", "productivity"]
    assert chemical["energy"]["source"] == "electric"
    assert chemical["energy"]["electric"]["usage_priority"] == "secondary-input"
    assert chemical["placement"]["recipe"] == {"name": "chemical-plant", "enabled": False}
    assert len(chemical["fluidboxes"]) == 2
    assert "volume" not in chemical["fluidboxes"][0]
    assert "fluidbox volume unavailable" in chemical["unknown_fields"]
    first_connection = chemical["fluidboxes"][0]["pipe_connections"][0]
    assert first_connection["positions"] == [
        {"x": -1, "y": 0.5}, {"x": -0.5, "y": -1},
        {"x": 1, "y": -0.5}, {"x": 0.5, "y": 1},
    ]
    assert first_connection["direction"] == 12
    assert first_connection["direction_name"] == "west"
    assert first_connection["flow_direction"] == "input"
    assert first_connection["connection_category"] == ["default"]
    assert chemical["fluidboxes"][1]["production_type"] == "output"

    pump = _entity(report, "offshore-pump")
    assert pump["pump"]["pumping_speed"] == 1.2
    assert pump["fluidboxes"][0]["filter"] == {"name": "water", "kind": "fluid"}

    furnace = _entity(report, "electric-furnace")
    assert furnace["crafting"]["categories"] == ["smelting"]
    assert furnace["crafting"]["speed"] == 2
    assert furnace["modules"]["inventory_size"] == 2

    generator = _entity(report, "steam-engine")
    assert generator["energy"]["source"] == "fluid"
    assert generator["energy"]["max_power_output"] == 900000
    assert generator["energy"]["fluid"]["burns_fluid"] is False
    assert generator["energy"]["fluid"]["filter"] == {"name": "steam", "kind": "fluid"}

    lab = _entity(report, "lab")
    assert lab["lab"]["inputs"] == ["automation-science-pack", "logistic-science-pack"]
    assert lab["lab"]["researching_speed"] == 1
    assert lab["modules"]["effect_receiver"]["base_effect"] == {"speed": 0.1}

    beacon = _entity(report, "beacon")
    assert beacon["beacon"] == {"supply_area_distance": 9, "distribution_effectivity": 0.5}

    drill = _entity(report, "electric-mining-drill")
    assert drill["mining"]["mining_speed"] == 0.5
    assert drill["mining"]["resource_categories"] == ["basic-solid"]

    pipe = _entity(report, "pipe-to-ground")
    assert pipe["max_underground_distance"] == 10
    assert pipe["fluidboxes"][0]["pipe_connections"][0]["connection_type"] == "underground"

    fluids = {fluid["name"]: fluid for fluid in report["fluids"]}
    assert set(fluids) == {"steam", "water"}
    assert fluids["water"]["max_temperature"] == 100
    assert fluids["water"]["heat_capacity"] == 1000


def test_incomplete_pipe_positions_are_not_reported_as_geometry(lua) -> None:
    lua.execute('prototypes.entity[2].fluidbox_prototypes[1].pipe_connections[1].positions[4] = nil')
    report = _run(lua, "player")
    Draft7Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(report)
    chemical = _entity(report, "chemical-plant")
    assert "positions" not in chemical["fluidboxes"][0]["pipe_connections"][0]
    assert "pipe connection positions unavailable or incomplete" in chemical["unknown_fields"]


def test_export_round_trips_json_scope_and_request_id(lua) -> None:
    report = _run(
        lua,
        json.dumps({"force": "player", "surface": "nauvis", "request_id": "req-1"}),
    )

    assert report["provenance"]["scope"]["surface"] == "nauvis"
    assert report["provenance"]["scope"]["surface_requested"] is True
    assert report["request_id"] == "req-1"
    Draft7Validator(json.loads(SCHEMA.read_text())).validate(report)
    assert lua.globals().written[1]["path"] == (
        "factorio_mod/runtime_catalogs/runtime_catalog_req-1.json"
    )


def test_failure_export_preserves_safe_request_id(lua):
    report = _run(lua, json.dumps({"force": "missing", "surface": "nauvis", "request_id": "req-2"}))
    assert report["ok"] is False and report["request_id"] == "req-2"
    Draft7Validator(json.loads(SCHEMA.read_text())).validate(report)


def test_unknown_force_writes_a_failure_report_without_creating_a_force(lua) -> None:
    report = _run(lua, "missing-force")
    Draft7Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(report)

    assert report["ok"] is False
    assert "Unknown force" in report["error"]
    assert lua.eval("game.forces['missing-force'] == nil") is True


def test_recipe_fluid_parts_keep_engine_order_and_feed_port_assignment(lua):
    import hashlib
    from core.fluid_topology import load_fluid_prototype_catalog, recipe_box_assignment
    lua.execute("""
    force_recipes = game.forces.player.recipes
    force_recipes["basic-oil-processing"] = { enabled = true,
      ingredients = { { type = "fluid", name = "crude-oil", amount = 100, fluidbox_index = 2 } },
      products = { { type = "fluid", name = "petroleum-gas", amount = 45, fluidbox_index = 3 } } }
    force_recipes["heavy-oil-cracking"] = { enabled = true,
      ingredients = { { type = "fluid", name = "water", amount = 30 }, { type = "fluid", name = "heavy-oil", amount = 40, fluidbox_index = 0 } },
      products = { { type = "fluid", name = "light-oil", amount = 30 } } }
    force_recipes["sulfuric-acid"] = { enabled = true,
      ingredients = { { type = "item", name = "sulfur", amount = 5 }, { type = "fluid", name = "water", amount = 100 } },
      products = { { type = "fluid", name = "sulfuric-acid", amount = 50 } } }
    force_recipes["iron-gear-wheel"] = { enabled = true,
      ingredients = { { type = "item", name = "iron-plate", amount = 2 } },
      products = { { type = "item", name = "iron-gear-wheel", amount = 1 } } }
    """)
    report = _run(lua, json.dumps({"force": "player", "surface": "nauvis"}))
    Draft7Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(report)
    assert report["provenance"]["scope"]["recipe_scope"] == "unlocks_and_fluid_ports"
    rows = {row["name"]: row for row in report["recipe_fluid_boxes"]}
    assert set(rows) == {"basic-oil-processing", "heavy-oil-cracking", "sulfuric-acid"}
    assert rows["heavy-oil-cracking"]["ingredients"] == [{"name": "water"}, {"name": "heavy-oil"}]
    assert rows["basic-oil-processing"]["products"] == [{"name": "petroleum-gas", "fluidbox_index": 3}]
    raw = lua.globals().written[1]["json"].encode()
    catalog = load_fluid_prototype_catalog(raw, expected_sha256=hashlib.sha256(raw).hexdigest(), surface="nauvis",
                                           force="player", active_mods=report["provenance"]["active_mods"])
    plant = catalog.entities["chemical-plant"]  # stub: one input box, one output box
    assert recipe_box_assignment(plant, catalog.recipes["sulfuric-acid"]) == {1: "water", 2: "sulfuric-acid"}
    assert recipe_box_assignment(plant, catalog.recipes["heavy-oil-cracking"]) is None  # two fluids, one box


def test_exported_research_prerequisites_feed_science_demand(lua):
    import hashlib
    from core.research_path_demand import research_path_demand

    report = _run(lua, json.dumps({"force": "player", "surface": "nauvis"}))
    raw = lua.globals().written[1]["json"].encode()
    result = research_path_demand(raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
                                  surface="nauvis", force="player",
                                  active_mods=report["provenance"]["active_mods"], targets=["logistics"])
    assert result["graph_read_complete"] is True
    assert result["prerequisites_researched_frontier"] == ["logistics"]
    assert result["known_gross_science_bill"] == {"automation-science-pack": "20"}
    assert result["bill_complete_for_exported_graph"] is True


@pytest.mark.parametrize("corruption", [
    'game.forces.player.technologies.logistics.prerequisites = nil',
    'game.forces.player.technologies.logistics.prerequisites = {bad = {name = "different"}}',
    'game.forces.player.technologies.logistics.prerequisites = {absent = {name = "absent"}}',
    'game.forces.player.technologies.logistics.prerequisites = {[1] = {name = "automation"}}',
])
def test_unreadable_prerequisite_maps_never_claim_graph_completeness(lua, corruption):
    import hashlib
    from core.research_path_demand import research_path_demand

    lua.execute(corruption)
    report = _run(lua, json.dumps({"force": "player", "surface": "nauvis"}))
    Draft7Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(report)
    assert report["technology_graph"]["complete"] is False
    raw = lua.globals().written[1]["json"].encode()
    # An unreadable graph can still be examined, but cannot certify a path.
    if "prerequisites = nil" in corruption or "[1]" in corruption:
        result = research_path_demand(raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
                                      surface="nauvis", force="player",
                                      active_mods=report["provenance"]["active_mods"], targets=["logistics"])
        assert result["graph_read_complete"] is False


@pytest.mark.parametrize("corruption", [
    'ingredients = { { type = "fluid", amount = 1 } }',
    'ingredients = { { type = "fluid", name = "water", fluidbox_index = 1.5 } }',
    'ingredients = { [2] = { type = "fluid", name = "water" } }',
    'ingredients = { { type = "mystery", name = "water" } }',
])
def test_unreadable_recipe_fluid_parts_omit_the_section(lua, corruption):
    lua.execute('game.forces.player.recipes["broken"] = { enabled = true, ' + corruption + ' }')
    report = _run(lua, "player")
    Draft7Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(report)
    assert "recipe_fluid_boxes" not in report
    assert report["provenance"]["scope"]["recipe_scope"] == "unlocks_only"
    assert any("recipe_fluid_boxes is omitted" in item for item in report["unknowns"])


def test_engine_userdata_collections_are_exported(lua) -> None:
    """The live API returns LuaCustomTable/prototype userdata, not Lua tables.

    Regression: on Factorio 2.1.24 the exporter silently returned zero entities,
    technologies and fluids because it only iterated values of Lua type "table".
    """
    baseline = _run(lua, "player")
    lua.globals().written = lua.table()
    lua.execute('''
      local function custom_table(backing)
        local proxy = python.eval("object()")  -- any full userdata
        debug.setmetatable(proxy, {
          __index = backing,
          __len = function() return #backing end,
          __pairs = function() return next, backing, nil end,
        })
        return proxy
      end
      for name, fluid in pairs(prototypes.fluid) do
        local backing_fluid = fluid
        prototypes.fluid[name] = custom_table(backing_fluid)
      end
      prototypes.entity = custom_table(prototypes.entity)
      prototypes.fluid = custom_table(prototypes.fluid)
      local force = game.forces.player
      force.technologies = custom_table(force.technologies)
    ''')
    report = _run(lua, "player")
    assert baseline["entities"] and baseline["technologies"] and baseline["fluids"]
    assert len(report["entities"]) == len(baseline["entities"])
    assert len(report["technologies"]) == len(baseline["technologies"])
    assert len(report["fluids"]) == len(baseline["fluids"])
    assert report["technology_graph"]["complete"] == baseline["technology_graph"]["complete"]
