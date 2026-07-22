-- Path: factorio_mod/research.lua
-- Purpose: Set and report deterministic planner-force research state.



local shared = require("sandbox_shared")
local get_or_create_planner_force = shared.get_or_create_planner_force

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

  return payload, nil
end

local function set_research(technology)
  local force = get_or_create_planner_force()
  local tech = force.technologies[technology]
  if not tech then
    error("Unknown technology: " .. tostring(technology))
  end
  if tech.researched then
    error("Technology already researched: " .. technology)
  end

  -- Factorio 2.0: force.add_research appends to the research queue, so clear
  -- it first for deterministic single-target behavior.
  force.research_queue = {}
  force.add_research(technology)
end

commands.add_command("set_research", "Set the current research target for force planner. Parameter: JSON {technology}.", function(command)
  local payload, parse_err = parse_research_payload(command.parameter)

  local report = { tick = game.tick }
  report.technology = payload and payload.technology or nil

  if parse_err then
    report.ok = false
    report.error = parse_err
  else
    local ok, run_err = pcall(function()
      set_research(payload.technology)
    end)
    report.ok = ok
    if not ok then
      report.error = tostring(run_err)
    end
  end

  local json = helpers.table_to_json(report)
  helpers.write_file("factorio_mod/research_reports/research_" .. game.tick .. ".json", json, false)
end)

local function build_research_status()
  local force = get_or_create_planner_force()
  local current = force.current_research

  local queue = {}
  for _, tech in ipairs(force.research_queue) do
    table.insert(queue, tech.name)
  end

  local science_packs = {}
  if current then
    for _, ingredient in pairs(current.research_unit_ingredients) do
      science_packs[ingredient.name] = ingredient.amount
    end
  end

  return {
    tick = game.tick,
    current_research = current and current.name or nil,
    research_progress = force.research_progress or 0,
    research_queue = queue,
    science_packs = science_packs
  }
end

commands.add_command("research_status", "Export current research status JSON (current research, progress, queue, science packs).", function(command)
  local status = build_research_status()

  local json = helpers.table_to_json(status)
  -- Empty Lua tables serialize as {} but the schema requires an array.
  if #status.research_queue == 0 then
    json = json:gsub('"research_queue":{}', '"research_queue":[]')
  end
  local path = "factorio_mod/research_reports/research_status_" .. game.tick .. ".json"
  helpers.write_file(path, json, false)
end)
