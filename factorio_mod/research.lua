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

local function set_research(technology, force_name)
  -- A supplied name resolves an existing force only. The legacy nil argument
  -- retains planner-force creation for older sandbox callers.
  local force = get_or_create_planner_force(force_name)
  local tech = force.technologies[technology]
  if not tech then
    error("Unknown technology: " .. tostring(technology))
  end
  if tech.researched then
    error("Technology already researched: " .. technology)
  end

  -- Factorio 2.0 appends with add_research, so retain deterministic one-target
  -- behavior for an explicit user request.
  force.research_queue = {}
  force.add_research(technology)
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
    local ok, run_err = pcall(function()
      set_research(payload.technology, payload.force)
    end)
    report.ok = ok
    if not ok then
      report.error = tostring(run_err)
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

local function build_research_status(force_name, technology_name)
  local force = get_or_create_planner_force(force_name)
  local current = force.current_research
  local queue = {}
  for _, tech in ipairs(force.research_queue) do
    table.insert(queue, tech.name)
  end

  local technology = nil
  if technology_name then
    local requested = force.technologies[technology_name]
    if not requested then
      error("Unknown technology: " .. tostring(technology_name))
    end
    technology = {
      name = requested.name,
      researched = requested.researched,
      enabled = requested.enabled,
      science_packs = science_packs_for(requested)
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
