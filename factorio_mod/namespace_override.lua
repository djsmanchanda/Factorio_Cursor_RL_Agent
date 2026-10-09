-- Path: factorio_mod/namespace_override.lua
-- Purpose: Runtime identity for the deterministic artifact. The mod-split build
-- overwrites this file for the Laya artifact; the committed values are the
-- deterministic runtime so the source tree deploys as-is.

return {
  runtime = "deterministic",
  mod_name = "factorio_cursor_rl_agent",
  command_prefix = "",
  remote_interface = "factorio_cursor_rl_agent",
  output_root = "factorio_mod",
  setting_name = "factorio_cursor_rl_agent_active",
  default_active = true,
}
