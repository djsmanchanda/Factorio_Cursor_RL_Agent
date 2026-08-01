-- Path: factorio_mod/logistic_sections.lua
-- Purpose: Pure logistic-section bookkeeping for requester chests, separated from the executor so it can be tested without a running game.

-- Extracted from layout_executor so it has a seam. Every function here touches
-- only LuaLogisticSections and plain tables, so tests drive it with stubs;
-- getting this wrong is expensive and quiet -- a chest with no requests looks
-- exactly like a chest whose requests were never asked for.

local M = {}

-- One section per consuming machine, labelled with that machine's group. The
-- game sums the sections, the label says which machine each set is for, and
-- re-running a build rewrites only that machine's own section instead of
-- accumulating onto whatever the chest already held.
function M.find_section_by_group(sections, group)
  for _, section in pairs(sections.sections) do
    if section.valid and section.group == group then
      return section
    end
  end
  return nil
end

-- A chest placed by create_entity starts with one blank unnamed section. Claim
-- that before adding another, so a single-machine cell ends up with exactly one
-- section rather than an empty one trailing every labelled group.
function M.claim_section_for_group(sections, group)
  for _, section in pairs(sections.sections) do
    if section.valid and section.group == "" and section.filters_count == 0 then
      section.group = group
      return section
    end
  end
  local section = sections.add_section()
  section.group = group
  return section
end

function M.verify_section_slots(section, requests)
  for index, request in ipairs(requests) do
    local slot = section.get_slot(index)
    local name = slot and slot.value and (slot.value.name or slot.value)
    local count = slot and tonumber(slot.min) or nil
    if name ~= request.name or count ~= tonumber(request.count) then
      error("slot=" .. index .. ",expected=" .. request.name .. ":" .. request.count
        .. ",actual=" .. tostring(name) .. ":" .. tostring(count))
    end
  end
end

-- Trailing slots are cleared, not left behind: a section rewritten to a shorter
-- request list would otherwise keep asking for whatever the longer list held.
function M.write_section_slots(section, requests)
  for index, request in ipairs(requests) do
    section.set_slot(index, { value = request.name, min = request.count })
  end
  for index = #requests + 1, section.filters_count do
    section.clear_slot(index)
  end
end

function M.clear_logistic_groups(entity, groups)
  local sections = entity.get_logistic_sections()
  if not sections then error("no_logistic_sections") end
  for _, group in ipairs(groups) do
    local section = M.find_section_by_group(sections, group)
    if section then M.write_section_slots(section, {}) end
  end
end

-- Which fields mean "this action still has settings to apply to an entity that
-- ALREADY exists". Listed once, because an omission here is silent: the entity
-- is reported already_present, no failure is recorded, and the setting simply
-- never lands -- indistinguishable from the planner never asking for it. Adding
-- a configurable action field means adding it here.
M.SETTING_FIELDS = {
  "logistic_request", "logistic_requests", "logistic_sections", "clear_logistic_groups", "inventory_limit",
  "infinity_filter",
}

function M.has_settings(action)
  for _, field in ipairs(M.SETTING_FIELDS) do
    if action[field] ~= nil then return true end
  end
  return false
end

-- A ghost was configured when it was created, so only a real entity needs its
-- recipe reapplied; settings above are reapplied to either.
function M.needs_reconfiguration(action, entity)
  if M.has_settings(action) then return true end
  return action.recipe ~= nil and entity.type ~= "entity-ghost"
end

return M
