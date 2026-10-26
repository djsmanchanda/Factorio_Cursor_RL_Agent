# Path: tests/test_laya_fluid_status.py | Purpose: Exercise typed fluid observations without granting item-operation eligibility.

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from training.laya_factory_status import compact_status, compile_status, goal_demand, observed_recipes
from training.laya_saved_world import measured_outcome

pytestmark = pytest.mark.fast
ROOT = Path(__file__).resolve().parents[1]


def plastic(enabled=True):
    return {"enabled": enabled, "ingredients": [
        {"name": "coal", "type": "item", "amount": 1},
        {"name": "petroleum-gas", "type": "fluid", "amount": 20}],
        "products": [{"name": "plastic-bar", "type": "item", "amount": 2,
                      "independent_probability": 1, "shared_probability": {"min": 0, "max": 1}}],
        "energy_ticks": 60, "category": "chemistry", "categories": ["chemistry"],
        "machine_speeds": {"chemical-plant": 1}}


def world(enabled=True):
    return {"recipes": {}, "recipe_observations": {"plastic-bar": plastic(enabled)},
            "machines": [{"name": "chemical-plant", "recipe": "plastic-bar", "status": "no_input_fluid"}],
            "networks": {"1": {"items": {"coal": 12}, "reserved": {}}}}


def needs(facts, measurement=None):
    return {n["item"]: n for n in compile_status(facts, {"plastic-bar": 4}, measurement)["needs"]}


def test_fluid_input_is_visible_but_never_a_mineable_or_assemblable_item():
    rows = needs(world())
    assert rows["coal"]["required_per_s"] == 2
    fluid = rows["petroleum-gas"]
    assert fluid["required_per_s"] == 40 and fluid["consumers"] == ["plastic-bar"]
    assert fluid["material_type"] == "fluid" and fluid["need_id"] == "fluid:petroleum-gas"
    assert fluid["condition"] == "fluid_supply_unknown"
    assert fluid["stock"] is None and fluid["capacity_per_s"] is None
    assert fluid["machines_needed"] == {} and fluid["upgrade"] is None
    assert fluid["fluid_supply"] == {"routing": None, "connected_stock": None}
    assert rows["coal"]["material_type"] == "item"
    assert rows["coal"]["condition"] == "unmeasured"


def test_plastic_uses_live_yield_category_and_chemical_machine_capacity():
    facts = world()
    row = needs(facts)["plastic-bar"]
    assert row["condition"] == "input_starved" and row["recipe_availability"] == "enabled"
    assert row["capacity_per_s"] == 2 and row["machines_needed"] == {"chemical-plant": 2}
    assert row["category"] == "chemistry" and row["upgrade"] is None
    assert row["effective_capacity_per_s"] is None  # No module, beacon or delivery inference.
    facts["recipe_observations"]["plastic-bar"]["products"][0]["amount"] = 3
    facts["recipe_observations"]["plastic-bar"]["energy_ticks"] = 120
    assert needs(facts)["plastic-bar"]["capacity_per_s"] == 1.5
    facts["machines"][0]["name"] = "assembling-machine-1"
    assert needs(facts)["plastic-bar"]["capacity_per_s"] is None


def test_disabled_and_unobserved_are_distinct_even_with_legacy_recipe_present():
    facts = world(False)
    assert needs(facts)["plastic-bar"]["condition"] == "recipe_disabled"
    assert needs(facts)["plastic-bar"]["recipe_availability"] == "disabled"
    facts["recipe_observations"] = {}
    facts["recipes"]["plastic-bar"] = {"ingredients": [], "product_amount": 2, "energy_ticks": 60}
    row = needs(facts)["plastic-bar"]
    assert row["condition"] == "recipe_unknown" and row["recipe_availability"] == "unknown"
    assert row["capacity_per_s"] is None


def test_measured_plastic_output_survives_missing_recipe_observation():
    facts = world()
    facts["recipe_observations"] = {}
    row = needs(facts, {"production": {"plastic-bar": {"per_second": 4}}})["plastic-bar"]
    assert row["condition"] == "satisfied" and row["recipe_availability"] == "unknown"


@pytest.mark.parametrize("enabled,availability", [(True, "enabled"), (False, "disabled"), (None, "unknown")])
def test_observed_fluid_recipe_availability_is_separate_from_unknown_supply(enabled, availability):
    acid = plastic(enabled)
    acid["products"][0].update(name="sulfuric-acid", type="fluid")
    facts = {"recipe_observations": {"sulfuric-acid": acid}, "recipes": {}}
    board = compile_status(facts, {"sulfuric-acid": 1})
    row = next(n for n in board["needs"] if n["item"] == "sulfuric-acid")
    assert row["material_type"] == "fluid" and row["recipe_observation_state"] == "observed"
    assert row["recipe_availability"] == availability
    assert row["fluid_supply"] == {"routing": None, "connected_stock": None}
    assert row["measured_per_s"] is None and row["capacity_per_s"] is None


def test_coproduct_and_recipe_name_mismatch_remain_explicitly_unsupported():
    facts = world()
    spec = facts["recipe_observations"]["plastic-bar"]
    spec["products"].append({"name": "other", "type": "item", "amount": 1})
    row = needs(facts)["plastic-bar"]
    assert row["condition"] == "recipe_unsupported" and not row["recipe_arithmetic_supported"]
    assert row["capacity_per_s"] is None and row["machines_needed"] == {}
    assert len(needs(facts)) == 1
    spec["products"] = [{"name": "other", "type": "item", "amount": 2}]
    assert needs(facts)["plastic-bar"]["condition"] == "recipe_unsupported"


def test_solid_legacy_arithmetic_and_compact_projection_keep_their_contract():
    solid = {"iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}],
                            "product_amount": 1, "energy_ticks": 192}}
    assert goal_demand({"iron-plate": 2}, solid)["iron-ore"]["required_per_s"] == 2
    board = compile_status(world(), {"plastic-bar": 4})
    rows = {n["item"]: n for n in compact_status(board)["needs"]}
    assert rows["plastic-bar"]["recipe_availability"] == "enabled"
    assert rows["plastic-bar"]["capacity"] == 2
    assert rows["petroleum-gas"]["material_type"] == "fluid"
    assert rows["petroleum-gas"]["fluid_supply"]["routing"] is None


def test_observation_catalog_does_not_change_solid_operation_candidates(monkeypatch):
    from training import laya_clean_runner as runner
    seen = []
    monkeypatch.setattr(runner, "compile_candidates", lambda facts, seed: seen.append(facts) or [])
    facts = world()
    original = copy.deepcopy(facts)
    assert runner._compile(facts, {"plastic-bar", "petroleum-gas"}, 1) == []
    assert seen[0]["recipes"] == {} and facts == original


LUA_PRELUDE = '''
local recipes={
  ["plastic-bar"]={name="plastic-bar",enabled=true,ingredients={{name="coal",type="item",amount=1},{name="petroleum-gas",type="fluid",amount=20}},
    products={{name="plastic-bar",type="item",amount=2,independent_probability=1,shared_probability={min=0,max=1}}},energy=1,categories={"chemistry"},prototype={},productivity_bonus=0},
  ["iron-plate"]={enabled=true,ingredients={{name="iron-ore",type="item",amount=1}},
    products={{name="iron-plate",type="item",amount=1,independent_probability=1,shared_probability={min=0,max=1}}},energy=3.2,categories={"smelting"},prototype={}},
  ["disabled"]={enabled=false,ingredients={},products={{name="disabled",type="item",amount=1}},energy=1,categories={"crafting"},prototype={}}
}
local chemical={name="chemical-plant",type="assembling-machine",energy=100,status=1,
  position={x=0,y=0},get_recipe=function() return recipes["plastic-bar"] end}
local surface={find_entities_filtered=function(filter)
  if not filter.type then return {chemical} end
  return {}
end}
local stats={get_input_count=function(name) return name=="plastic-bar" and 17 or 0 end,get_output_count=function() return 0 end}
local force={recipes=recipes,technologies={},get_item_production_statistics=function() return stats end}
game={surfaces={nauvis=surface},forces={player=force},tick_paused=true,ticks_to_run=0,tick=0,players={}}
script={active_mods={}}
defines={entity_status={no_input_fluid=1}}
prototypes={entity={
  ["chemical-plant"]={type="assembling-machine",crafting_categories={chemistry=true},get_crafting_speed=function(quality) assert(quality=="normal"); return 1 end,ingredient_count=0},
  ["assembling-machine-1"]={type="assembling-machine",crafting_categories={crafting=true},get_crafting_speed=function() return .5 end,ingredient_count=3},
  ["electric-furnace"]={type="furnace",crafting_categories={smelting=true},get_crafting_speed=function() return 2 end,ingredient_count=0}},
  -- Every queried name in this fixture is an item; fluids never reach the item survey.
  item=setmetatable({},{__index=function() return {} end})}
request={bounds={x_min=0,y_min=0,x_max_exclusive=0,y_max_exclusive=0}}
'''


def test_lua_exporter_runtime_with_typed_fluid_recipe_and_disabled_recipe():
    lua = shutil.which("lua")
    if not lua:
        pytest.skip("Lua runtime unavailable")
    source = (ROOT / "training/laya_saved_world.lua").read_text()
    assertions = '''
assert(facts.recipes["plastic-bar"]==nil)
assert(facts.recipes["iron-plate"].product_amount==1)
assert(facts.recipe_observations["plastic-bar"].ingredients[2].type=="fluid")
assert(facts.recipe_observations["plastic-bar"].machine_speeds["chemical-plant"]==1)
assert(facts.recipe_observations["plastic-bar"].machine_speeds["assembling-machine-1"]==nil)
assert(facts.recipe_observations["disabled"].enabled==false)
assert(facts.production["plastic-bar"]==17)
assert(facts.production["petroleum-gas"]==nil)
'''
    result = subprocess.run([lua, "-"], input=LUA_PRELUDE + source.rsplit("return facts", 1)[0] + assertions,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_schema_accepts_legacy_catalog_and_explicit_observed_enabled_states():
    from training.laya_saved_world import FACTS_VALIDATOR
    facts = {"version": "saved-world-v1", "tick": 0, "surface": "nauvis", "force": "player",
             "mods": {}, "networks": {}, "ports": [], "poles": [], "machines": [], "resources": [],
             "player_stock": {}, "production": {}, "consumption": {}, "researched": {}, "pending_ghosts": 0}
    FACTS_VALIDATOR.validate(facts)  # No new field is required of old evidence.
    for enabled, availability in [(True, "enabled"), (False, "disabled"), (None, "unknown")]:
        facts["recipe_observations"] = {"plastic-bar": plastic(enabled)}
        FACTS_VALIDATOR.validate(facts)
        assert needs(facts)["plastic-bar"]["recipe_availability"] == availability
        assert needs(facts)["plastic-bar"]["recipe_observation_state"] == "observed"
    facts["recipe_observations"] = {}
    FACTS_VALIDATOR.validate(facts)
    assert needs(facts)["plastic-bar"]["recipe_observation_state"] == "unobserved"
    facts["recipe_observations"] = {"plastic-bar": plastic("unknown")}
    assert list(FACTS_VALIDATOR.iter_errors(facts))  # Strings cannot masquerade as enabled truth.
    facts["recipe_observations"] = {"plastic-bar": plastic()}
    del facts["recipe_observations"]["plastic-bar"]["enabled"]
    assert list(FACTS_VALIDATOR.iter_errors(facts))


def test_engine_measurement_uses_exported_plastic_counts_and_keeps_fluid_unknown():
    before = {"tick": 0, "production": {"plastic-bar": 3}, "consumption": {"plastic-bar": 0},
              "pending_ghosts": 0, "machines": []}
    after = {**before, "tick": 600, "production": {"plastic-bar": 43}}
    measured = measured_outcome(before, after)
    rows = needs(world(), measured)
    assert rows["plastic-bar"]["measured_per_s"] == 4
    assert rows["plastic-bar"]["condition"] == "satisfied"
    assert rows["petroleum-gas"]["measured_per_s"] is None


def test_mocked_export_round_trip_through_schema_normalization_and_board():
    from training.laya_saved_world import FACTS_VALIDATOR
    lua = shutil.which("lua")
    if not lua:
        pytest.skip("Lua runtime unavailable")
    source = (ROOT / "training/laya_saved_world.lua").read_text()
    encoder = r'''
local function encode(value)
  if type(value)=="string" then return string.format("%q",value) end
  if type(value)~="table" then return tostring(value) end
  local parts={}
  if #value>0 then
    for _,v in ipairs(value) do table.insert(parts,encode(v)) end
    return "["..table.concat(parts,",").."]"
  end
  for k,v in pairs(value) do table.insert(parts,encode(k)..":"..encode(v)) end
  return "{"..table.concat(parts,",").."}"
end
print(encode(facts))
'''
    result = subprocess.run([lua, "-"], input=LUA_PRELUDE + source.rsplit("return facts", 1)[0] + encoder,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    facts = json.loads(result.stdout)
    # SavedWorldAdapter.query normalizes these four empty Lua tables to arrays.
    for key in ("ports", "poles", "machines", "resources"):
        if facts[key] == {}:
            facts[key] = []
    FACTS_VALIDATOR.validate(facts)
    rows = needs(facts)
    assert rows["plastic-bar"]["condition"] == "input_starved"
    assert rows["plastic-bar"]["capacity_per_s"] == 2
    assert rows["petroleum-gas"]["required_per_s"] == 40
    assert facts["recipes"].keys() == {"iron-plate"}
    iron = compile_status(facts, {"iron-plate": 1})["needs"][0]
    assert iron["recipe_arithmetic_supported"] and iron["condition"] == "unbuilt"
    assert iron["machines_needed"] == {"electric-furnace": 2}


@pytest.mark.parametrize("change", ["probabilistic", "shared", "unknown_probability", "extra", "ranged", "ranged_with_amount", "catalyst"])
def test_unsupported_yield_models_do_not_invent_demand_or_capacity(change):
    facts = world()
    product = facts["recipe_observations"]["plastic-bar"]["products"][0]
    if change == "probabilistic":
        product["independent_probability"] = 0.5
    elif change == "shared":
        product["shared_probability"]["max"] = 0.5
    elif change == "unknown_probability":
        product.pop("shared_probability")
        product.pop("independent_probability")
    elif change == "extra":
        product["extra_count_fraction"] = 0.5
    elif change in {"ranged", "ranged_with_amount"}:
        if change == "ranged":
            product.pop("amount")
        product.update(amount_min=1, amount_max=3)
    else:
        product["ignored_by_productivity"] = 1
    rows = needs(facts)
    assert rows["plastic-bar"]["condition"] == "recipe_unsupported"
    assert rows["plastic-bar"]["capacity_per_s"] is None
    assert rows["plastic-bar"]["capacity_basis"] is None
    assert set(rows) == {"plastic-bar"}
