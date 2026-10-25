-- Path: factorio_mod/namespace.lua
-- Purpose: One runtime's identity (mod name, command prefix, remote interface,
-- script-output root) plus the environment wrappers that namespace the shared
-- deterministic modules.
--
-- The committed defaults are the deterministic runtime. The mod-split build
-- (tools/build_runtime_mods.py) emits the Laya artifact by overwriting
-- namespace_override.lua and settings.lua; the shared modules never change.

local defaults = {
  runtime = "deterministic",
  mod_name = "factorio_cursor_rl_agent",
  command_prefix = "",
  remote_interface = "factorio_cursor_rl_agent",
  output_root = "factorio_mod",
  setting_name = "factorio_cursor_rl_agent_active",
  default_active = true,
}

local override_ok, override = pcall(require, "namespace_override")
if override_ok and type(override) == "table" then
  for key, value in pairs(override) do
    defaults[key] = value
  end
end

local namespace = {}
namespace.runtime = defaults.runtime
namespace.mod_name = defaults.mod_name
namespace.command_prefix = defaults.command_prefix
namespace.remote_interface = defaults.remote_interface
namespace.output_root = defaults.output_root
namespace.setting_name = defaults.setting_name
namespace.default_active = defaults.default_active

function namespace.command_name(name)
  name = tostring(name or "")
  if name:sub(1, 1) == "/" then
    name = name:sub(2)
  end
  return namespace.command_prefix .. name
end

-- Shared modules hard-code "factorio_mod/" as the logical report root. A
-- non-deterministic runtime rewrites that prefix so the two mods never write
-- into the same script-output directory.
function namespace.output_path(path)
  path = tostring(path or "")
  if namespace.output_root == "factorio_mod" then
    return path
  end
  return (path:gsub("^factorio_mod/", namespace.output_root .. "/", 1))
end

function namespace.info()
  return {
    runtime = namespace.runtime,
    mod_name = namespace.mod_name,
    command_prefix = namespace.command_prefix,
    remote_interface = namespace.remote_interface,
    output_root = namespace.output_root,
    setting_name = namespace.setting_name,
  }
end

-- Whether this runtime is the save's selected runtime. Absent the setting
-- (e.g. a save created before the mod was added) fall back to the build default.
function namespace.is_active()
  local ok, value = pcall(function()
    return settings.global[namespace.setting_name].value
  end)
  if ok and value ~= nil then
    return value
  end
  return namespace.default_active
end

function namespace.set_active(active)
  local ok, err = pcall(function()
    settings.global[namespace.setting_name] = { value = active and true or false }
  end)
  return ok, err
end

-- Shadow the engine globals inside this mod's environment so every shared
-- module registers prefixed commands and writes under this runtime's root.
-- `engine_commands`/`engine_helpers` are the real engine tables; the returned
-- shadows pass every other method through via __index.
function namespace.install(engine_commands, engine_helpers)
  local wrapped_commands = setmetatable({}, { __index = engine_commands })
  function wrapped_commands.add_command(name, help, callback)
    return engine_commands.add_command(namespace.command_name(name), help, callback)
  end

  local wrapped_helpers = setmetatable({}, { __index = engine_helpers })
  function wrapped_helpers.write_file(path, data, append)
    return engine_helpers.write_file(namespace.output_path(path), data, append)
  end

  return wrapped_commands, wrapped_helpers
end

function namespace.install_remote(engine_remote, additional_methods)
  if engine_remote.interfaces[namespace.remote_interface] then
    return
  end
  local methods = {
    runtime_info = function()
      return namespace.info()
    end,
    is_active = function()
      return namespace.is_active()
    end,
  }
  for name, method in pairs(additional_methods or {}) do
    methods[name] = method
  end
  engine_remote.add_interface(namespace.remote_interface, methods)
end

return namespace
