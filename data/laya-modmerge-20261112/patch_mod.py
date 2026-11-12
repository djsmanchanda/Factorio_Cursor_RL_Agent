#!/usr/bin/env python3
# Path: data/laya-modmerge-20261112/patch_mod.py | Purpose: Apply the additive correlated-layout-receipt protocol and Track B observer modules to a copy of Track A's mod pack.
"""Patch A's pack copy without changing its executor semantics.

1. layout_executor.lua: add a request-id-named report writer and a correlated
   receipt journal for envelopes that carry request_id/request_digest. Legacy
   uncorrelated calls keep A's tick-named report and produce no journal entry.
2. control.lua: require B's seven observer modules and register their event
   hooks; A's own handlers stay in place.
"""
from __future__ import annotations

import sys
from pathlib import Path

HELPER = '''local function write_layout_report(report, request_id)
  -- Correlated calls name the report after the request id; legacy calls keep
  -- the tick-named report Track A reads.
  local json = helpers.table_to_json(report)
  if report.placement_failures and #report.placement_failures == 0 then
    json = json:gsub('"placement_failures":{}', '"placement_failures":[]')
  end
  local suffix = request_id or tostring(game.tick)
  helpers.write_file("factorio_mod/layout_reports/layout_" .. suffix .. ".json", json, false)
end

'''

CORRELATED = '''  -- Correlated submissions (Track B's runner) carry a request id and digest;
  -- streamed submissions also carry a stream id and sequence. A duplicate may
  -- replay the receipt but never the world mutation; a missing receipt is not
  -- permission to retry. Legacy uncorrelated calls skip this block and behave
  -- exactly as before.
  local request_id, digest = payload.request_id, payload.request_digest
  local stream_id, sequence = payload.stream_id, payload.sequence
  local streamed = stream_id ~= nil or sequence ~= nil
  local correlated = request_id ~= nil or digest ~= nil or streamed
  local entry = nil
  if payload.optimizer_ownership ~= nil or payload.optimizer_tag_protocol ~= nil then
    game.print("BuildPlan error: shared optimizer tags are not supported by this executor")
    return
  end
  if correlated then
    local valid = type(request_id) == "string" and #request_id == 32 and request_id:match("^[0-9a-f]+$")
      and type(digest) == "string" and #digest == 64 and digest:match("^[0-9a-f]+$")
      and type(command.parameter) == "string" and #command.parameter <= 262144
    for key in pairs(payload) do
      if key ~= "request_id" and key ~= "request_digest" and key ~= "authorization" and key ~= "build_plan"
        and key ~= "stream_id" and key ~= "sequence" then
        valid = false
      end
    end
    if streamed then
      valid = valid and type(stream_id) == "string" and #stream_id == 16 and stream_id:match("^[0-9a-f]+$")
        and type(sequence) == "number" and sequence >= 1 and sequence <= 4294967295 and sequence % 1 == 0
      if valid then valid = request_id == stream_id .. string.format("%016x", sequence) end
    end
    if not valid then game.print("BuildPlan error: invalid correlated request"); return end
    if streamed then
      -- One stream retains only its latest request. Older sequence numbers are
      -- rejected, so bounded retention cannot turn an old retry into a new build.
      if storage.layout_submission_streams == nil then
        storage.layout_submission_streams = {count=0, entries={}}
      end
      local state = storage.layout_submission_streams
      if type(state) ~= "table" or type(state.entries) ~= "table" or type(state.count) ~= "number"
        or state.count < 0 or state.count % 1 ~= 0 then
        game.print("BuildPlan error: invalid stream journal"); return
      end
      local legacy = storage.layout_submission_receipts
      if legacy ~= nil and (type(legacy) ~= "table" or type(legacy.entries) ~= "table") then
        game.print("BuildPlan error: invalid receipt journal"); return
      end
      if legacy and legacy.entries[request_id] ~= nil then
        game.print("BuildPlan error: request id belongs to legacy journal"); return
      end
      entry = state.entries[stream_id]
      if entry == nil then
        if sequence ~= 1 then game.print("BuildPlan error: stream must begin at sequence 1"); return end
        if state.count >= 128 then game.print("BuildPlan error: stream journal capacity exceeded"); return end
        entry = {sequence=0, status="completed"}
        state.entries[stream_id] = entry
        state.count = state.count + 1
      elseif type(entry) ~= "table" or type(entry.sequence) ~= "number" or entry.sequence < 1
        or entry.sequence % 1 ~= 0 then
        game.print("BuildPlan error: invalid stream entry"); return
      end
      if sequence == entry.sequence then
        if entry.payload ~= command.parameter then
          game.print("BuildPlan error: stream request conflicts with stored payload"); return
        end
        if entry.status ~= "completed" or type(entry.report) ~= "table" then
          game.print("BuildPlan error: prior submission outcome unknown"); return
        end
        write_layout_report(entry.report, request_id)
        return
      end
      if sequence ~= entry.sequence + 1 or entry.status ~= "completed" then
        game.print("BuildPlan error: stream sequence is stale, skipped, or pending"); return
      end
      entry.sequence = sequence
      entry.payload = command.parameter
      entry.status = "pending"
      entry.report = nil
    else
      if storage.layout_submission_receipts == nil then
        storage.layout_submission_receipts = {count=0, entries={}}
      end
      local state = storage.layout_submission_receipts
      if type(state) ~= "table" or type(state.entries) ~= "table" or type(state.count) ~= "number"
        or state.count < 0 or state.count % 1 ~= 0 then
        game.print("BuildPlan error: invalid receipt journal"); return
      end
      local streams = storage.layout_submission_streams
      if streams ~= nil and (type(streams) ~= "table" or type(streams.entries) ~= "table") then
        game.print("BuildPlan error: invalid stream journal"); return
      end
      if streams and streams.entries[string.sub(request_id, 1, 16)] ~= nil then
        game.print("BuildPlan error: request id belongs to a stream"); return
      end
      entry = state.entries[request_id]
      if entry ~= nil then
        if type(entry) ~= "table" or entry.payload ~= command.parameter then
          game.print("BuildPlan error: request id conflicts with stored payload"); return
        end
        if entry.status ~= "completed" or type(entry.report) ~= "table" then
          game.print("BuildPlan error: prior submission outcome unknown"); return
        end
        write_layout_report(entry.report, request_id)
        return
      end
      if state.count >= 128 then game.print("BuildPlan error: receipt journal capacity exceeded"); return end
      entry = {payload=command.parameter, status="pending"}
      state.entries[request_id] = entry
      state.count = state.count + 1
    end
  end

'''

REPORT_ANCHOR = '''  local json = helpers.table_to_json(report)
  if run_ok and #result.placement_failures == 0 then
    json = json:gsub('"placement_failures":{}', '"placement_failures":[]')
  end
  helpers.write_file("factorio_mod/layout_reports/layout_" .. game.tick .. ".json", json, false)
end)
'''

REPORT_NEW = '''  if correlated then
    report.request_id = request_id
    report.request_digest = digest
    report.receipt_version = streamed and "layout-submission-receipt-v2" or "layout-submission-receipt-v1"
    if streamed then report.stream_id, report.sequence = stream_id, sequence end
    entry.report = report
    entry.status = "completed"
  end
  -- Always keep Track A's tick-named report; correlated calls also write the
  -- request-id-named report Track B's runner polls for.
  write_layout_report(report, nil)
  if correlated then write_layout_report(report, request_id) end
end)
'''

CONTROL_ANCHOR = '''local shared = require("sandbox_shared")
local trash_requesters = require("trash_requesters")
'''

CONTROL_NEW = '''local shared = require("sandbox_shared")
local trash_requesters = require("trash_requesters")
local research_lab_ownership = require("research_lab_ownership")
local optimizer_build_evidence = require("optimizer_build_evidence")
local rocket_launch_telemetry = require("rocket_launch_telemetry")
'''

CONTROL_MODULES_ANCHOR = '''require("layout_executor")
require("research")
require("science_telemetry")
require("logistic_inventory")
require("live_execution")
require("spidertron_builder")
local laya_coach = require("laya_coach")
'''

CONTROL_MODULES_NEW = '''require("layout_executor")
require("research")
require("science_telemetry")
require("logistic_inventory")
require("live_execution")
require("spidertron_builder")
require("runtime_catalog")
require("runtime_identity_probe")
require("optimizer_consumer_observation")
require("rocket_silo_observation")
local laya_coach = require("laya_coach")
'''

ROBOT_ANCHOR = '''  local entity = event.entity
  if entity.surface and entity.surface.name == "planner-sandbox" and entity.force.name == "planner" then
'''

ROBOT_NEW = '''  local entity = event.entity
  local ownership = research_lab_ownership.capture(event)
  local optimizer_evidence = optimizer_build_evidence.capture(event)
  local ownership_entity = entity
  if entity.surface and entity.surface.name == "planner-sandbox" and entity.force.name == "planner" then
'''

TRASH_ANCHOR = '''      trash_requesters.revive_with_trash(entity.surface, entity)
    end
  end
end)

script.on_init(function()
'''

TRASH_NEW = '''      local replaced, _, replacement = trash_requesters.revive_with_trash(entity.surface, entity)
      ownership_entity = replaced and replacement or nil
    end
  end
  research_lab_ownership.record(ownership, ownership_entity)
  optimizer_build_evidence.record(optimizer_evidence, ownership_entity)
end)

script.on_event(defines.events.on_rocket_launched, rocket_launch_telemetry.on_rocket_launched)

script.on_init(function()
'''



ACTION_AUTH_OLD = '''    place_ghost = "project_more_ghosts",
    place_tile_ghost = "project_more_ghosts",'''
ACTION_AUTH_NEW = '''    place_ghost = "project_more_ghosts",
    place_ghost_no_reconcile = "project_more_ghosts",
    place_tile_ghost = "project_more_ghosts",
    configure_entity_if_unit_number = "place_core_infrastructure",'''

ATOMIC_OLD = '''        if action.action_type == "place_entity" or action.action_type == "place_ghost" then'''
ATOMIC_NEW = '''        if action.action_type == "place_entity" or action.action_type == "place_ghost"
          or action.action_type == "place_ghost_no_reconcile" then'''

CONFIGURE_OLD = '''      if action.action_type == "configure_entity" then
        counts.attempted_entities = counts.attempted_entities + 1
        local existing = find_exact_entity(surface, force, action.entity, position)
        if not existing then
          counts.failed_entities = counts.failed_entities + 1
          record_failure(phase, action, "configure_target_missing")
        else'''
CONFIGURE_NEW = '''      if action.action_type == "configure_entity" or action.action_type == "configure_entity_if_unit_number" then
        counts.attempted_entities = counts.attempted_entities + 1
        local existing = find_exact_entity(surface, force, action.entity, position)
        if not existing then
          counts.failed_entities = counts.failed_entities + 1
          record_failure(phase, action, "configure_target_missing")
        elseif action.action_type == "configure_entity_if_unit_number"
          and (type(action.expected_unit_number) ~= "number"
            or action.expected_unit_number < 1
            or action.expected_unit_number % 1 ~= 0
            or existing.unit_number ~= action.expected_unit_number) then
          counts.failed_entities = counts.failed_entities + 1
          record_failure(phase, action, "configure_target_unit_number_mismatch")
        else'''

GHOST_OLD = '''      elseif action.action_type == "place_ghost" then
        counts.attempted_ghosts = counts.attempted_ghosts + 1
        local built = find_exact_entity(surface, force, action.entity, position)
        local ghost = find_exact_ghost(surface, force, action.entity, position)
        local existing = built or ghost
        if existing then
          local configure_error = nil
          if needs_reconfiguration(action, existing) then
            configure_error = configure_created_entity(existing, action)
          end
          local mismatch = configure_error or configuration_error(existing, action, direction)
          mismatch = repair_existing_inserter_direction(existing, action, direction, mismatch)
          if mismatch then
            counts.failed_ghosts = counts.failed_ghosts + 1
            record_failure(phase, action, mismatch)
          else
            counts.already_present_ghosts = counts.already_present_ghosts + 1
          end
        elseif #exact_position_occupants(surface, force, position) > 0 then
          counts.failed_ghosts = counts.failed_ghosts + 1
          record_failure(phase, action, "exact_position_occupied_by_different_entity")'''
GHOST_NEW = '''      elseif action.action_type == "place_ghost" or action.action_type == "place_ghost_no_reconcile" then
        counts.attempted_ghosts = counts.attempted_ghosts + 1
        local built = find_exact_entity(surface, force, action.entity, position)
        local ghost = find_exact_ghost(surface, force, action.entity, position)
        local existing = built or ghost
        if existing then
          local configure_error = nil
          if action.action_type == "place_ghost" and needs_reconfiguration(action, existing) then
            configure_error = configure_created_entity(existing, action)
          end
          local mismatch = configure_error or configuration_error(existing, action, direction)
          if action.action_type == "place_ghost" then
            mismatch = repair_existing_inserter_direction(existing, action, direction, mismatch)
          end
          if mismatch then
            counts.failed_ghosts = counts.failed_ghosts + 1
            record_failure(phase, action, mismatch)
          else
            counts.already_present_ghosts = counts.already_present_ghosts + 1
          end
        elseif #exact_position_occupants(surface, force, position) > 0 then
          counts.failed_ghosts = counts.failed_ghosts + 1
          record_failure(phase, action, action.action_type == "place_ghost_no_reconcile"
            and "guarded_ghost_position_occupied" or "exact_position_occupied_by_different_entity")'''

GHOST_CREATE_OLD = '''          ghost = surface.create_entity({
            name = "entity-ghost",
            inner_name = action.entity,
            position = { position.x, position.y },
            direction = direction,
            type = action.underground_type,
            force = force
          })'''
GHOST_CREATE_NEW = '''          ghost = surface.create_entity({
            name = "entity-ghost",
            inner_name = action.entity,
            position = { position.x, position.y },
            direction = direction,
            mirror = action.mirror,
            type = action.underground_type,
            force = force,
            tags = action.research_lab_ownership or action.optimizer_ownership
          })'''



TRASH_RETURN_OLD = '''    return true
'''
TRASH_RETURN_NEW = '''    return true, nil, created
'''



OBSTACLE_OLD = "local function blocked_only_by_removals(surface, action, direction, removals)"
OBSTACLE_NEW = """-- Trees and minable rocks: a player's blueprint builds over them and robots clear them.
-- Plans opt in with build_plan.clear_natural_obstacles (Track B's runner always does);
-- plans without the flag keep treating them as collisions exactly as before.
local function clearable_obstacle(entity)
  if not entity or not entity.valid then return false end
  local ok, kind, minable = pcall(function() return entity.type, entity.minable end)
  return ok and (kind == "tree" or (kind == "simple-entity" and minable == true))
end

local function blocked_only_by_removals(surface, action, direction, removals, clear_natural)"""

OBSTACLE_CLAUSE_OLD = "        if removals[entity] then"
OBSTACLE_CLAUSE_NEW = "        if removals[entity] or (clear_natural and clearable_obstacle(entity)) then"

ATOMIC_SIG_OLD = "local function atomic_placement_blocked(surface, force, action, removals)"
ATOMIC_SIG_NEW = "local function atomic_placement_blocked(surface, force, action, removals, clear_natural)"

ATOMIC_CALL_OLD = "  return not blocked_only_by_removals(surface, action, direction, removals)"
ATOMIC_CALL_NEW = "  return not blocked_only_by_removals(surface, action, direction, removals, clear_natural)"

ORDER_OLD = "local function atomic_planned_placement_blocked(action, planned_actions)"
ORDER_NEW = """-- Order deconstruction of the trees and rocks in a ghost's footprint before the ghost is created, as a
-- blueprint does. Returns the number ordered, or nil plus a reason when one cannot be ordered.
local function order_obstacle_deconstruction(surface, force, action, direction)
  local area = oriented_collision_area(action, direction)
  local layers = collision_layers(prototypes.entity[action.entity])
  if not area or not layers then return 0 end
  local ordered = 0
  for _, entity in pairs(surface.find_entities_filtered({ area = area })) do
    if entity.valid and clearable_obstacle(entity)
      and masks_overlap(layers, collision_layers(entity.prototype or prototypes.entity[entity.name])) then
      if entity.to_be_deconstructed() or entity.order_deconstruction(force) then
        ordered = ordered + 1
      else
        return nil, "obstacle_deconstruction_refused"
      end
    end
  end
  return ordered
end

local function atomic_planned_placement_blocked(action, planned_actions)"""

PRECHECK_OLD = "          local blocked = atomic_placement_blocked(surface, force, action, removals)"
PRECHECK_NEW = """          local blocked = atomic_placement_blocked(surface, force, action, removals,
            build_plan.clear_natural_obstacles == true)"""

PREPASS_OLD = """    if #placement_failures > 0 then
      counts.failed_placements = #placement_failures
      counts.placement_failures = placement_failures
      counts.error = "atomic_footprint_blocked"
      return counts
    end
  end

  for _, phase in ipairs(build_plan.phases) do"""
PREPASS_NEW = """    if #placement_failures > 0 then
      counts.failed_placements = #placement_failures
      counts.placement_failures = placement_failures
      counts.error = "atomic_footprint_blocked"
      return counts
    end
  end

  -- Clear first, then place: robots remove the trees and rocks and build the ghost on the cleared tiles.
  if build_plan.clear_natural_obstacles == true then
    for _, phase in ipairs(build_plan.phases) do
      for _, action in ipairs(phase.actions or {}) do
        if action.action_type == "place_ghost" or action.action_type == "place_ghost_no_reconcile" then
          local direction = action.direction and defines.direction[action.direction] or nil
          local ordered, reason = order_obstacle_deconstruction(surface, force, action, direction)
          if not ordered then record_failure(phase, action, reason) end
        end
      end
    end
    if #placement_failures > 0 then
      counts.failed_placements = #placement_failures
      counts.placement_failures = placement_failures
      counts.error = "obstacle_deconstruction_refused"
      return counts
    end
  end

  for _, phase in ipairs(build_plan.phases) do"""


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {text.count(old)}")
    return text.replace(old, new)


def main(root: Path) -> int:
    mod = root / "mods" / "factorio_cursor_rl_agent"
    layout = (mod / "layout_executor.lua").read_text()
    anchor = 'commands.add_command("layout_executor_capabilities"'
    layout = replace_once(layout, anchor, HELPER + anchor, "layout helper")
    validation = '''  if not ok or type(payload) ~= "table" or type(payload.authorization) ~= "table" or type(payload.build_plan) ~= "table" then
    game.print("BuildPlan error: payload must include authorization and build_plan")
    return
  end

'''
    layout = replace_once(layout, validation, validation + CORRELATED, "correlated block")
    layout = replace_once(layout, REPORT_ANCHOR, REPORT_NEW, "report writer")
    layout = replace_once(layout, ACTION_AUTH_OLD, ACTION_AUTH_NEW, "action authorization")
    layout = replace_once(layout, ATOMIC_OLD, ATOMIC_NEW, "atomic pre-check")
    layout = replace_once(layout, CONFIGURE_OLD, CONFIGURE_NEW, "configure branch")
    layout = replace_once(layout, GHOST_OLD, GHOST_NEW, "ghost branch")
    layout = replace_once(layout, GHOST_CREATE_OLD, GHOST_CREATE_NEW, "ghost tags")
    layout = replace_once(layout, OBSTACLE_OLD, OBSTACLE_NEW, "clearable obstacle")
    layout = replace_once(layout, OBSTACLE_CLAUSE_OLD, OBSTACLE_CLAUSE_NEW, "obstacle clause")
    layout = replace_once(layout, ATOMIC_SIG_OLD, ATOMIC_SIG_NEW, "atomic signature")
    layout = replace_once(layout, ATOMIC_CALL_OLD, ATOMIC_CALL_NEW, "atomic call")
    layout = replace_once(layout, ORDER_OLD, ORDER_NEW, "obstacle ordering")
    layout = replace_once(layout, PRECHECK_OLD, PRECHECK_NEW, "precheck flag")
    layout = replace_once(layout, PREPASS_OLD, PREPASS_NEW, "obstacle pre-pass")
    (mod / "layout_executor.lua").write_text(layout)

    control = (mod / "control.lua").read_text()
    control = replace_once(control, CONTROL_ANCHOR, CONTROL_NEW, "control requires")
    control = replace_once(control, CONTROL_MODULES_ANCHOR, CONTROL_MODULES_NEW, "control modules")
    control = replace_once(control, ROBOT_ANCHOR, ROBOT_NEW, "robot capture")
    control = replace_once(control, TRASH_ANCHOR, TRASH_NEW, "robot record")
    (mod / "control.lua").write_text(control)

    trash = (mod / "trash_requesters.lua").read_text()
    # Track B's evidence capture records the replacement chest returned here;
    # A returned only a boolean, so the replacement had no build record.
    trash = replace_once(trash, TRASH_RETURN_OLD, TRASH_RETURN_NEW, "trash replacement return")
    (mod / "trash_requesters.lua").write_text(trash)
    print("patched", mod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
