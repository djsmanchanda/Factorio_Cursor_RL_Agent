-- Path: factorio_mod/settings.lua
-- Purpose: Runtime activation setting for the deterministic runtime mod.
--
-- A runtime-global setting (stored in the save, synced on join) selects which
-- runtime mod is active. The deterministic and Laya artifacts each own a
-- differently named setting, so both can load together without a collision.

data:extend({
  {
    type = "bool-setting",
    name = "factorio_cursor_rl_agent_active",
    setting_type = "runtime-global",
    default_value = true,
    order = "factorio-cursor-rl-agent",
  },
})
