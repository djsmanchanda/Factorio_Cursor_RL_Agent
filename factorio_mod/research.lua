-- Path: factorio_mod/research.lua
-- Purpose: Set and report force-specific research state without creating real-base forces.

local shared = require("sandbox_shared")
local get_or_create_planner_force = shared.get_or_create_planner_force

local function parse_force(payload)
  if payload.force ~= nil and (type(payload.force) ~= "string" or payload.force == "") then
    return nil, "Research force must be a non-empty string"
  end
  return payload.force, nil
end

local function parse_research_payload(json_text)
  if not json_text or json_text == "" then
    return nil, "Missing research payload JSON"
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(json_text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid research payload JSON"
  end
  if type(payload.technology) ~= "string" or payload.technology == "" then
    return nil, "Research payload must include technology"
  end
  local _, force_err = parse_force(payload)
  if force_err then
    return nil, force_err
  end
  return payload, nil
end

local function parse_status_payload(json_text)
  if not json_text or json_text == "" then
    return {}, nil
  end

  local ok, payload = pcall(function()
    return helpers.json_to_table(json_text)
  end)
  if not ok or type(payload) ~= "table" then
    return nil, "Invalid research-status payload JSON"
  end
  local _, force_err = parse_force(payload)
  if force_err then
    return nil, force_err
  end
  if payload.technology ~= nil and (type(payload.technology) ~= "string" or payload.technology == "") then
    return nil, "Research technology must be a non-empty string"
  end
  return payload, nil
end

local function split_level_name(name)
  local stem, level_text = name:match("^(.*)%-(%d+)$")
  if not stem then
    return nil, nil
  end
  return stem, tonumber(level_text)
end

local function same_level_stem(name, stem)
  if name == stem then
    return true
  end
  return name:match("^(.*)%-%d+$") == stem
end

local function resolve_research(force, requested_name)
  local exact = force.technologies[requested_name]
  local stem, requested_level = split_level_name(requested_name)

  -- Repeatable LuaTechnology.level advances while its ranged prototype keeps
  -- the original table key (for example mining-productivity-3).
  if exact then
    local prototype = exact.prototype
    local is_ranged = prototype.max_level > prototype.level
    return exact, exact.name, (is_ranged and requested_level or nil)
  end
  if not stem then
    error("Unknown technology: " .. tostring(requested_name))
  end

  local matches = {}
  for name, technology in pairs(force.technologies) do
    local prototype = technology.prototype
    if same_level_stem(name, stem)
      and prototype.max_level > prototype.level
      and prototype.level <= requested_level
      and requested_level <= prototype.max_level then
      table.insert(matches, technology)
    end
  end
  if #matches == 0 then
    error("Unknown technology: " .. tostring(requested_name))
  end
  if #matches > 1 then
    error("Ambiguous ranged technology: " .. tostring(requested_name))
  end
  return matches[1], matches[1].name, requested_level
end

local function target_state(technology, requested_level)
  if requested_level == nil then
    return technology.researched, technology.researched and "completed" or "available"
  end
  if requested_level < technology.level then
    return true, "completed"
  end
  if requested_level > technology.level then
    return false, "future"
  end
  if technology.researched then
    return true, "completed"
  end
  return false, "current"
end

local function queued_or_current(force, canonical_name)
  if force.current_research and force.current_research.name == canonical_name then
    return true
  end
  for _, technology in ipairs(force.research_queue) do
    if technology.name == canonical_name then
      return true
    end
  end
  return false
end

local function set_research(requested_name, force_name)
  -- A supplied name resolves an existing force only. The legacy nil argument
  -- retains planner-force creation for older sandbox callers.
  local force = get_or_create_planner_force(force_name)
  local technology, canonical_name, requested_level = resolve_research(force, requested_name)
  local completed, state = target_state(technology, requested_level)
  if completed then
    error("Technology target already researched: " .. requested_name)
  end
  if state == "future" then
    error("Technology target is future: " .. requested_name .. " (current level " .. technology.level .. ")")
  end
  if queued_or_current(force, canonical_name) then
    return canonical_name, true
  end

  -- Factorio 2.0 appends with add_research, so explicit requests replace the queue.
  force.research_queue = {}
  local added = force.add_research(canonical_name)
  if added ~= true then
    error("Failed to add research target: " .. requested_name)
  end
  if not queued_or_current(force, canonical_name) then
    error("Research target was not queued after add_research: " .. requested_name)
  end
  return canonical_name, false
end

commands.add_command("set_research", "Set a research target. Parameter: JSON {technology,force?}.", function(command)
  local payload, parse_err = parse_research_payload(command.parameter)
  local report = {
    tick = game.tick,
    technology = payload and payload.technology or nil,
    force = payload and payload.force or "planner"
  }

  if parse_err then
    report.ok = false
    report.error = parse_err
  else
    local ok, canonical_name, idempotent = pcall(function()
      return set_research(payload.technology, payload.force)
    end)
    report.ok = ok
    if ok then
      report.canonical_name = canonical_name
      report.idempotent = idempotent
    else
      report.error = tostring(canonical_name)
    end
  end

  local json = helpers.table_to_json(report)
  helpers.write_file("factorio_mod/research_reports/research_" .. game.tick .. ".json", json, false)
end)

local function science_packs_for(technology)
  local science_packs = {}
  for _, ingredient in pairs(technology.research_unit_ingredients) do
    science_packs[ingredient.name] = ingredient.amount
  end
  return science_packs
end

local function science_pack_totals_for(technology, research_unit_count)
  local totals = {}
  if type(research_unit_count) ~= "number" then
    return totals
  end
  for _, ingredient in pairs(technology.research_unit_ingredients) do
    totals[ingredient.name] = ingredient.amount * research_unit_count
  end
  return totals
end

local function build_research_status(force_name, technology_name)
  local force = get_or_create_planner_force(force_name)
  local current = force.current_research
  local queue = {}
  for _, tech in ipairs(force.research_queue) do
    table.insert(queue, tech.name)
  end

  local technology = nil
  if technology_name then
    local requested, canonical_name, requested_level = resolve_research(force, technology_name)
    local completed, state = target_state(requested, requested_level)
    local research_unit_count = requested.research_unit_count
    technology = {
      name = requested.name,
      researched = requested.researched,
      enabled = requested.enabled,
      science_packs = science_packs_for(requested),
      requested_name = technology_name,
      canonical_name = canonical_name,
      requested_level = requested_level,
      current_level = requested.level,
      prototype_level = requested.prototype.level,
      max_level = requested.prototype.max_level,
      target_completed = completed,
      state = state,
      research_unit_count = research_unit_count,
      science_pack_totals = science_pack_totals_for(requested, research_unit_count)
    }
  end

  return {
    tick = game.tick,
    force = force.name,
    current_research = current and current.name or nil,
    research_progress = force.research_progress or 0,
    research_queue = queue,
    science_packs = current and science_packs_for(current) or {},
    technology = technology
  }
end

commands.add_command("research_status", "Export research state. Parameter: optional JSON {force?,technology?}.", function(command)
  local payload, parse_err = parse_status_payload(command.parameter)
  local status = { tick = game.tick, force = payload and payload.force or "planner" }
  if parse_err then
    status.ok = false
    status.error = parse_err
  else
    local ok, result = pcall(function()
      return build_research_status(payload.force, payload.technology)
    end)
    if ok then
      status = result
      status.ok = true
    else
      status.ok = false
      status.error = tostring(result)
    end
  end

  local json = helpers.table_to_json(status)
  if status.research_queue and #status.research_queue == 0 then
    json = json:gsub('"research_queue":{}', '"research_queue":[]')
  end
  local path = "factorio_mod/research_reports/research_status_" .. game.tick .. ".json"
  helpers.write_file(path, json, false)
end)
