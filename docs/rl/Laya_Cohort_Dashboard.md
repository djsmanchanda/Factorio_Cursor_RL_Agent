<!-- Path: docs/rl/Laya_Cohort_Dashboard.md | Purpose: Explain isolated cohort dashboard operation, evidence boundaries, and deployment limitations. -->

# Laya cohort operations dashboard

This is a separate loopback-only dashboard. It reads disposable cohort evidence; it does not modify campaign files, saves, mods, or Factorio. Read `docs/system_invariants.md` before any deployment. The dashboard is **not yet a complete replacement for manual operations review**; see limitations below.

## Start and access

From the implementation checkout, with the original evidence repository supplied explicitly:

```sh
python3 tools/laya_cohort_dashboard.py --repo-root /home/miniship/Github_Desktop/Factorio_Cursor_RL_Agent
```

It binds `127.0.0.1:9138`, without LAN authentication or remote exposure. On Starship use `ssh -L 9138:127.0.0.1:9138 miniship`, then open `http://127.0.0.1:9138` on Starship. The tunnel is for HTTP only; it cannot forward Factorio UDP. `--registry`, `--host-root`, `--data-root` and `--port` override paths and port. Local notes, audit and cooling state go under ignored `data/laya-cohort-dashboard/`; backups of this directory are the operator's responsibility. The example user unit at `systemd/laya-cohort-dashboard.service.example` requires editing absolute paths; it is not installed automatically.

Controls default to disabled. Do not pass `--enable-controls` until the updated `tools/laya_host_scheduler.py` has been reviewed, deployed over the host's installed scheduler, and that scheduler service restarted. The old installed controller does not share the lock and a manual target bypasses UPS checks. Check active campaigns and their deadlines before scheduler deployment/restart; do not edit frozen campaign copies or restart campaigns. Preserve the existing host config. Rollback: stop the dashboard service and restore the prior scheduler source and restart only the host scheduler when authorized; understand that the old scheduler's manual-target bypass returns. If cooling is active, cancel it before removing the dashboard code, because its timer calls this script.

## Controls

The manual ceiling is an upper bound, not a request to immediately fill slots. It can be temporary or permanent, including zero; existing worlds drain naturally. The automatic search and UPS, thermal, memory, mode and runner-instability caps still apply. Temporary ceilings nest and restore the earlier policy; `resume` clears them. A zero-to-nonzero recovery with queued work can bootstrap one unmeasured slot. Floors refer to the 5th and 2nd percentiles of sampled run UPS, respectively, representing p95/p98 coverage, not the 95th/98th numerical percentiles. Changes use the scheduler CLI and its shared file lock. Other clients must also use that lock and updated scheduler code to avoid lost writes.

Cooling requests schedule a transient user-systemd timer before selecting `powerprofilesctl` power-saver and scheduler cool mode. At expiry the helper restores Linux balanced and restores the previous scheduler mode only if its token still owns that mode. Repeating renews the deadline; stale timers do nothing. Explicit scheduler `mode` commands clear the ownership token. Cancellation restores immediately. Failures are audited; an active timer's helper retries failures through systemd. This is not a BIOS setting. Transient timers survive dashboard restart, but do not survive a user manager restart or reboot; after either, inspect `data/laya-cohort-dashboard/cooling.json` and restore manually if required. Do not remove or relocate the script while a timer is pending.

## Evidence and limitations

- Cohort status and planned counts come from registry `job-status.json`, `freeze.json`, `planned-jobs.json`, and the loop queue. A missing job is unaccounted, not a success or failure. Report paths are shown but not served as arbitrary files. Dataset snapshots and historical index are descriptive; no final ranking is inferred from partial cohorts.
- Scheduler history is bounded to the last 2 MiB and cached JSON reads are limited to 12 MiB per file. Attribution describes reported reasons, not independent causal inference; intervals over ten minutes are omitted. History includes settling, missing samples, thermal, UPS, mode, memory and instability reasons when reported, but no synthetic reason is invented. A recorded `effective_max` from an older scheduler may not include a manual ceiling.
- Run rows display manifest UDP ports and mod-list data only when available. They do not verify live process identity, listener binding, a routable address, or reachability from Starship, and do not offer a connect string. Actual spectator access requires verified process/listener and network route as well as matching Factorio and mod versions. Never use RCON credentials for viewing.
- Latency, milestone-history charts, interactive datapoint tooltips, control markers, typed-fault classification, complete version-qualified historical predicate comparisons, and export are not implemented. The existing chart selector covers slow-tail UPS, throughput, temperature, and run caps. The per-run milestone fields are displayed separately from the Laya C6 evidence version; no equivalence is implied.
- Notes are append-only user observations, not training labels or learning truth. The interface displays only the bounded 2 MiB tail of notes; older notes remain in the file. The HTTP API has Host, Origin and random-token checks; it assumes a trusted local user and must not be proxied to untrusted networks.
- Systemd timer and power-profile behavior has fixture coverage only, not a live host test. This checkout has not deployed or restarted any service. The scheduler and dashboard need review and end-to-end testing before enabling controls.
