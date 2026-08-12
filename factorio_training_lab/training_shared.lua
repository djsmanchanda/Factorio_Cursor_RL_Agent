-- Path: factorio_training_lab/training_shared.lua
-- Purpose: Own training-lab storage, identity checks, and collision-free reports.

local VERSION = "1.1.0"
local OWNER = "factorio_training_lab"

local function ensure_storage()
  storage.training_lab = storage.training_lab or {
    version = VERSION,
    report_sequence = 0,
    episodes = {},
    pending_force_merges = {},
    orphan_surfaces = {},
    uploads = {}
  }
  storage.training_lab.version = VERSION
  storage.training_lab.orphan_surfaces = storage.training_lab.orphan_surfaces or {}
  storage.training_lab.uploads = storage.training_lab.uploads or {}
  return storage.training_lab
end

local function assert_identifier(value, label)
  if type(value) ~= "string" or value == "" or #value > 128 then
    error(label .. " must be a nonempty string of at most 128 characters")
  end
  return value
end

local function parse_command(command, require_episode)
  if command.player_index ~= nil then
    error("training commands are RCON-only")
  end
  local ok, payload = pcall(function()
    return helpers.json_to_table(command.parameter or "")
  end)
  if not ok or type(payload) ~= "table" then
    error("invalid JSON payload")
  end
  if payload.version ~= VERSION then
    error("command version must be " .. VERSION)
  end
  assert_identifier(payload.request_id, "request_id")
  if require_episode ~= false then assert_identifier(payload.episode_id, "episode_id") end
  return payload
end

local function write_report(kind, report)
  local state = ensure_storage()
  state.report_sequence = state.report_sequence + 1
  report.version = VERSION
  report.kind = kind
  report.tick = game.tick
  local suffix = string.format("%010d_%06d", game.tick, state.report_sequence)
  local path = "factorio_training_lab/reports/" .. kind .. "_" .. suffix .. ".json"
  helpers.write_file(path, helpers.table_to_json(report), false)
  return path
end

local function episode_for(episode_id)
  local episode = ensure_storage().episodes[episode_id]
  if not episode or episode.owner ~= OWNER then
    error("unknown or unowned training episode: " .. tostring(episode_id))
  end
  return episode
end

local function clear_episode_uploads(episode_id)
  local uploads = ensure_storage().uploads
  for upload_id, upload in pairs(uploads) do
    if upload.episode_id == episode_id then uploads[upload_id] = nil end
  end
end

return {
  VERSION = VERSION,
  OWNER = OWNER,
  ensure_storage = ensure_storage,
  parse_command = parse_command,
  write_report = write_report,
  episode_for = episode_for,
  clear_episode_uploads = clear_episode_uploads
}
