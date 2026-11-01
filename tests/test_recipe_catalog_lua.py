# Path: tests/test_recipe_catalog_lua.py
# Purpose: Guard the Factorio 2.1 recipe-category compatibility fix.

from pathlib import Path
import json

import pytest
from jsonschema import Draft7Validator
from tests.test_runtime_catalog_lua import lua


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "factorio_mod" / "recipe_catalog.lua"


def test_recipe_catalog_uses_factorio_21_plural_categories() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "local function primary_recipe_category(recipe)" in source
    assert "local categories = recipe.categories" in source
    assert "categories = recipe.prototype.categories" in source
    assert "local category, categories = primary_recipe_category(recipe)" in source
    assert "recipe.category" not in source


@pytest.mark.parametrize("categories", [None, [], [1], {"crafting": True}, ["crafting", "crafting"]])
def test_unknown_category_is_marked_unsupported_without_inventing_crafting(lua, categories):
    from tests.test_runtime_catalog_lua import _python_to_lua
    lua.execute('''
      package.preload["sandbox_shared"] = function() return {} end
      game.forces.player.recipes = {
        gear = {enabled=true, energy=0.5,
                ingredients={{name="iron-plate", type="item", amount=2}},
                products={{name="gear", type="item", amount=1}}}
      }
      prototypes.entity = {}; prototypes.asteroid_chunk = {}
      prototypes.item = {gear = {stack_size = 100}}
    ''')
    if categories is not None:
        lua.globals().game.forces.player.recipes.gear.categories = _python_to_lua(lua, categories)
    lua.execute(f'dofile("{SOURCE.as_posix()}")')
    lua.globals().commands.registered["export_recipe_catalog"](lua.table(parameter="player"))
    payload = json.loads(lua.globals().written[1]["json"])
    Draft7Validator(json.loads((ROOT / "schemas/recipe_catalog.schema.json").read_text())).validate(payload)
    recipe = payload["recipes"][0]
    assert recipe["supported"] is False and recipe["category"] == "unavailable"
    assert recipe["categories"] == []
    assert "recipe category unavailable or malformed" in recipe["unsupported_reason"]


def test_recipe_export_records_loaded_mods_and_force(lua):
    lua.execute('''
      package.preload["sandbox_shared"] = function() return {} end
      game.forces.player.recipes = {
        gear = {enabled=true, categories={"smelting", "crafting"}, energy=0.5,
                ingredients={{name="iron-plate", type="item", amount=2}},
                products={{name="gear", type="item", amount=1}}}
      }
      prototypes.entity = {}
      prototypes.asteroid_chunk = {}
      prototypes.item = {gear = {stack_size = 100}}
    ''')
    lua.execute(f'dofile("{SOURCE.as_posix()}")')
    lua.globals().commands.registered["export_recipe_catalog"](lua.table(parameter="player"))
    payload = json.loads(lua.globals().written[1]["json"])
    schema = json.loads((ROOT / "schemas/recipe_catalog.schema.json").read_text())
    Draft7Validator(schema).validate(payload)
    assert payload["recipes"][0]["categories"] == ["crafting", "smelting"]
    assert payload["recipes"][0]["category"] == "crafting"
    assert payload["version"] == "1.3.0"
    assert payload["force"] == "player" and payload["tick"] == 4242
    assert payload["active_mods"] == {"base": "2.1.24", "space-age": "2.1.24", "factorio_cursor_rl_agent": "0.2.0"}
    assert payload["active_mods_count"] == 3
    assert lua.globals().written[1]["path"].endswith("recipe_catalog_4242.json")


def test_json_request_is_correlated_and_preserves_legacy_force_behavior(lua):
    lua.execute('''
      package.preload["sandbox_shared"] = function() return {} end
      game.forces.player.recipes = {
        gear = {enabled=true, categories={"crafting"}, energy=0.5,
                ingredients={{name="iron-plate", type="item", amount=2}},
                products={{name="gear", type="item", amount=1}}}
      }
      prototypes.entity = {}; prototypes.asteroid_chunk = {}
      prototypes.item = {gear = {stack_size = 100}}
    ''')
    lua.execute(f'dofile("{SOURCE.as_posix()}")')
    lua.globals().commands.registered["export_recipe_catalog"](
        lua.table(parameter=json.dumps({"force": "player", "request_id": "req-42"}))
    )
    entry = lua.globals().written[1]
    payload = json.loads(entry["json"])
    Draft7Validator(json.loads((ROOT / "schemas/recipe_catalog.schema.json").read_text())).validate(payload)
    assert payload["request_id"] == "req-42" and payload["force"] == "player"
    assert entry["path"].endswith("recipe_catalog_req-42.json")


@pytest.mark.parametrize("payload", [
    {"request_id": "req"}, {"force": "player"}, {"force": "player", "request_id": "../escape"},
    {"force": "player", "request_id": "req", "extra": True},
])
def test_json_request_rejects_missing_or_unsafe_fields_before_export(lua, payload):
    lua.execute('package.preload["sandbox_shared"] = function() return {} end')
    lua.execute(f'dofile("{SOURCE.as_posix()}")')
    with pytest.raises(Exception):
        lua.globals().commands.registered["export_recipe_catalog"](lua.table(parameter=json.dumps(payload)))
    assert len(lua.globals().written) == 0


@pytest.mark.parametrize("invalid", [{}, {"base": ""}, {"": "2.1"}])
def test_recipe_export_rejects_missing_or_malformed_mods(lua, invalid):
    from tests.test_runtime_catalog_lua import _python_to_lua
    lua.globals().script.active_mods = _python_to_lua(lua, invalid)
    lua.execute('package.preload["sandbox_shared"] = function() return {} end')
    lua.execute(f'dofile("{SOURCE.as_posix()}")')
    with pytest.raises(Exception, match="active mod list"):
        lua.globals().commands.registered["export_recipe_catalog"](lua.table(parameter="player"))
    assert len(lua.globals().written) == 0
