-- Path: factorio_training_lab/training_geometry.lua
-- Purpose: Keep all training build footprints inside their scenario boundary.

local function prototype_bounds(name, position)
  if type(position) ~= "table" or type(position.x) ~= "number" or type(position.y) ~= "number" then
    return nil
  end
  local prototype = prototypes.entity[name]
  local box = prototype and (prototype.collision_box or prototype.selection_box)
  if not box or not box.left_top or not box.right_bottom then return nil end
  return {
    x_min = position.x + box.left_top.x,
    x_max = position.x + box.right_bottom.x,
    y_min = position.y + box.left_top.y,
    y_max = position.y + box.right_bottom.y,
  }
end

local function fits(bounds, name, position)
  local footprint = prototype_bounds(name, position)
  return footprint ~= nil
    and footprint.x_min >= bounds.x_min and footprint.x_max <= bounds.x_max_exclusive
    and footprint.y_min >= bounds.y_min and footprint.y_max <= bounds.y_max_exclusive
end

return {
  fits = fits,
  prototype_bounds = prototype_bounds,
}