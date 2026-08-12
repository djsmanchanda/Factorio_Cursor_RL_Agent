# Path: tests/test_training_lab_validation_lua.py
# Purpose: Exercise pure training scenario validation and tick measurement in Lua.

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from training.scenarios.mining_delivery import generate_mining_delivery_scenario

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "factorio_training_lab"

try:
    import lupa
except ImportError:  # pragma: no cover - the static contract tests still run
    lupa = None


def _to_lua(runtime, value):
    if isinstance(value, dict):
        table = runtime.table()
        for key, item in value.items():
            table[key] = _to_lua(runtime, item)
        return table
    if isinstance(value, list):
        table = runtime.table()
        for index, item in enumerate(value, 1):
            table[index] = _to_lua(runtime, item)
        return table
    return value


@pytest.fixture
def lua():
    if lupa is None:
        pytest.skip("pip install lupa for behavioural Lua validation")
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(f'package.path = "{LAB.as_posix()}/?.lua;" .. package.path')
    return runtime


def _scenario() -> dict:
    scenario = generate_mining_delivery_scenario(7)
    return scenario


def test_validation_accepts_the_corrected_generated_contract(lua) -> None:
    module = lua.eval('require("scenario_validation")')[0]

    assert module.validate_scenario(_to_lua(lua, _scenario())) is not None


def test_validation_rejects_half_tile_power_fixture(lua) -> None:
    module = lua.eval('require("scenario_validation")')[0]
    scenario = _scenario()
    scenario["fixtures"][0]["position"] = [0.5, 0.5]

    lua.globals().candidate = _to_lua(lua, scenario)
    ok, message = lua.eval(
        "(function() return pcall(function() return require('scenario_validation').validate_scenario(candidate) end) end)()"
    )
    assert ok is False
    assert "integral centre" in message


def test_validation_rejects_power_source_footprint_outside_the_world(lua) -> None:
    scenario = _scenario()
    scenario["fixtures"][0]["position"] = [-64, 0]
    lua.globals().candidate = _to_lua(lua, scenario)

    ok, message = lua.eval(
        "(function() return pcall(function() return require('scenario_validation').validate_scenario(candidate) end) end)()"
    )
    assert ok is False
    assert "footprint" in message

def test_validation_rejects_real_base_identity(lua) -> None:
    scenario = _scenario()
    scenario["environment"]["surface_name"] = "nauvis"
    scenario["environment"]["force_name"] = "player"
    lua.globals().candidate = _to_lua(lua, scenario)

    ok, message = lua.eval(
        "(function() return pcall(function() return require('scenario_validation').validate_scenario(candidate) end) end)()"
    )
    assert ok is False
    assert "training-only name" in message


def test_validation_rejects_budget_and_allowed_entity_drift(lua) -> None:
    scenario = _scenario()
    scenario["constraints"]["allowed_entities"].remove("splitter")
    lua.globals().candidate = _to_lua(lua, scenario)

    ok, message = lua.eval(
        "(function() return pcall(function() return require('scenario_validation').validate_scenario(candidate) end) end)()"
    )
    assert ok is False
    assert "differs from budget" in message


def test_tick_sampler_requires_consecutive_target_windows(lua) -> None:
    lua.execute("storage = {training_lab={version='1.1.0', report_sequence=0, episodes={}, pending_force_merges={}}}")
    module = lua.eval('require("episode_measurement")')[0]
    state = _to_lua(lua, {
        "last_sample_tick": 0, "started_tick": 0, "sample_ticks": 0,
        "sample_items": 0, "delivered_items": 0, "rate_per_tick": 0,
        "sustained_ticks": 0, "status": "ready", "failure_kind": "none",
        "failure_reason": "", "scenario": {
            "objective": {"target_rate_per_tick": 2 / 60, "sustain_ticks": 120},
            "constraints": {"max_episode_ticks": 600},
        },
    })

    module.advance_sample(state, 2, 60)
    assert state.sustained_ticks == 60
    module.advance_sample(state, 0, 120)
    assert state.sustained_ticks == 0
    module.advance_sample(state, 3, 180)
    module.advance_sample(state, 3, 240)
    module.advance_sample(state, 3, 300)
    module.advance_sample(state, 3, 360)
    module.advance_sample(state, 3, 420)
    module.advance_sample(state, 3, 480)
    assert state.status == "completed"


def test_tick_sampler_records_timeout_in_ticks(lua) -> None:
    lua.execute("storage = {training_lab={version='1.1.0', report_sequence=0, episodes={}, pending_force_merges={}}}")
    module = lua.eval('require("episode_measurement")')[0]
    state = _to_lua(lua, {
        "last_sample_tick": 540, "started_tick": 0, "sample_ticks": 0,
        "sample_items": 0, "delivered_items": 0, "rate_per_tick": 0,
        "sustained_ticks": 0, "status": "running", "failure_kind": "none",
        "failure_reason": "", "scenario": {
            "objective": {"target_rate_per_tick": 2 / 60, "sustain_ticks": 120},
            "constraints": {"max_episode_ticks": 600},
        },
    })

    module.advance_sample(state, 0, 600)
    assert state.status == "timed_out"
    assert state.failure_kind == "timeout"


def test_audit_accumulates_productive_drill_capacity_without_an_extra_scan(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, episodes={}, pending_force_merges={}}}
        defines = {entity_status={working=1, full_output=2, no_power=3}}
        prototypes = {entity={
          ['electric-mining-drill']={tile_width=3, tile_height=3, collision_box={left_top={x=-1.5,y=-1.5},right_bottom={x=1.5,y=1.5}}},
          ['medium-electric-pole']={tile_width=2, tile_height=2, collision_box={left_top={x=-1,y=-1},right_bottom={x=1,y=1}}},
          ['transport-belt']={tile_width=1, tile_height=1, collision_box={left_top={x=-0.4,y=-0.4},right_bottom={x=0.4,y=0.4}}}
        }}
        training_force = {name='training-mining-delivery-00000001'}
        drill_a = {type='mining-drill', name='electric-mining-drill', unit_number=1, status=1, position={x=2.5,y=2.5}, force=training_force}
        drill_b = {type='mining-drill', name='electric-mining-drill', unit_number=2, status=2, position={x=6.5,y=2.5}, force=training_force}
        drill_c = {type='mining-drill', name='electric-mining-drill', unit_number=3, status=3, position={x=10.5,y=2.5}, force=training_force}
        pole = {type='electric-pole', name='medium-electric-pole', unit_number=4, position={x=4,y=7}, force=training_force}
        belt = {type='transport-belt', name='transport-belt', unit_number=5, position={x=6.5,y=7.5}, force=training_force}
        audit_surface = {scan_count=0, find_entities=function()
          audit_surface.scan_count = audit_surface.scan_count + 1
          return {drill_a, drill_b, drill_c, pole, belt}
        end}
        audit_episode = {fixtures={}, scenario={constraints={
          allowed_entities={'electric-mining-drill','medium-electric-pole','transport-belt'},
          allowed_build_area={x_min=0,x_max_exclusive=16,y_min=0,y_max_exclusive=16}
        }, construction_budget={['electric-mining-drill']=3,['medium-electric-pole']=1,['transport-belt']=1}}}
        measurement = require('episode_measurement')
        _, _, _, _, first_metrics = measurement.audit_entities(audit_surface, training_force, audit_episode, 60)
        drill_a.status=2; drill_b.status=1; drill_c.status=1
        _, _, _, _, second_metrics = measurement.audit_entities(audit_surface, training_force, audit_episode, 60)
    """)

    first = lua.globals().first_metrics
    assert first["electric_pole_count"] == 1
    assert first["occupied_footprint_tiles"] == 32
    assert first["placed_mining_drills"] == 3
    assert first["productive_mining_drills"] == 1
    assert first["productive_mining_drill_ratio"] == pytest.approx(1 / 3)
    assert first["mining_drill_capacity_ticks"] == 180
    assert first["mining_drill_working_ticks"] == 60
    assert first["mining_drill_blocked_ticks"] == 60
    assert first["mining_drill_idle_ticks"] == 60

    second = lua.globals().second_metrics
    assert second["productive_mining_drills"] == 3
    assert second["productive_mining_drill_ratio"] == 1
    assert second["mining_drill_capacity_ticks"] == 360
    assert second["mining_drill_working_ticks"] == 180
    assert second["mining_drill_blocked_ticks"] == 120
    assert second["mining_drill_idle_ticks"] == 60
    assert lua.globals().audit_surface["scan_count"] == 2


def test_audit_treats_unknown_drill_status_as_idle(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, episodes={}, pending_force_merges={}}}
        defines = {entity_status={}}
        prototypes = {entity={['electric-mining-drill']={tile_width=3, tile_height=3, collision_box={left_top={x=-1.5,y=-1.5},right_bottom={x=1.5,y=1.5}}}}}
        training_force = {name='training-mining-delivery-00000001'}
        unknown_drill = {type='mining-drill', name='electric-mining-drill', unit_number=1, status=99, position={x=2.5,y=2.5}, force=training_force}
        audit_surface = {find_entities=function() return {unknown_drill} end}
        audit_episode = {fixtures={}, scenario={constraints={allowed_entities={'electric-mining-drill'},allowed_build_area={x_min=0,x_max_exclusive=8,y_min=0,y_max_exclusive=8}},construction_budget={['electric-mining-drill']=1}}}
        _, _, _, _, unknown_metrics = require('episode_measurement').audit_entities(audit_surface, training_force, audit_episode, 60)
    """)

    metrics = lua.globals().unknown_metrics
    assert metrics["productive_mining_drills"] == 0
    assert metrics["mining_drill_idle_ticks"] == 60


def test_audit_consumers_avoid_a_second_whole_force_scan(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, episodes={}, pending_force_merges={}}}
        defines = {entity_status={working=1}}
        prototypes = {entity={['electric-mining-drill']={collision_box={left_top={x=-1.5,y=-1.5},right_bottom={x=1.5,y=1.5}}}}}
        training_force = {name='training-mining-delivery-00000001'}
        source = {valid=true, type='electric-energy-interface', name='electric-energy-interface', unit_number=10, electric_network_id=7, position={x=0,y=0}, force=training_force, surface={name='training/mining-delivery-00000001'}}
        drill = {valid=true, type='mining-drill', name='electric-mining-drill', unit_number=11, status=1, electric_network_id=7, position={x=3.5,y=3.5}, force=training_force}
        power_surface = {
          full_force_scans=0,
          find_entities=function() return {source, drill} end,
          find_entities_filtered=function(filter)
            if filter.name then return {source} end
            power_surface.full_force_scans = power_surface.full_force_scans + 1
            return {drill}
          end
        }
        power_episode = {
          fixtures={source={kind='power_source', name='electric-energy-interface', unit_number=10, position={x=0,y=0}}},
          scenario={constraints={allowed_entities={'electric-mining-drill'},allowed_build_area={x_min=0,x_max_exclusive=8,y_min=0,y_max_exclusive=8}},construction_budget={['electric-mining-drill']=1}}
        }
        measurement = require('episode_measurement')
        _, _, _, _, _, audited_consumers = measurement.audit_entities(power_surface, training_force, power_episode, 60)
        power_ok, power_reason = measurement.power_state(power_surface, training_force, power_episode, audited_consumers)
    """)

    assert lua.globals().power_ok is True
    assert lua.globals().power_reason == ""
    assert lua.globals().power_surface["full_force_scans"] == 0


def test_fixture_check_uses_surface_lookup_even_when_global_lookup_is_empty(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, episodes={}, pending_force_merges={}}}
        game = {get_entity_by_unit_number=function() return nil end}
        training_force = {name='training-mining-delivery-00000001'}
        fixture_entity = {
          valid=true, name='infinity-chest', unit_number=7,
          position={x=8.5, y=-8.5},
          surface={name='training/mining-delivery-00000001'}, force=training_force
        }
        fixture_surface = {find_entities_filtered=function(_) return {fixture_entity} end}
        fixture_episode = {
          surface_name='training/mining-delivery-00000001',
          fixtures={sink={name='infinity-chest', unit_number=7, position={x=8.5, y=-8.5}}}
        }
    """)
    module = lua.eval('require("episode_measurement")')[0]

    assert module.check_fixtures(
        lua.globals().fixture_surface, lua.globals().training_force, lua.globals().fixture_episode,
    ) is True

def test_chunked_plan_execution_module_loads(lua) -> None:
    module = lua.eval('require("plan_execution")')[0]

    assert module.MAX_CHUNK_BYTES == 1800

def test_plan_execution_rejects_cumulative_budget_before_placement(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, episodes={}, pending_force_merges={}, uploads={}}}
        game = {surfaces={}, forces={}}
        prototypes = {entity={['transport-belt']={collision_box={left_top={x=-0.4,y=-0.4},right_bottom={x=0.4,y=0.4}}}}}
        existing = {type='transport-belt', name='transport-belt', unit_number=11, position={x=2,y=2}}
        surface = {find_entities_filtered=function(filter)
          if filter.force then return {existing} end
          return {}
        end}
        force = {name='training-mining-delivery-00000001'}
        episode = {
          fixtures={},
          scenario={constraints={allowed_entities={'transport-belt'},allowed_build_area={x_min=0,x_max_exclusive=10,y_min=0,y_max_exclusive=10}},construction_budget={['transport-belt']=1}}
        }
        plan = {phases={{name='placement',actions={{action_type='place_entity',entity='transport-belt',position={x=4,y=4}}}}}}
    """)
    module = lua.eval('require("plan_execution")')[0]

    ok, message = lua.eval("(function() return pcall(function() return require('plan_execution').plan_actions(plan, episode, surface, force) end) end)()")

    assert ok is False
    assert "construction budget" in message


def test_training_footprints_cannot_cross_the_build_boundary(lua) -> None:
    lua.execute("""
        prototypes = {entity={
          ['electric-mining-drill']={collision_box={left_top={x=-1.5,y=-1.5},right_bottom={x=1.5,y=1.5}}},
          ['splitter']={collision_box={left_top={x=-0.9,y=-0.9},right_bottom={x=0.9,y=0.9}}}
        }}
        bounds = {x_min=0,x_max_exclusive=10,y_min=0,y_max_exclusive=10}
        drill_inside = {x=1.5,y=1.5}
        drill_outside = {x=1.0,y=1.5}
        splitter_inside = {x=9.0,y=5.0}
        splitter_outside = {x=9.5,y=5.0}
    """)
    geometry = lua.eval('require("training_geometry")')[0]

    assert geometry.fits(lua.globals().bounds, "electric-mining-drill", lua.globals().drill_inside) is True
    assert geometry.fits(lua.globals().bounds, "electric-mining-drill", lua.globals().drill_outside) is False
    assert geometry.fits(lua.globals().bounds, "splitter", lua.globals().splitter_inside) is True
    assert geometry.fits(lua.globals().bounds, "splitter", lua.globals().splitter_outside) is False

def test_primary_research_completes_finite_technologies_only(lua) -> None:
    lua.execute("""
        research_force = {
          research_queue={'stale'},
          technologies={
            finite={enabled=true, researched=false, level=1, prototype={max_level=1}},
            final_level={enabled=true, researched=false, level=3, prototype={max_level=3}},
            finished={enabled=true, researched=true, level=3, prototype={max_level=3}},
            infinite={enabled=true, researched=false, level=4, prototype={max_level=4294967295}},
            disabled={enabled=false, researched=false, level=1, prototype={max_level=2}}
          },
          reset_technology_effects=function() research_force.effects_reset=true end
        }
    """)
    world = lua.eval('require("episode_world")')[0]

    assert world.complete_primary_research(lua.globals().research_force) == 2
    assert lua.globals().research_force.technologies.finite.researched is True
    assert lua.globals().research_force.technologies.finite.level == 1
    assert lua.globals().research_force.technologies.final_level.researched is True
    assert lua.globals().research_force.technologies.final_level.level == 3
    assert lua.globals().research_force.technologies.finished.researched is True
    assert lua.globals().research_force.technologies.infinite.researched is False
    assert lua.globals().research_force.technologies.infinite.level == 4
    assert lua.globals().research_force.technologies.disabled.researched is False
    assert lua.globals().research_force.effects_reset is True

def test_orphan_training_surfaces_recycle_after_a_grace_window(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, episodes={active={status='running', started_tick=0, last_sample_tick=0, surface_name='training/mining-delivery-00000015'}}, pending_force_merges={}, orphan_surfaces={}}}
        deleted, merged = {}, {}
        log = function(message) last_log = message end
        orphan = {name='training/mining-delivery-0000005c', valid=true}
        owned = {name='training/mining-delivery-00000015', valid=true}
        game = {
          surfaces={orphan=orphan, owned=owned},
          forces={neutral={name='neutral'}, ['training-mining-delivery-0000005c']={name='training-mining-delivery-0000005c', valid=true}},
          connected_players={},
          delete_surface=function(surface)
            deleted[#deleted + 1] = surface.name
            surface.valid = false
            for key, value in pairs(game.surfaces) do if value == surface then game.surfaces[key] = nil end end
            return nil
          end,
          merge_forces=function(force, neutral) merged[#merged + 1] = force.name end
        }
    """)
    world = lua.eval('require("episode_world")')[0]

    world.cleanup_orphan_surfaces(0)
    assert list(lua.globals().deleted.values()) == []

    world.cleanup_orphan_surfaces(3600)
    assert list(lua.globals().deleted.values()) == ["training/mining-delivery-0000005c"]
    assert list(lua.globals().merged.values()) == ["training-mining-delivery-0000005c"]


def test_terminal_episode_records_do_not_protect_stale_surfaces(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, episodes={
          done={status='timed_out', surface_name='training/mining-delivery-00000016'},
          active={status='running', started_tick=0, last_sample_tick=0, surface_name='training/mining-delivery-00000017'}
        }, pending_force_merges={}, orphan_surfaces={}}}
        deleted, merged = {}, {}
        log = function(_) end
        done_surface = {name='training/mining-delivery-00000016', valid=true}
        active_surface = {name='training/mining-delivery-00000017', valid=true}
        game = {
          surfaces={done=done_surface, active=active_surface},
          forces={neutral={name='neutral'}, ['training-mining-delivery-00000016']={name='training-mining-delivery-00000016', valid=true}},
          connected_players={},
          delete_surface=function(surface)
            deleted[#deleted + 1] = surface.name
            surface.valid = false
            for key, value in pairs(game.surfaces) do if value == surface then game.surfaces[key] = nil end end
            return nil
          end,
          merge_forces=function(force, _) merged[#merged + 1] = force.name end
        }
    """)
    world = lua.eval('require("episode_world")')[0]

    world.cleanup_orphan_surfaces(0)
    world.cleanup_orphan_surfaces(3600)

    assert list(lua.globals().deleted.values()) == ["training/mining-delivery-00000016"]
    assert list(lua.globals().merged.values()) == ["training-mining-delivery-00000016"]

def test_stale_live_episode_records_do_not_protect_surfaces(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, episodes={
          stale={status='running', started_tick=0, last_sample_tick=0, surface_name='training/mining-delivery-00000018'},
          fresh={status='running', started_tick=3500, last_sample_tick=3590, surface_name='training/mining-delivery-00000019'}
        }, pending_force_merges={}, orphan_surfaces={}}}
        deleted, merged = {}, {}
        log = function(_) end
        stale_surface = {name='training/mining-delivery-00000018', valid=true}
        fresh_surface = {name='training/mining-delivery-00000019', valid=true}
        game = {
          surfaces={stale=stale_surface, fresh=fresh_surface},
          forces={neutral={name='neutral'}, ['training-mining-delivery-00000018']={name='training-mining-delivery-00000018', valid=true}},
          connected_players={},
          delete_surface=function(surface)
            deleted[#deleted + 1] = surface.name
            surface.valid = false
            for key, value in pairs(game.surfaces) do if value == surface then game.surfaces[key] = nil end end
            return nil
          end,
          merge_forces=function(force, _) merged[#merged + 1] = force.name end
        }
    """)
    world = lua.eval('require("episode_world")')[0]

    world.cleanup_orphan_surfaces(0)
    world.cleanup_orphan_surfaces(3601, True)

    assert list(lua.globals().deleted.values()) == ["training/mining-delivery-00000018"]
    assert list(lua.globals().merged.values()) == ["training-mining-delivery-00000018"]

def test_orphan_training_forces_without_surfaces_are_merged(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, episodes={},
          pending_force_merges={}, orphan_surfaces={}}}
        merged = {}
        game = {
          surfaces={},
          forces={neutral={name='neutral', valid=true},
            ['training-mining-delivery-dead']={name='training-mining-delivery-dead', valid=true}},
          connected_players={},
          merge_forces=function(force, _) merged[#merged + 1] = force.name end
        }
    """)
    world = lua.eval('require("episode_world")')[0]

    result = world.cleanup_orphan_surfaces(0)

    assert list(lua.globals().merged.values()) == ["training-mining-delivery-dead"]
    assert list(result.recycled_forces.values()) == ["training-mining-delivery-dead"]


def test_provision_reclaims_expired_surface_ownership(lua) -> None:
    scenario = _scenario()
    surface_name = scenario["environment"]["surface_name"]
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, episodes={
          stale={episode_id='old', status='running', started_tick=0, last_sample_tick=0,
            surface_name='training/mining-delivery-00000007', force_name='training-mining-delivery-00000007'}
        }, pending_force_merges={}, orphan_surfaces={}, uploads={}}}
        deleted = {}
        stale_surface = {name='training/mining-delivery-00000007', valid=true}
        game = {
          tick=7200, connected_players={}, surfaces={["training/mining-delivery-00000007"] = stale_surface},
          forces={neutral={name='neutral'}}
        }
        game.delete_surface = function(surface)
          deleted[#deleted + 1] = surface.name
          surface.valid = false
          game.surfaces[surface.name] = nil
          return nil
        end
    """)
    scenario["environment"]["surface_name"] = "training/mining-delivery-00000007"
    scenario["environment"]["force_name"] = "training-mining-delivery-00000007"
    lua.globals().candidate = _to_lua(lua, scenario)
    world = lua.eval('require("episode_world")')[0]

    world.ensure_names_available(lua.globals().candidate)

    assert list(lua.globals().deleted.values()) == ["training/mining-delivery-00000007"]

def test_visible_training_floor_covers_the_complete_environment(lua) -> None:
    lua.execute("""
        painted = {}
        floor_surface = {set_tiles=function(tiles)
          for _, tile in pairs(tiles) do table.insert(painted, tile) end
        end}
        floor_bounds = {x_min=-2,y_min=-1,x_max_exclusive=2,y_max_exclusive=2}
    """)
    world = lua.eval('require("episode_world")')[0]

    world.fill_visible_floor(lua.globals().floor_surface, lua.globals().floor_bounds)

    painted = list(lua.globals().painted.values())

    assert len(painted) == 12
    assert {tile["name"] for tile in painted} == {"lab-dark-1", "lab-dark-2"}
    assert sum(tile["name"] == "lab-dark-1" for tile in painted) == 6
    assert sum(tile["name"] == "lab-dark-2" for tile in painted) == 6


def test_view_command_switches_to_spectator_before_teleport(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, uploads={}, pending_force_merges={}, episodes={
          active={owner='factorio_training_lab', surface_name='training/mining-delivery-00000001', scenario={environment={bounds={x_min=-64,y_min=-64,x_max_exclusive=64,y_max_exclusive=64}}}
        }}}}
        defines = {controllers={spectator=7}}
        player = {
          force={chart=function(surface, area) player.charted_surface=surface; player.charted_area=area end},
          set_controller=function(controller) player.controller=controller end,
          teleport=function(position, surface) player.teleported_position=position; player.teleported_surface=surface; return true end,
          print=function(message) player.message=message end
        }
        game = {
          get_player=function(_) return player end,
          surfaces={['training/mining-delivery-00000001']={name='training/mining-delivery-00000001'}}
        }
        view_command = {player_index=1, parameter='training/mining-delivery-00000001'}
    """)
    world = lua.eval('require("episode_world")')[0]

    world.view_episode(lua.globals().view_command)

    assert lua.globals().player.controller["type"] == 7
    assert lua.globals().player.teleported_surface["name"] == "training/mining-delivery-00000001"
    assert lua.globals().player.teleported_position["x"] == -62
    assert "spectator" in lua.globals().player.message

def test_joined_observer_charts_every_active_training_episode(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, uploads={}, pending_force_merges={}, episodes={
          active={owner='factorio_training_lab', surface_name='training/mining-delivery-00000001', scenario={environment={bounds={x_min=-64,y_min=-64,x_max_exclusive=64,y_max_exclusive=64}}}
        }}}}
        observer = {
          force={chart=function(surface, area) observer.charted_surface=surface; observer.charted_area=area end}
        }
        game = {
          surfaces={['training/mining-delivery-00000001']={name='training/mining-delivery-00000001'}}
        }
    """)
    world = lua.eval('require("episode_world")')[0]

    world.reveal_active_episodes(lua.globals().observer)

    assert lua.globals().observer.charted_surface["name"] == "training/mining-delivery-00000001"
    assert lua.globals().observer.charted_area[1][1] == -64
    assert lua.globals().observer.charted_area[2][2] == 64


def test_training_focus_moves_only_the_configured_connected_observer(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.1.0', report_sequence=0, uploads={}, pending_force_merges={}, episodes={
          active={owner='factorio_training_lab', surface_name='training/mining-delivery-00000001', scenario={environment={bounds={x_min=-64,y_min=-64,x_max_exclusive=64,y_max_exclusive=64}}}
        }}}}
        defines = {controllers={spectator=7}}
        observer = {
          name='main', connected=true,
          force={chart=function(surface, area) observer.charted_surface=surface end},
          set_controller=function(controller) observer.controller=controller end,
          teleport=function(position, surface) observer.teleported_surface=surface; return true end,
        }
        game = {
          get_player=function(name) if name == 'main' then return observer end end,
          surfaces={['training/mining-delivery-00000001']={name='training/mining-delivery-00000001'}}
        }
        focus_payload = {
          request_id='request-1', episode_id='active', observer_name='main',
          confirmation_token='FOCUS_TRAINING_OBSERVER'
        }
    """)
    world = lua.eval('require("episode_world")')[0]

    result = world.focus_observer(lua.globals().focus_payload)

    assert result["ok"] is True
    assert result["surface"] == "training/mining-delivery-00000001"
    assert lua.globals().observer.controller["type"] == 7
    assert lua.globals().observer.teleported_surface["name"] == "training/mining-delivery-00000001"


def test_tick_sampler_smooths_low_rate_inserter_batches(lua) -> None:
    lua.execute("storage = {training_lab={version='1.1.0', report_sequence=0, episodes={}, pending_force_merges={}}}")
    module = lua.eval('require("episode_measurement")')[0]
    state = _to_lua(lua, {
        "last_sample_tick": 0, "started_tick": 0, "sample_ticks": 0,
        "sample_items": 0, "delivered_items": 0, "rate_per_tick": 0,
        "sustained_ticks": 0, "status": "ready", "failure_kind": "none",
        "failure_reason": "", "scenario": {
            "objective": {"target_rate_per_tick": 1 / 120, "sustain_ticks": 120},
            "constraints": {"max_episode_ticks": 600},
        },
    })

    module.advance_sample(state, 1, 60)
    module.advance_sample(state, 0, 120)
    module.advance_sample(state, 1, 180)

    assert state.rate_per_tick >= 1 / 120
    assert state.sustained_ticks == 180
    assert state.status == "completed"