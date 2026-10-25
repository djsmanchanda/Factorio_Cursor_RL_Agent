#!/usr/bin/env bash
# Path: scripts/factorio_runtime_config.sh
# Purpose: Refresh owned Factorio runtime paths without discarding other configuration.

write_factorio_runtime_config() {
  local config_path="$1" read_data="$2" write_data="$3" update_threads="${4:-}"
  local temporary="$config_path.tmp.$$" input=/dev/null
  [[ ! -f "$config_path" ]] || input="$config_path"
  FACTORIO_CONFIG_READ_DATA="$read_data" FACTORIO_CONFIG_WRITE_DATA="$write_data" \
    FACTORIO_CONFIG_UPDATE_THREADS="$update_threads" \
    awk '
      function paths() {
        print "read-data=" ENVIRON["FACTORIO_CONFIG_READ_DATA"]
        print "write-data=" ENVIRON["FACTORIO_CONFIG_WRITE_DATA"]
      }
      /^[[:space:]]*\[path\][[:space:]]*$/ {
        print; paths(); in_path = 1; found_path = 1; next
      }
      /^[[:space:]]*\[other\][[:space:]]*$/ {
        print; in_path = 0; in_other = 1; found_other = 1
        if (ENVIRON["FACTORIO_CONFIG_UPDATE_THREADS"] != "")
          print "update-runner-threads-count=" ENVIRON["FACTORIO_CONFIG_UPDATE_THREADS"]
        next
      }
      /^[[:space:]]*\[/ { in_path = 0; in_other = 0 }
      in_path && /^[[:space:]]*(read-data|write-data)[[:space:]]*=/ { next }
      in_other && ENVIRON["FACTORIO_CONFIG_UPDATE_THREADS"] != "" && /^[[:space:]]*update-runner-threads-count[[:space:]]*=/ { next }
      { print }
      END {
        if (!found_path) { print "[path]"; paths() }
        if (!found_other && ENVIRON["FACTORIO_CONFIG_UPDATE_THREADS"] != "") {
          print "[other]"
          print "update-runner-threads-count=" ENVIRON["FACTORIO_CONFIG_UPDATE_THREADS"]
        }
      }
    ' "$input" > "$temporary"
  mv "$temporary" "$config_path"
}
