-- Path: factorio_mod/cell_release.lua | Purpose: Release a tagged finite cell with durable, conserved WIP and restart-safe ownership.
local sections = require("logistic_sections")
local M = {}

local function targets(machine, spec)
  if machine.type ~= "assembling-machine" or not machine.unit_number then return nil,nil,"release_machine_unsupported" end
  local function at(position, name)
    for _,e in pairs(machine.surface.find_entities_filtered{position=position,radius=0.05,force=machine.force,name=name}) do
      if e.valid and math.abs(e.position.x-position.x)<0.01 and math.abs(e.position.y-position.y)<0.01 then return e end
    end
  end
  local requester,provider=at(spec.requester,"requester-chest"),at(spec.provider,"passive-provider-chest")
  if not requester or not provider then return nil,nil,"release_container_missing" end
  if requester.unit_number==provider.unit_number then return nil,nil,"release_same_container" end
  for _,e in ipairs({requester,provider}) do
    if math.abs(e.position.x-machine.position.x)>8 or math.abs(e.position.y-machine.position.y)>8 then
      return nil,nil,"release_container_not_local"
    end
  end
  return requester,provider
end

local function retained_items(requester)
  local keep={}
  for _,section in pairs(requester.get_logistic_sections().sections) do
    if section.valid then for index=1,section.filters_count do
      local slot=section.get_slot(index);local value=slot and slot.value
      local name=type(value)=="table" and value.name or value
      if name then keep[name]=true end
    end end
  end
  return keep
end

local function records()
  storage.laya_cell_releases=storage.laya_cell_releases or {}
  return storage.laya_cell_releases
end

local function has_items(record)
  for _,stack in ipairs(record.items) do if stack.count>0 then return true end end
  return false
end

local function move_stack(stack,destination,limit)
  for index=1,limit do
    destination[index].transfer_stack(stack)
    if not stack.valid_for_read then return false end
  end
  return stack.valid_for_read
end

function M.apply(machine,spec)
  local requester,provider,reason=targets(machine,spec)
  if reason then return reason end
  local all=records();local key=tostring(machine.unit_number);local pending=all[key]
  if pending and (pending.group~=spec.group or pending.requester~=requester.unit_number
    or pending.provider~=provider.unit_number) then return "release_owner_changed" end
  local holder=requester.get_logistic_sections();local owned=sections.find_section_by_group(holder,spec.group)
  local recipe=machine.get_recipe();local current=recipe and recipe.name
  if spec.acknowledge then
    if current~=spec.restore_recipe then return "release_restore_not_verified" end
    if (owned and not pending) or (pending and (has_items(pending) or not pending.ready)) then
      return "release_recovery_pending"
    end
    if owned then sections.clear_logistic_groups(requester,{spec.group}) end
    all[key]=nil
    return nil
  end
  if current and current~=spec.expected_recipe then return "release_recipe_changed" end
  if not owned then return current and "release_owner_missing" or (pending and "release_owner_changed" or nil) end
  if not pending then
    pending={group=spec.group,requester=requester.unit_number,provider=provider.unit_number,items={}}
    all[key]=pending
  end
  pending.ready=false
  if current then
    -- The engine removes these real items when clearing the recipe. Persist the exact returned refund
    -- before attempting delivery. A full requester/provider never invokes the legacy spill fallback.
    local removed=machine.set_recipe(nil) or {}
    for name,value in pairs(removed) do
      local stack=type(value)=="table" and value or {name=name,count=value}
      local quality=stack.quality
      if type(quality)=="table" or type(quality)=="userdata" then quality=quality.name end
      pending.items[#pending.items+1]={name=stack.name,count=stack.count,quality=quality}
    end
  end
  sections.write_section_slots(owned,{}) -- retain the unique marker until restoration is acknowledged
  local source=requester.get_inventory(defines.inventory.chest)
  local destination=provider.get_inventory(defines.inventory.chest)
  if not source or not destination then return "release_inventory_missing" end
  local limit=#destination
  if destination.supports_bar() then limit=math.min(limit,destination.get_bar()-1) end
  for _,stack in ipairs(pending.items) do
    if stack.count>0 then
      local inserted=destination.insert(stack)
      stack.count=stack.count-inserted
    end
  end
  local remaining=has_items(pending)
  local box=machine.bounding_box
  for _,inserter in pairs(machine.surface.find_entities_filtered{type="inserter",force=machine.force,
    area={{box.left_top.x-3,box.left_top.y-3},{box.right_bottom.x+3,box.right_bottom.y+3}}}) do
    local p=inserter.drop_position;local held=inserter.held_stack
    if p and p.x>=box.left_top.x and p.x<=box.right_bottom.x and p.y>=box.left_top.y and p.y<=box.right_bottom.y
      and held and held.valid_for_read then remaining=move_stack(held,destination,limit) or remaining end
  end
  local keep=retained_items(requester)
  for index=1,#source do
    local stack=source[index]
    if stack.valid_for_read and not keep[stack.name] then remaining=move_stack(stack,destination,limit) or remaining end
  end
  if remaining then return "release_destination_full" end
  pending.ready=true
  return nil
end

function M.verify(machine,spec)
  local recipe=machine.get_recipe();local current=recipe and recipe.name
  if spec.acknowledge then
    if current~=spec.restore_recipe then return "release_restore_not_verified" end
  elseif current then return "release_machine_not_vacant" end
  local requester,_provider,reason=targets(machine,spec)
  if reason then return reason end
  local pending=records()[tostring(machine.unit_number)]
  if pending and (has_items(pending) or not pending.ready) then return "release_recovery_pending" end
  local owned=sections.find_section_by_group(requester.get_logistic_sections(),spec.group)
  if spec.acknowledge and owned then return "release_recovery_pending" end
  if owned and owned.get_slot(1) and owned.get_slot(1).value then return "release_requests_not_cleared" end
  return nil
end

return M
