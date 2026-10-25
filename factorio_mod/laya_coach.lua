-- Path: factorio_mod/laya_coach.lua
-- Purpose: Provide a read-only Factorio window for human Laya guidance and labels.

local shared = require("sandbox_shared")
local namespace = require("namespace")

local FRAME_NAME = "laya_coach_frame"
local TOGGLE_NAME = "laya_coach_toggle"
local OUTPUT_DIRECTORY = "factorio_mod/laya_guidance/"
local MAX_SAMPLE_ENTITIES = 160
local MAX_TEXT = 2000
local AREA_TOOL = "laya-coach-area-tool"
local PREVIEW_INTERVAL_TICKS = 10
local PREVIEW_RESOURCE_LIMIT = 2000
local COMMIT_RESOURCE_LIMIT = 20000

local DECISION_KINDS = {
  "serve_goal", "choose_operation", "select_site", "select_scale", "select_route",
  "select_research", "select_upgrade", "recovery", "wait_or_act", "select_candidate"
}
local FOCUSES = { "general", "throughput", "reliability", "efficiency", "exploration" }
local OUTCOMES = { "unknown", "success", "failure", "blocked" }

local function coach_storage()
  local state = shared.ensure_storage()
  state.laya_coach = state.laya_coach or { sequence = 0 }
  state.laya_coach.pending_area = state.laya_coach.pending_area or {}
  state.laya_coach.preview_tick = state.laya_coach.preview_tick or {}
  return state.laya_coach
end

local function bounded_text(value, maximum)
  local text = tostring(value or ""):gsub("%c", " ")
  maximum = maximum or MAX_TEXT
  if #text > maximum then
    return text:sub(1, maximum)
  end
  return text
end

local function list_from_text(value)
  local result = {}
  local seen = {}
  for token in string.gmatch(tostring(value or ""), "[^,;]+") do
    local normalized = bounded_text(token:gsub("^%s+", ""):gsub("%s+$", ""), 128)
    if normalized ~= "" and not seen[normalized] then
      result[#result + 1] = normalized
      seen[normalized] = true
    end
    if #result >= 20 then break end
  end
  return result
end

local function add_toggle(player)
  if not player or not player.valid then return end
  if player.gui.top[TOGGLE_NAME] then return end
  player.gui.top.add({
    type = "button",
    name = TOGGLE_NAME,
    caption = "Laya Coach",
    tooltip = "Record an observation, goal, correction, or labelled decision for future Laya training."
  })
end

local function close_window(player)
  if player and player.valid and player.gui.screen[FRAME_NAME] then
    player.gui.screen[FRAME_NAME].destroy()
  end
end

local function add_field(parent, label, name, text, single_line)
  parent.add({ type = "label", caption = label })
  local element = parent.add({
    type = single_line and "textfield" or "text-box",
    name = name,
    text = text or ""
  })
  if not single_line then
    element.style.height = 70
  end
  return element
end

-- Training worlds come from the training lab's remote interface; the section only
-- appears on servers that run the lab (disposable workers), never on the real base.
local LAB_INTERFACE = "factorio_training_lab"
local WORLDS_NAME = "laya_coach_worlds"

local function lab_available()
  return remote.interfaces[LAB_INTERFACE] ~= nil
end

local function render_worlds(frame)
  if frame[WORLDS_NAME] then frame[WORLDS_NAME].destroy() end
  local section = frame.add({ type = "frame", name = WORLDS_NAME, direction = "vertical", caption = "Training worlds", index = 2 })
  local worlds = remote.call(LAB_INTERFACE, "active_worlds")
  if #worlds == 0 then
    section.add({ type = "label", caption = "No active training world right now." })
  end
  local list = section.add({ type = "table", column_count = 2 })
  for _, world in ipairs(worlds) do
    local level = world.level and (" L" .. world.level) or ""
    list.add({ type = "label", caption = world.surface .. "  ·  " .. tostring(world.family) .. level .. "  ·  " .. world.status })
    list.add({ type = "button", caption = "View", tags = { laya_coach_view_surface = world.surface },
      tooltip = "Spectate this training world" })
  end
  local controls = section.add({ type = "flow", direction = "horizontal" })
  controls.add({ type = "button", name = "laya_coach_worlds_refresh", caption = "Refresh list" })
  controls.add({ type = "button", name = "laya_coach_worlds_leave", caption = "Leave training world" })
end

-- Area selection: read-only queries over a dragged box. Nothing here builds,
-- mines or changes an entity; the box only annotates the next coach record.
local function bounds(area)
  local lt, rb = area.left_top or area[1], area.right_bottom or area[2]
  return {
    left_top = { x = lt.x or lt[1], y = lt.y or lt[2] },
    right_bottom = { x = rb.x or rb[1], y = rb.y or rb[2] }
  }
end

local function mode_name(event, fallback)
  local mode = event.selection_mode
  if type(mode) == "string" then return mode end
  if mode ~= nil and defines.selection_mode then
    for name, value in pairs(defines.selection_mode) do
      if value == mode then return name end
    end
  end
  return fallback
end

local function area_summary(surface, box, resource_limit, with_sample)
  local resources = {}
  local found = surface.find_entities_filtered({ area = box, type = "resource", limit = resource_limit })
  for _, resource in pairs(found) do
    resources[resource.name] = (resources[resource.name] or 0) + resource.amount
  end
  local summary = {
    entity_count = surface.count_entities_filtered({ area = box, type = { "resource", "character" }, invert = true }),
    resource_entity_count = #found,
    resources = resources,
    truncated = #found >= resource_limit
  }
  if with_sample then
    summary.entities = {}
    local sample = surface.find_entities_filtered({
      area = box, type = { "resource", "character" }, invert = true, limit = MAX_SAMPLE_ENTITIES
    })
    for _, entity in pairs(sample) do
      summary.entities[#summary.entities + 1] = {
        name = entity.name, type = entity.type, position = { x = entity.position.x, y = entity.position.y }
      }
    end
  end
  return summary
end

local function summary_text(box, summary)
  local width = box.right_bottom.x - box.left_top.x
  local height = box.right_bottom.y - box.left_top.y
  local ores = {}
  for name, amount in pairs(summary.resources) do ores[#ores + 1] = name .. " " .. amount end
  table.sort(ores)
  local text = string.format("%dx%d tiles, %d entities", math.floor(width + 0.5), math.floor(height + 0.5), summary.entity_count)
  if #ores > 0 then text = text .. ", ore: " .. table.concat(ores, ", ") end
  if summary.truncated then text = text .. " (ore totals truncated)" end
  return text
end

local function area_caption(area)
  if not area then return "Area: none (use Select area to attach one)" end
  return "Area [" .. area.selection_mode .. "]: " .. summary_text(area, area)
end

local function open_window(player)
  close_window(player)
  local frame = player.gui.screen.add({
    type = "frame", name = FRAME_NAME, direction = "vertical", caption = "Laya Coach"
  })
  frame.auto_center = true
  frame.style.width = 560

  local header = frame.add({ type = "label", caption = "Observation and guidance only — this window never executes actions." })
  header.style.single_line = false
  if lab_available() then render_worlds(frame) end

  local decision_flow = frame.add({ type = "flow", direction = "horizontal" })
  decision_flow.add({ type = "label", caption = "Decision type" })
  decision_flow.add({ type = "drop-down", name = "laya_coach_decision_kind", items = DECISION_KINDS, selected_index = 1 })
  decision_flow.add({ type = "label", caption = "Focus" })
  decision_flow.add({ type = "drop-down", name = "laya_coach_focus", items = FOCUSES, selected_index = 1 })

  add_field(frame, "Goal or priority", "laya_coach_goal", "", true)
  add_field(frame, "Guidance / correction / explanation", "laya_coach_instruction", "", false)
  add_field(frame, "Candidate option IDs (comma-separated, optional)", "laya_coach_options", "", true)
  add_field(frame, "Chosen option ID (optional; required for a training label)", "laya_coach_choice", "", true)

  local outcome_flow = frame.add({ type = "flow", direction = "horizontal" })
  outcome_flow.add({ type = "label", caption = "Observed outcome" })
  outcome_flow.add({ type = "drop-down", name = "laya_coach_outcome", items = OUTCOMES, selected_index = 1 })

  local area_flow = frame.add({ type = "flow", direction = "horizontal" })
  area_flow.add({ type = "button", name = "laya_coach_area", caption = "Select area",
    tooltip = "Drag a box to attach an area to the next saved record. Shift-drag records alt_select (e.g. avoid)." })
  area_flow.add({ type = "button", name = "laya_coach_area_clear", caption = "Clear area" })
  local area_label = frame.add({ type = "label", name = "laya_coach_area_status", caption = area_caption(coach_storage().pending_area[player.index]) })
  area_label.style.single_line = false

  local buttons = frame.add({ type = "flow", direction = "horizontal" })
  buttons.add({ type = "button", name = "laya_coach_capture", caption = "Capture observation" })
  buttons.add({ type = "button", name = "laya_coach_submit", caption = "Save guidance" })
  buttons.add({ type = "button", name = "laya_coach_close", caption = "Close" })
  frame.add({ type = "label", name = "laya_coach_status", caption = "Ready" })
end

local function inventory_snapshot(player)
  local counts = {}
  local ok, inventory = pcall(function() return player.get_main_inventory() end)
  if not ok or not inventory or not inventory.valid then return counts end
  for name, count in pairs(inventory.get_contents()) do
    if type(name) == "string" and type(count) == "number" and count > 0 then
      counts[name] = count
    end
  end
  return counts
end

local function world_observation(player)
  local surface = player.surface
  local force = player.force
  local entities = {}
  local counts = {}
  local ok, found = pcall(function()
    return surface.find_entities_filtered({ force = force, limit = MAX_SAMPLE_ENTITIES })
  end)
  if ok and found then
    for _, entity in pairs(found) do
      if entity.valid and entity.type ~= "character" then
        counts[entity.type] = (counts[entity.type] or 0) + 1
        if #entities < MAX_SAMPLE_ENTITIES then
          entities[#entities + 1] = {
            name = entity.name,
            type = entity.type,
            position = { x = entity.position.x, y = entity.position.y },
            direction = entity.direction
          }
        end
      end
    end
  end
  local queue = {}
  for _, technology in ipairs(force.research_queue or {}) do
    if #queue >= 32 then break end
    queue[#queue + 1] = technology.name
  end
  return {
    tick = game.tick,
    surface = surface.name,
    force = force.name,
    player_position = { x = player.position.x, y = player.position.y },
    entity_counts_sampled = counts,
    entities = entities,
    inventory = inventory_snapshot(player),
    research = {
      current = force.current_research and force.current_research.name or nil,
      queue = queue,
      researched_count = force.technologies and #force.technologies or nil
    }
  }
end

local function selected_item(element, items)
  local index = tonumber(element and element.selected_index or 1) or 1
  return items[index] or items[1]
end

local function new_record(player, frame, feedback_kind)
  local state = coach_storage()
  state.sequence = (state.sequence or 0) + 1
  local observation = world_observation(player)
  local decision_kind = selected_item(frame.laya_coach_decision_kind, DECISION_KINDS)
  local focus = selected_item(frame.laya_coach_focus, FOCUSES)
  local outcome = selected_item(frame.laya_coach_outcome, OUTCOMES)
  local options = list_from_text(frame.laya_coach_options.text)
  local choice = bounded_text(frame.laya_coach_choice.text, 128)
  if choice == "" then choice = nil end
  local instruction = bounded_text(frame.laya_coach_instruction.text, 1000)
  local goal = bounded_text(frame.laya_coach_goal.text, 512)
  if instruction == "" then instruction = goal end
  if instruction == "" then instruction = "Capture the current factory state for Laya." end
  local runtime = player.surface.name == "nauvis" and "real_nauvis" or "disposable_training"
  local record_id = string.format("coach-%d-%d-%d", game.tick, player.index, state.sequence)
  return {
    version = "1.0.0",
    record_id = record_id,
    -- Factorio's deterministic Lua runtime does not expose wall-clock time.
    -- The authoritative capture clock is the tick in source; the epoch value
    -- keeps the record compatible with the JSON date-time contract.
    captured_at = "1970-01-01T00:00:00Z",
    reviewer_id = bounded_text(player.name, 128),
    decision_kind = decision_kind,
    source = {
      runtime = runtime,
      surface = player.surface.name,
      force = player.force.name,
      tick = game.tick,
      elapsed_ticks = game.tick,
      run_id = record_id,
      episode_id = record_id,
      trajectory_id = string.format("coach-trajectory-%d-%d", player.index, game.tick)
    },
    guidance = {
      focus = focus,
      goal = goal,
      instruction = instruction,
      outcome = outcome,
      notes = instruction,
      feedback_kind = feedback_kind,
      confidence = choice and 1.0 or 0.5
    },
    options = options,
    chosen_option_id = choice,
    confidence = choice and 1.0 or 0.5,
    observation = observation,
    area = state.pending_area[player.index]
  }
end

local function write_record(player, frame, feedback_kind)
  local record = new_record(player, frame, feedback_kind)
  local json = helpers.table_to_json(record)
  -- Factorio serialises empty Lua arrays as objects. Preserve array contracts.
  json = json:gsub('"options":{}', '"options":[]')
  json = json:gsub('"queue":{}', '"queue":[]')
  json = json:gsub('"entities":{}', '"entities":[]')
  helpers.write_file(
    OUTPUT_DIRECTORY .. record.record_id .. ".json",
    json,
    false
  )
  if record.area then
    coach_storage().pending_area[player.index] = nil
    frame.laya_coach_area_status.caption = area_caption(nil)
  end
  return record
end


local function set_status(frame, message)
  if frame and frame.valid and frame.laya_coach_status then
    frame.laya_coach_status.caption = message
  end
end

local function tool_event(event)
  local player = game.get_player(event.player_index)
  if not player then return nil end
  local item = event.item
  if item == nil and player.cursor_stack and player.cursor_stack.valid_for_read then
    item = player.cursor_stack.name
  end
  if item ~= AREA_TOOL then return nil end
  return player, event.surface or player.surface
end

local function on_preselect(event)
  local player, surface = tool_event(event)
  if not player then return end
  local state = coach_storage()
  local last = state.preview_tick[player.index]
  if last and game.tick - last < PREVIEW_INTERVAL_TICKS then return end
  state.preview_tick[player.index] = game.tick
  local frame = player.gui.screen[FRAME_NAME]
  if not frame or not event.area then return end
  local box = bounds(event.area)
  set_status(frame, "Preview: " .. summary_text(box, area_summary(surface, box, PREVIEW_RESOURCE_LIMIT, false)))
end

local function on_cancel(event)
  local player = tool_event(event)
  if player then set_status(player.gui.screen[FRAME_NAME], "Area selection cancelled") end
end

local function on_selected(event, fallback_mode)
  local player, surface = tool_event(event)
  if not player then return end
  local area = area_summary(surface, bounds(event.area), COMMIT_RESOURCE_LIMIT, true)
  local box = bounds(event.area)
  area.left_top, area.right_bottom = box.left_top, box.right_bottom
  area.surface = surface.name
  area.tick = game.tick
  area.selection_mode = mode_name(event, fallback_mode)
  coach_storage().pending_area[player.index] = area
  if not player.gui.screen[FRAME_NAME] then open_window(player) end
  local frame = player.gui.screen[FRAME_NAME]
  frame.laya_coach_area_status.caption = area_caption(area)
  set_status(frame, "Area attached; label it and save.")
end

local function on_click(event)
  if not namespace.is_active() then return end
  local element = event.element
  if not element or not element.valid then return end
  local player = game.get_player(event.player_index)
  if not player then return end
  if element.name == TOGGLE_NAME then
    if player.gui.screen[FRAME_NAME] then close_window(player) else open_window(player) end
    return
  end
  local frame = player.gui.screen[FRAME_NAME]
  local view_surface = element.tags and element.tags.laya_coach_view_surface
  if view_surface and lab_available() then
    local result = remote.call(LAB_INTERFACE, "view_world", player.index, view_surface)
    set_status(frame, result.message)
    return
  end
  if element.name == "laya_coach_worlds_refresh" and frame and frame.valid and lab_available() then
    render_worlds(frame)
    set_status(frame, "Training world list refreshed")
    return
  end
  if element.name == "laya_coach_worlds_leave" and lab_available() then
    local result = remote.call(LAB_INTERFACE, "leave_world", player.index)
    set_status(frame, result.message)
    return
  end
  if element.name == "laya_coach_area" then
    if player.clear_cursor() and player.cursor_stack.set_stack({ name = AREA_TOOL }) then
      set_status(frame, "Drag a box over the area; its summary appears below.")
    else
      set_status(frame, "Could not put the area tool in the cursor.")
    end
    return
  end
  if element.name == "laya_coach_area_clear" then
    coach_storage().pending_area[player.index] = nil
    if frame and frame.valid then frame.laya_coach_area_status.caption = area_caption(nil) end
    return
  end
  if element.name == "laya_coach_close" then
    close_window(player)
  elseif frame and frame.valid and element.name == "laya_coach_capture" then
    local record = write_record(player, frame, "outcome")
    set_status(frame, "Observation saved: " .. record.record_id)
  elseif frame and frame.valid and element.name == "laya_coach_submit" then
    local record = write_record(player, frame, frame.laya_coach_choice.text ~= "" and "label" or "guidance")
    if record.chosen_option_id then
      set_status(frame, "Label saved for future fine-tuning: " .. record.record_id)
    else
      set_status(frame, "Guidance saved; add options and a chosen option to create a label.")
    end
  end
end

local function on_init()
  if not namespace.is_active() then return end
  coach_storage()
  for _, player in pairs(game.players) do add_toggle(player) end
end

local function on_configuration_changed()
  if not namespace.is_active() then return end
  coach_storage()
  for _, player in pairs(game.players) do add_toggle(player) end
end

script.on_event(defines.events.on_player_created, function(event)
  if not namespace.is_active() then return end
  add_toggle(game.get_player(event.player_index))
end)

script.on_event(defines.events.on_player_joined_game, function(event)
  if not namespace.is_active() then return end
  add_toggle(game.get_player(event.player_index))
end)

script.on_event(defines.events.on_gui_click, on_click)

script.on_event(defines.events.on_player_start_selected_area, on_preselect)
script.on_event(defines.events.on_player_change_selected_area, on_preselect)
script.on_event(defines.events.on_player_cancel_selected_area, on_cancel)
script.on_event(defines.events.on_player_selected_area, function(event) on_selected(event, "select") end)
script.on_event(defines.events.on_player_alt_selected_area, function(event) on_selected(event, "alt_select") end)

script.on_event(defines.events.on_gui_closed, function(event)
  if not namespace.is_active() then return end
  if event.element and event.element.valid and event.element.name == FRAME_NAME then
    event.element.destroy()
  end
end)

return {
  on_init = on_init,
  on_configuration_changed = on_configuration_changed,
}
