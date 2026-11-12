-- Path: factorio_mod/optimizer_consumer_observation.lua | Purpose: Observe one scoped consumer's recipe and lifetime counter.
-- Read-only gameplay query; it neither authenticates the runtime nor authorizes construction.
local DIRECTORY = "factorio_mod/optimizer_consumer_observations/"

local function safe_request(value)
  return type(value)=="string" and #value>=1 and #value<=64 and value:match("^[%w_-]+$")~=nil
end

local function text(value)
  return type(value)=="string" and #value>=1 and #value<=128
end

local function coordinate(value)
  return type(value)=="number" and value==value and math.abs(value)<=1000000
end

local function valid(payload)
  if type(payload)~="table" or not safe_request(payload.request_id) then return false end
  local allowed={request_id=true,surface=true,force=true,entity=true,position=true,unit_number=true}
  for key in pairs(payload) do if not allowed[key] then return false end end
  local p=payload.position
  if not text(payload.surface) or not text(payload.force) or not text(payload.entity)
    or type(payload.unit_number)~="number" or payload.unit_number<1 or payload.unit_number%1~=0
    or payload.unit_number>9007199254740991 or type(p)~="table"
    or not coordinate(p.x) or not coordinate(p.y) then return false end
  for key in pairs(p) do if key~="x" and key~="y" then return false end end
  return true
end

local function query(payload, surface, force)
  local ok, entities=pcall(function()
    return surface.find_entities_filtered{position=payload.position,name=payload.entity,force=force,limit=65}
  end)
  if not ok or type(entities)~="table" or #entities>=65 then
    return {state="unavailable",reason="entity_scan_unavailable_or_truncated"}
  end
  local target=nil
  for _,entity in pairs(entities) do
    local read, matches=pcall(function()
      return entity.valid and entity.name==payload.entity and entity.force.name==payload.force
        and entity.surface.name==payload.surface and entity.unit_number==payload.unit_number
        and entity.position.x==payload.position.x and entity.position.y==payload.position.y
    end)
    if not read then return {state="unavailable",reason="entity_identity_unreadable"} end
    if matches then
      if target then return {state="unavailable",reason="entity_identity_ambiguous"} end
      target=entity
    end
  end
  if not target then return {state="unavailable",reason="entity_not_found"} end
  local kind_ok, kind=pcall(function() return target.type end)
  if not kind_ok or (kind~="assembling-machine" and kind~="furnace" and kind~="rocket-silo") then
    return {state="unavailable",reason="unsupported_consumer_type"}
  end
  local recipe={state="unavailable"}
  local recipe_ok, value=pcall(function() return target.get_recipe() end)
  if recipe_ok then
    if value==nil then recipe={state="unset"}
    else
      local name_ok, name=pcall(function() return value.name end)
      if name_ok and text(name) then recipe={state="available",value=name} end
    end
  end
  local counter={state="unavailable"}
  local counter_ok, finished=pcall(function() return target.products_finished end)
  if counter_ok and type(finished)=="number" and finished>=0
    and finished<=9007199254740991 and finished%1==0 then
    counter={state="available",value=finished}
  end
  return {state="present",recipe=recipe,products_finished=counter}
end

commands.add_command("optimizer_consumer_observation", "Observe one scoped consumer without granting authority.", function(command)
  local ok,payload=pcall(function() return helpers.json_to_table(command.parameter or "") end)
  if not ok or type(payload)~="table" or not safe_request(payload.request_id) then return end
  local report={version="optimizer-consumer-observation-v1",kind="optimizer_consumer_observation",
    request_id=payload.request_id,tick=game.tick,ok=false,ownership_claimed=false,dispatch_authorized=false}
  if not valid(payload) then
    report.error="invalid_request"
  else
    local surface,force=game.surfaces[payload.surface],game.forces[payload.force]
    if not surface or not force then
      report.error="invalid_scope"
    else
      report.ok=true
      report.surface=payload.surface
      report.force=payload.force
      report.requested={entity=payload.entity,unit_number=payload.unit_number,
                        position={x=payload.position.x,y=payload.position.y}}
      report.observation=query(payload,surface,force)
    end
  end
  helpers.write_file(DIRECTORY.."optimizer_consumer_"..payload.request_id..".json",helpers.table_to_json(report),false)
end)
