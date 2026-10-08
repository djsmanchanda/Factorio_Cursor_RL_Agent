#!/usr/bin/env bash
# Path: scripts/factorio_runtime_config.sh
# Purpose: Refresh owned Factorio runtime paths without discarding other configuration.

write_factorio_runtime_config() {
  local config_path="$1" read_data="$2" write_data="$3"
  local temporary="$config_path.tmp.$$" input=/dev/null
  [[ ! -f "$config_path" ]] || input="$config_path"
  FACTORIO_CONFIG_READ_DATA="$read_data" FACTORIO_CONFIG_WRITE_DATA="$write_data" \
    awk '
      function paths() {
        print "read-data=" ENVIRON["FACTORIO_CONFIG_READ_DATA"]
        print "write-data=" ENVIRON["FACTORIO_CONFIG_WRITE_DATA"]
      }
      /^[[:space:]]*\[path\][[:space:]]*$/ {
        print; paths(); in_path = 1; found_path = 1; next
      }
      /^[[:space:]]*\[/ { in_path = 0 }
      in_path && /^[[:space:]]*(read-data|write-data)[[:space:]]*=/ { next }
      { print }
      END { if (!found_path) { print "[path]"; paths() } }
    ' "$input" > "$temporary"
  mv "$temporary" "$config_path"
}
