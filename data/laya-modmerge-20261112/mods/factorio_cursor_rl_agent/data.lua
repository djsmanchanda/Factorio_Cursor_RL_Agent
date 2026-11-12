-- Path: factorio_mod/data.lua
-- Purpose: Declare the read-only Laya coach area selection tool.

-- The tool only reports a dragged box to control.lua; selecting never builds,
-- mines or deconstructs. `send_pre_selection_events` drives the live preview.
data:extend({
  {
    type = "selection-tool",
    name = "laya-coach-area-tool",
    icon = "__base__/graphics/icons/blueprint.png",
    icon_size = 64,
    flags = { "only-in-cursor", "not-stackable", "spawnable" },
    hidden = true,
    subgroup = "other",
    order = "z[laya-coach-area-tool]",
    stack_size = 1,
    send_pre_selection_events = true,
    select = {
      border_color = { 0.3, 0.8, 1.0 },
      cursor_box_type = "entity",
      mode = { "any-entity" },
    },
    alt_select = {
      border_color = { 1.0, 0.5, 0.2 },
      cursor_box_type = "not-allowed",
      mode = { "any-entity" },
    },
  },
})
