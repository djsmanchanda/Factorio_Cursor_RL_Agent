<!-- Path: docs/rl/Laya_Cohort_Dashboard.md | Purpose: Explain isolated cohort dashboard operation, evidence boundaries, and deployment limitations. -->

# Laya cohort operations dashboard

This is a separate loopback-only dashboard. It reads disposable cohort evidence; it does not modify campaign files, saves, mods, or Factorio. Read `docs/system_invariants.md` before any deployment. The dashboard is **not yet a complete replacement for manual operations review**; see limitations below.

## Start and access

From the implementation checkout, with the original evidence repository supplied explicitly:

```sh
python3 tools/laya_cohort_dashboard.py --repo-root /home/miniship/Github_Desktop/Factorio_Cursor_RL_Agent
```

It binds `127.0.0.1:9138`, without LAN authentication or remote exposure. On Starship use `ssh -L 9138:127.0.0.1:9138 miniship`, then open `http://127.0.0.1:9138` on Starship. The tunnel is for HTTP only; it cannot forward Factorio UDP. `--registry`, `--host-root`, `--data-root` and `--port` override paths and port. Local notes, audit and cooling state go under ignored `data/laya-cohort-dashboard/`; backups of this directory are the operator's responsibility. The example user unit at `systemd/laya-cohort-dashboard.service.example` requires editing absolute paths; it is not installed automatically.

Controls default to disabled. Even with `--enable-controls`, they remain locked unless a fresh scheduler heartbeat identifies a living process with matching protocol and source hash. This is a compatibility check, not a defense against a malicious local user. Do not enable until the updated scheduler has been reviewed, deployed and restarted. The old controller does not share the lock and a manual target bypasses UPS checks. Check active campaigns and deadlines before deployment/restart; do not edit frozen campaign copies or restart campaigns. Preserve the existing host config. Rollback: cancel cooling, stop the dashboard, restore the prior scheduler source and restart only the host scheduler when authorized; the old manual-target bypass returns.

## Controls

The manual ceiling is an upper bound, not a request to immediately fill slots. It can be temporary or permanent, including zero; existing worlds drain naturally. The automatic search and UPS, thermal, memory, mode and runner-instability caps still apply. Temporary ceilings nest and restore the earlier policy; `resume` clears them. A zero-to-nonzero recovery with queued work can bootstrap one unmeasured slot. Floors refer to the 5th and 2nd percentiles of sampled run UPS, respectively, representing p95/p98 coverage, not the 95th/98th numerical percentiles. Changes use the scheduler CLI and its shared file lock. Other clients must also use that lock and updated scheduler code to avoid lost writes.

Cooling requires the persistent recovery service and timer to be installed, enabled and active before it can start. Copy `systemd/laya-cohort-cool-recovery.service.example` and `systemd/laya-cohort-cool-recovery.timer.example` to `~/.config/systemd/user/` without `.example`, replace all absolute paths in the service, then run `systemctl --user daemon-reload` and `systemctl --user enable --now laya-cohort-cool-recovery.timer`. The service must use this dashboard's exact code, evidence, host and data paths. Check `systemctl --user status laya-cohort-cool-recovery.timer`. A user manager must run after reboot; if no login is guaranteed, user lingering must be enabled separately by an authorized administrator. No root or linger change is performed here.

The timer checks the persistent state every minute and retries failures, including after user-manager restart or reboot. At expiry the helper selects Linux balanced and restores the previous scheduler mode only if its token still owns it. Repeating renews the deadline. Explicit scheduler `mode` commands clear ownership. A failed power-saver request attempts immediate rollback; interrupted applications and failed rollback remain recorded for recovery. This is not a BIOS setting. Before uninstalling, cancel cooling, confirm the state is clear, then `systemctl --user disable --now laya-cohort-cool-recovery.timer` and remove the two installed units when authorized. Keep the script and paths available while recovery is pending.

## Evidence and limitations

- Cohort status and planned counts come from registry `job-status.json`, `freeze.json`, `planned-jobs.json`, and the loop queue. A missing job is unaccounted, not a success or failure. Report paths are shown but not served as arbitrary files. Dataset snapshots and historical index are descriptive; no final ranking is inferred from partial cohorts.
- Scheduler history is bounded to the last 2 MiB and cached JSON reads are limited to 12 MiB per file. Attribution describes reported reasons, not independent causal inference; intervals over ten minutes are omitted. History includes settling, missing samples, thermal, UPS, mode, memory and instability reasons when reported, but no synthetic reason is invented. A recorded `effective_max` from an older scheduler may not include a manual ceiling.
- Running rows inspect the Factorio PID, process executable and world identity, then associate IPv4 UDP socket inodes with the manifest port. This is a point-in-time local check, not a reachability test; wildcard binds have no asserted endpoint, IPv6 is unknown, and loopback cannot be joined directly from Starship. Mod names/enabled flags and frozen engine are shown where available, but exact mod versions and parity are not established. Never use RCON credentials for viewing.
- The historical chart covers only the curated C35/C36/C39/C41–C43 arms; its C5 version and eligibility are not established, and only C6 final is supplied. Latency charts, full historical comparisons, interactive host datapoint tooltips, control markers, typed-fault classification and export are not implemented. The host chart covers slow-tail UPS, throughput, temperature, and run caps. Per-run milestones are separate from Laya C6 evidence; no equivalence is implied.
- Notes are append-only user observations, not training labels or learning truth. The interface displays only the bounded 2 MiB tail of notes; older notes remain in the file. The HTTP API has Host, Origin and random-token checks; it assumes a trusted local user and must not be proxied to untrusted networks.
- Systemd timer and power-profile behavior has fixture coverage only, not a live host test. This checkout has not deployed or restarted any service. The scheduler and dashboard need review and end-to-end testing before enabling controls.
