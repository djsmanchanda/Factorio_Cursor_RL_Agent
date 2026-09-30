-- Path: factorio_mod/laya_coach.lua
-- Purpose: Provide a read-only Factorio window for human Laya guidance and labels.

local shared = require("sandbox_shared")

local FRAME_NAME = "laya_coach_frame"
local TOGGLE_NAME = "laya_coach_toggle"
local OUTPUT_DIRECTORY = "factorio_mod/laya_guidance/"
local MAX_SAMPLE_ENTITIES = 160
local MAX_TEXT = 2000

local DECISION_KINDS = {
  "serve_goal", "choose_operation", "select_site", "select_scale", "select_route",
  "select_research", "select_upgrade", "recovery", "wait_or_act", "select_candidate"
}
local FOCUSES = { "general", "throughput", "reliability", "efficiency", "exploration" }
local OUTCOMES = { "unknown", "success", "failure", "blocked" }

local function coach_storage()
  local state = shared.ensure_storage()
  state.laya_coach = state.laya_coach or { sequence = 0 }
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
    observation = observation
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
  return record
end

local function set_status(frame, message)
  if frame and frame.valid and frame.laya_coach_status then
    frame.laya_coach_status.caption = message
  end
end

local function on_click(event)
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
  coach_storage()
  for _, player in pairs(game.players) do add_toggle(player) end
end

local function on_configuration_changed()
  coach_storage()
  for _, player in pairs(game.players) do add_toggle(player) end
end

script.on_event(defines.events.on_player_created, function(event)
  add_toggle(game.get_player(event.player_index))
end)

script.on_event(defines.events.on_player_joined_game, function(event)
  add_toggle(game.get_player(event.player_index))
end)

script.on_event(defines.events.on_gui_click, on_click)

script.on_event(defines.events.on_gui_closed, function(event)
  if event.element and event.element.valid and event.element.name == FRAME_NAME then
    event.element.destroy()
  end
end)

return {
  on_init = on_init,
  on_configuration_changed = on_configuration_changed,
}
