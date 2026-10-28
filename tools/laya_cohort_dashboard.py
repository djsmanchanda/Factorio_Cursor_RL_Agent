#!/usr/bin/env python3
# Path: tools/laya_cohort_dashboard.py | Purpose: Local, isolated Laya cohort evidence and operator controls.
"""Serve cohort evidence on loopback; never operates Factorio or a campaign."""
from __future__ import annotations

import argparse
import collections
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import threading
import math
from urllib.parse import urlsplit, parse_qs
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__:
    from tools import laya_host_scheduler as scheduler
    from tools.laya_cohort_view import ViewerRelay
    from tools.laya_cohort_history import History
    from tools.laya_cohort_mod_sync import sync_to_starship
else:
    import laya_host_scheduler as scheduler
    from laya_cohort_view import ViewerRelay
    from laya_cohort_history import History
    from laya_cohort_mod_sync import sync_to_starship

ASSETS = Path(__file__).with_name("laya_cohort_dashboard")
MAX_JSON = 12 * 1024 * 1024
MAX_TAIL = 2 * 1024 * 1024
# A persistent mode choice also sets the system power profile it is named after ("auto" follows the system).
MODE_PROFILES = {"cool": "power-saver", "balanced": "balanced", "performance": "performance"}
PROFILES = frozenset(MODE_PROFILES.values())


def read_json(path, fallback=None):
    try:
        if path.stat().st_size > MAX_JSON:
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def tail_jsonl(path, limit=MAX_TAIL):
    try:
        with path.open("rb") as file:
            size = file.seek(0, os.SEEK_END)
            file.seek(max(0, size - limit))
            if size > limit:
                file.readline()
            rows = []
            for line in file:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        rows.append(value)
                except (ValueError, UnicodeError):
                    continue
            return rows
    except OSError:
        return []


def listener_evidence(world, port, proc_root=Path('/proc')):
    result = {"checked_at": time.time(), "status": "unknown", "pid": None, "bind": None, "endpoint": None}
    if type(port) is not int or not 1 <= port <= 65535:
        return result
    try:
        pid = int((world / "server/factorio.pid").read_text().strip())
        proc = proc_root / str(pid)
        executable = (proc / 'exe').resolve()
        arguments = (proc / 'cmdline').read_bytes().split(b'\0')
        if executable.name != 'factorio' or not any(str(world).encode() in arg for arg in arguments):
            result['status'] = 'process identity mismatch'
            return result
        result['pid'] = pid
        try:
            result['start_ticks'] = int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[19])
        except (OSError, ValueError, IndexError):
            result['start_ticks'] = None
        inodes = set()
        for fd in (proc / 'fd').iterdir():
            try:
                target = os.readlink(fd)
                if target.startswith('socket:[') and target.endswith(']'):
                    inodes.add(target[8:-1])
            except OSError:
                continue
        for line in (proc / 'net/udp').read_text().splitlines()[1:]:
            fields = line.split()
            address, raw_port = fields[1].split(':')
            if int(raw_port, 16) == port and fields[9] in inodes:
                bind = socket.inet_ntoa(bytes.fromhex(address)[::-1])
                result.update(status='verified IPv4 UDP listener', bind=bind)
                if bind == '127.0.0.1':
                    result['status'] = 'loopback only; remote direct join unavailable'
                else:
                    result['status'] = 'listener verified; remote route unverified'
                    if bind != '0.0.0.0':
                        result['endpoint'] = f'{bind}:{port}'
                return result
        result['status'] = 'IPv4 listener not found; IPv6 or access unknown'
    except (OSError, ValueError, IndexError):
        pass
    return result


class Dashboard:
    def __init__(self, repo, registry, host, data, enable_controls=False, viewer_bind=None, viewer_address=None):
        self.repo, self.registry, self.host, self.data = map(Path, (repo, registry, host, data))
        self.cache = {}
        self.viewer_bind, self.viewer_address = viewer_bind, viewer_address
        self.relays, self.relay_lock = {}, threading.Lock()
        self.mod_sync_lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.enable_controls = enable_controls
        self.data.mkdir(parents=True, exist_ok=True)
        self.history = History(self.host / "history.jsonl", self.data / "history.sqlite3")

    def cached(self, path, fallback=None):
        try:
            stamp = (path.stat().st_mtime_ns, path.stat().st_size)
        except OSError:
            return fallback
        if path not in self.cache or self.cache[path][0] != stamp:
            self.cache[path] = (stamp, read_json(path, fallback))
        return self.cache[path][1]

    def audit(self, action, details):
        with (self.data / "audit.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps({"time": time.time(), "action": action, "details": details}, allow_nan=False) + "\n")

    def scheduler_compatibility(self):
        report = self.cached(self.host / "target.json", {}) or {}
        controller = report.get("controller") or {}
        try:
            if time.time() - float(report["updated"]) > 180 or time.time() < float(report["updated"]) - 10:
                return False, "Scheduler heartbeat is stale"
            if controller.get("protocol") != scheduler.PROTOCOL or controller.get("source_sha256") != scheduler.SOURCE_HASH:
                return False, "Running scheduler protocol or source differs"
            pid = int(controller["pid"])
            proc = Path('/proc') / str(pid)
            stat = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            arguments = (proc / 'cmdline').read_bytes().split(b'\0')
            script = Path(controller["script"]).resolve()
            cwd = (proc / 'cwd').resolve()
            if (int(stat[19]) != controller["start_ticks"] or
                    not any((cwd / os.fsdecode(arg)).resolve() == script for arg in arguments if arg and os.fsdecode(arg).endswith("laya_host_scheduler.py")) or
                    hashlib.sha256(script.read_bytes()).hexdigest() != controller["source_sha256"]):
                return False, "Scheduler process identity does not match heartbeat"
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            return False, "Scheduler process identity unavailable"
        return True, "Verified live scheduler heartbeat and source"

    def cohorts(self):
        queue = self.cached(self.repo / "data/laya-orchestration/laya-loop/queue.json", {}) or {}
        if not isinstance(queue, dict):
            queue = {}
        queue_by_name = {row.get("name"): row for row in queue.get("cohorts", []) if isinstance(row, dict)}
        result = []
        for path in sorted(self.registry.iterdir() if self.registry.exists() else []):
            if not path.is_dir() or path.name.startswith("_"):
                continue
            status = self.cached(path / "job-status.json", {}) or {}
            batch = self.cached(path / "batch.json", {}) or {}
            freeze = self.cached(path / "freeze.json", {}) or {}
            plan = self.cached(path / "planned-jobs.json", {}) or {}
            status = status if isinstance(status, dict) else {}
            batch = batch if isinstance(batch, dict) else {}
            freeze = freeze if isinstance(freeze, dict) else {}
            plan = plan if isinstance(plan, dict) else {}
            jobs = status.get("jobs", []) if isinstance(status.get("jobs", []), list) else []
            if not jobs and not batch and not freeze and not plan:
                continue
            planned = freeze.get("planned_jobs") or len(plan.get("jobs", [])) or batch.get("planned_jobs") or queue_by_name.get(path.name, {}).get("planned_jobs")
            counts = collections.Counter(str(job.get("status", "unknown")) for job in jobs if isinstance(job, dict))
            arms = {arm.get("id"): arm for arm in freeze.get("arms", []) if isinstance(arm, dict)}
            clean = []
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                row = {key: job.get(key) for key in ("id", "arm", "seed", "status", "started", "runner_exited", "termination_cause", "runner_stop", "failures", "accepted_ever", "accepted_final", "c5_predicate_version", "c6_predicate_version", "comparison_eligible", "typed_outcomes", "outcome_classifications", "untyped_stuck", "maintenance_failure_events", "evidence_errors")}
                acceptance = job.get("laya_acceptance")
                row["laya_c6_version"] = acceptance.get("C6", {}).get("version") if isinstance(acceptance, dict) and isinstance(acceptance.get("C6"), dict) else None
                arm = arms.get(job.get("arm"), {})
                row["commit"] = arm.get("revision")
                row["world"] = job.get("world")
                row["window"] = freeze.get("until")
                relay = self.relays.get((path.name, str(job.get("id"))))
                row["viewer"] = relay.status() if relay else None
                row["game_port"] = None
                row["mods"] = None
                if job.get("status") == "running" and job.get("world"):
                    world = Path(job["world"])
                    try:
                        world.resolve().relative_to(self.registry.resolve())
                        manifest = self.cached(world / "manifest.json", {}) or {}
                        row["game_port"] = manifest.get("game_port")
                        row["listener"] = listener_evidence(world, row["game_port"])
                        row["comparison_eligible"] = manifest.get("comparison_eligible", row["comparison_eligible"])
                        mod_list = self.cached(world / "server/mods/mod-list.json", {}) or {}
                        row["mods"] = mod_list.get("mods")
                    except (ValueError, OSError):
                        pass
                clean.append(row)
            result.append({"name": path.name, "planned": planned, "counts": dict(counts), "unaccounted": max(0, planned - len(jobs)) if isinstance(planned, int) else None, "updated": status.get("updated"), "created": freeze.get("frozen_at") or batch.get("created_at"), "queue_status": queue_by_name.get(path.name, {}).get("status"), "engine": freeze.get("engine"), "jobs": clean, "source": str(path / "report.md")})
        return result

    def snapshot(self):
        target = self.cached(self.host / "target.json", {}) or {}
        config = scheduler._load_config(self.host)
        history = self.history.window(6)
        notes = tail_jsonl(self.data / "notes.jsonl")
        audit = tail_jsonl(self.data / "audit.jsonl")
        compatible, compatibility = self.scheduler_compatibility()
        curated = self.cached(self.repo / "docs/rl/laya_cohort_curated_history.json", None)
        if not curated:
            curated = self.cached(self.repo / "docs/rl/laya_helper_runs/terra-review-20261028/cohorts.json", {}) or {}
        return {"now": time.time(), "target": target, "config": config, "history": history["rows"], "history_window": history, "cohorts": self.cohorts(), "curated": curated, "notes": notes, "audit": audit, "cooling": read_json(self.data / "cooling.json", {}) or {}, "controls_enabled": self.enable_controls and compatible, "scheduler_compatibility": compatibility, "viewer_available": bool(self.viewer_bind and self.viewer_address)}

    def selected_job(self, cohort, run):
        if not isinstance(cohort, str) or Path(cohort).name != cohort or cohort.startswith("_"):
            raise ValueError("Invalid cohort")
        path = self.registry / cohort / "job-status.json"
        path.resolve().relative_to(self.registry.resolve())
        data = self.cached(path, {})
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        job = next((row for row in jobs if row.get("id") == run), None)
        if job is None:
            raise ValueError("Unknown run")
        return job

    def selected_world(self, job):
        if not isinstance(job.get("world"), str):
            raise ValueError("Run has no world artifact")
        world = Path(job["world"]).resolve()
        world.relative_to(self.registry.resolve())
        return world

    def run_details(self, cohort, run):
        job = self.selected_job(cohort, run)
        world = self.selected_world(job)
        rows = tail_jsonl(world / "evidence/decision-states.jsonl")
        timing = []
        previous = None
        for row in rows:
            values = (row.get("outcome") or {}).get("timing") or {}
            item = {"index": row.get("index"), "tick": row.get("tick"),
                    "policy_seconds": values.get("policy_wall_seconds"),
                    "executor_seconds": values.get("operation_wall_seconds"),
                    "model_client_seconds": values.get("model_client_seconds")}
            recorded = row.get("recorded_at")
            if isinstance(recorded, str):
                try:
                    recorded = datetime.fromisoformat(recorded).timestamp()
                except ValueError:
                    recorded = None
            # Decision cadence includes observation, operation and waits. It is
            # useful old evidence, but cannot be relabeled inference latency.
            item["decision_interval_seconds"] = (recorded - previous if type(recorded) in (int, float)
                and previous is not None and recorded >= previous else None)
            previous = recorded if type(recorded) in (int, float) else None
            timing.append(item)
        versions = self.cached(world / "evidence/latest-status.json", {})
        return {"timing": timing, "timing_scope": "bounded journal tail", "latest": versions,
                "notes": self.notes_page(cohort, run), "mod_hashes": self.cached(world / "manifest.json", {}).get("mod_hashes")}

    def notes_page(self, cohort, run, cursor=None):
        self.selected_job(cohort, run)
        path = self.data / "notes.jsonl"
        try:
            with path.open("rb") as file:
                size = file.seek(0, os.SEEK_END)
                end = size if cursor is None else int(cursor)
                if not 0 <= end <= size:
                    raise ValueError("Invalid notes cursor")
                start = max(0, end - MAX_TAIL)
                file.seek(start)
                if start:
                    file.readline()
                boundary = file.tell()
                content = file.read(end - boundary)
            matches = []
            for line in content.splitlines():
                try:
                    row = json.loads(line)
                    if row.get("cohort") == cohort and row.get("run") == run:
                        matches.append(row)
                except (ValueError, AttributeError):
                    continue
            return {"rows": matches, "next_cursor": boundary if start else None}
        except FileNotFoundError:
            return {"rows": [], "next_cursor": None}

    def sync_mods(self, body):
        cohort, run = body.get("cohort"), body.get("run")
        with self.mod_sync_lock:
            job = self.selected_job(cohort, run)
            if job.get("status") != "running":
                raise ValueError("Select a running server to sync its mods")
            world = self.selected_world(job)
            manifest = self.cached(world / "manifest.json", {})
            if listener_evidence(world, manifest.get("game_port")).get("pid") is None:
                raise ValueError("Running Factorio identity unavailable")
            expected = manifest.get("mod_hashes")
            if not isinstance(expected, dict) or not expected:
                raise ValueError("Frozen mod hashes unavailable")
            receipt = sync_to_starship(world / "server/mods", expected, self.data)
            self.audit("sync_mods", {"cohort": cohort, "run": run, **receipt})
            return {"message": "Mods synced. Restart Factorio on Starship before joining.", **receipt}

    def view(self, body):
        if not self.viewer_bind or not self.viewer_address:
            raise ValueError("Viewer bind and allowed Starship address are not configured")
        cohort, run = body.get("cohort"), body.get("run")
        job = self.selected_job(cohort, run)
        world = self.selected_world(job)
        manifest = self.cached(world / "manifest.json", {})
        port = manifest.get("game_port")
        evidence = listener_evidence(world, port)
        if job.get("status") != "running" or evidence.get("bind") != "127.0.0.1" or evidence.get("start_ticks") is None:
            raise ValueError("Viewer relay requires a running verified loopback UDP server")
        pid = evidence["pid"]
        def valid():
            current = self.selected_job(cohort, run)
            listener = listener_evidence(world, port)
            return current.get("status") == "running" and listener.get("pid") == pid and listener.get("bind") == "127.0.0.1" and listener.get("start_ticks") == evidence["start_ticks"]
        with self.relay_lock:
            key = (cohort, run)
            relay = self.relays.get(key)
            if body.get("stop"):
                if relay:
                    relay.close()
                    del self.relays[key]
            elif relay and not relay.stopped.is_set():
                relay.deadline = time.time() + 3600
            else:
                self.relays[key] = ViewerRelay(self.viewer_bind, self.viewer_address, ("127.0.0.1", port), valid)
        self.audit("viewer", {"cohort": cohort, "run": run, "stop": bool(body.get("stop"))})

    def note(self, body):
        cohort, run, text, tags = (body.get(key) for key in ("cohort", "run", "text", "tags"))
        if not all(isinstance(value, str) for value in (cohort, run, text, tags)) or not text.strip() or len(text) > 10000 or len(tags) > 500 or len(cohort) > 300 or len(run) > 300:
            raise ValueError("Invalid note")
        row = {"time": time.time(), "cohort": cohort, "run": run, "text": text, "tags": tags, "kind": "user_observation"}
        with scheduler.control_lock(self.host):
            with (self.data / "notes.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.audit("note", {"cohort": cohort, "run": run})

    def control(self, body):
        if not self.enable_controls:
            raise ValueError("Controls disabled until the updated scheduler has been deployed and restarted")
        compatible, message = self.scheduler_compatibility()
        if not compatible:
            raise ValueError(message)
        action = body.get("action")
        if action in {"cool", "cool_cancel"}:
            return self.cool(action == "cool")
        if action == "mode":
            return self.set_mode(body.get("mode"), body.get("system_profile", True))
        if action == "bump":
            # A relative, one-off nudge: the automatic search restarts from the raised target and every safeguard still applies.
            value = body.get("delta")
            if type(value) is not int or not 1 <= value <= 100:
                raise ValueError("Raise must be a whole number of runs between 1 and 100")
            args = ["bump", f"+{value}"]
            had_ceiling = scheduler._load_config(self.host).get("manual_target") is not None
        elif action == "ceiling":
            value = body.get("slots")
            if type(value) is not int or not 0 <= value <= 1000:
                raise ValueError("Ceiling must be an integer between 0 and 1000")
            duration = body.get("duration", "")
            if not isinstance(duration, str) or (duration and not scheduler.re.fullmatch(r"[1-9][0-9]{0,4}[smhd]", duration)):
                raise ValueError("Duration must be empty or like 30m")
            args = ["target", str(value)] + (["--for", duration] if duration else [])
        elif action == "resume":
            args = ["resume"]
        elif action == "configure":
            args = ["configure"]
            for key, flag in (("p95", "--min-ups-p95"), ("p98", "--min-ups-p98"), ("temp", "--temp-limit")):
                value = body.get(key)
                if type(value) not in (int, float) or not scheduler.math.isfinite(value):
                    raise ValueError("Floors and temperature must be finite numbers")
                args.extend((flag, str(value)))
                if key == "temp":
                    args.extend(("--cool-temp", str(min(value, scheduler._load_config(self.host)["cool_temp_limit_c"]))))
        else:
            raise ValueError("Unknown control")
        result = subprocess.run([sys.executable, str(Path(scheduler.__file__).resolve()), "--root", str(self.host), "--registry", str(self.registry), *args], capture_output=True, text=True, timeout=20)
        with scheduler.control_lock(self.host):
            self.audit(action, {"request": body, "ok": result.returncode == 0, "error": result.stderr[-500:]})
        if result.returncode:
            raise ValueError(result.stderr[-500:] or "Control failed")
        if action == "bump":
            try:
                moved = json.loads(result.stdout)
                previous, target = int(moved["previous"]), int(moved["target"])
            except (ValueError, KeyError, TypeError):
                return {"message": "Admission target raised."}
            cleared = " The manual ceiling was cleared." if had_ceiling else ""
            if target <= previous:
                return {"message": f"Admission target is already at the scheduler maximum ({previous}).{cleared}"}
            capped = " (the scheduler maximum)" if target < previous + value else ""
            return {"message": f"Admission target raised from {previous} to {target}{capped}; the next scheduler cycle applies it.{cleared}"}

    def _system_profile(self):
        try:
            result = subprocess.run(["powerprofilesctl", "get"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return "balanced"
        profile = result.stdout.strip()
        return profile if result.returncode == 0 and profile in PROFILES else "balanced"

    def set_mode(self, mode, system_profile=True):
        """Persistently choose the scheduler mode; unlike cool() there is no expiry or restore."""
        if mode not in ("auto", *MODE_PROFILES) or type(system_profile) is not bool:
            raise ValueError("Mode must be auto, cool, balanced or performance")
        profile = MODE_PROFILES.get(mode) if system_profile else None
        with scheduler.control_lock(self.host):
            state = read_json(self.data / "cooling.json", {}) or {}
            previous = scheduler._load_config(self.host).get("mode", "auto")
            if profile:
                try:
                    result = subprocess.run(["powerprofilesctl", "set", profile], capture_output=True, text=True, timeout=20)
                    if result.returncode:
                        raise ValueError(result.stderr[-200:].strip() or "command failed")
                except (OSError, subprocess.SubprocessError, ValueError) as error:
                    self.audit("mode_failed", {"mode": mode, "error": str(error)[:500]})
                    raise ValueError(f"Could not set the {profile} power profile ({str(error)[:200]}); the mode was not changed") from error
            scheduler._set_config(self.host, mode=mode, dashboard_cool_token=None)
            # An active one-hour cooling request would otherwise restore a stale profile and mode when it expires.
            ended = bool(state.get("token"))
            if ended:
                scheduler._atomic(self.data / "cooling.json", {})
            self.audit("mode", {"mode": mode, "previous_mode": previous, "profile": profile, "ended_cooling": ended})
        note = "follows the system power profile" if mode == "auto" else f"system power profile set to {profile}" if profile else "system power profile left unchanged"
        return {"message": f"Mode set to {mode}: {note}." + (" The 1-hour cooling request was ended." if ended else "")}

    def cool(self, enable):
        with scheduler.control_lock(self.host):
            state = read_json(self.data / "cooling.json", {}) or {}
            if not enable:
                return self._restore_locked(state.get("token"), force=True)
            config = scheduler._load_config(self.host)
            renewing = bool(state.get("token") and config.get("dashboard_cool_token") == state.get("token"))
            previous = state.get("previous_mode") if renewing else config.get("mode", "auto")
            # Remember the profile too: a persistent performance or power-saver choice must survive an hour of cooling.
            previous_profile = state.get("previous_profile") if renewing else self._system_profile()
            token = secrets.token_hex(12)
            self._check_recovery_timer()
            scheduler._atomic(self.data / "cooling.json", {"token": token, "until": time.time() + 3600, "previous_mode": previous, "previous_profile": previous_profile, "phase": "applying"})
            scheduler._set_config(self.host, mode="cool", dashboard_cool_token=token)
            try:
                result = subprocess.run(["powerprofilesctl", "set", "power-saver"], capture_output=True, text=True, timeout=20)
                if result.returncode:
                    raise ValueError(result.stderr[-500:] or "power-saver failed")
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                self.audit("cool_apply_failed", {"error": str(error)[:500], "recovery": "attempting rollback"})
                try:
                    self._restore_locked(token, force=True)
                except (OSError, subprocess.SubprocessError, ValueError) as restore_error:
                    self.audit("cool_rollback_pending", {"error": str(restore_error)[:500]})
                raise ValueError("Could not set power-saver; recovery attempted or pending") from error
            scheduler._atomic(self.data / "cooling.json", {"token": token, "until": time.time() + 3600, "previous_mode": previous, "previous_profile": previous_profile, "phase": "active"})
            self.audit("cool", {"until": time.time() + 3600, "previous_mode": previous})

    def _check_recovery_timer(self):
        for command in (["systemctl", "--user", "is-active", "--quiet", "laya-cohort-cool-recovery.timer"],
                        ["systemctl", "--user", "is-enabled", "--quiet", "laya-cohort-cool-recovery.timer"]):
            if subprocess.run(command, capture_output=True, text=True, timeout=20).returncode:
                raise ValueError("Persistent cooling recovery timer must be enabled and active")
        result = subprocess.run(["systemctl", "--user", "show", "laya-cohort-cool-recovery.service", "--property=ExecStart", "--value"], capture_output=True, text=True, timeout=20)
        required = (str(Path(__file__).resolve()), "--recover-cooling", str(self.repo), str(self.host), str(self.data))
        if result.returncode or not all(value in result.stdout for value in required):
            raise ValueError("Cooling recovery service does not match this dashboard's paths")

    def _restore_locked(self, token, force=False):
        state = read_json(self.data / "cooling.json", {}) or {}
        if not token or state.get("token") != token:
            return
        if not force and time.time() < state.get("until", 0):
            raise ValueError("Cooling timer fired before its deadline")
        config = scheduler._load_config(self.host)
        if config.get("dashboard_cool_token") != token:
            # A later explicit mode owns both the policy and profile now.
            scheduler._atomic(self.data / "cooling.json", {})
            self.audit("cool_superseded", {"token": token})
            return
        # State files written before previous_profile existed restore balanced, as they always did.
        profile = state.get("previous_profile") if state.get("previous_profile") in PROFILES else "balanced"
        try:
            result = subprocess.run(["powerprofilesctl", "set", profile], capture_output=True, text=True, timeout=20)
            if result.returncode:
                raise ValueError(result.stderr[-500:] or "power profile command failed")
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            self.audit("cool_restore_failed", {"error": str(error)[:500]})
            raise ValueError(f"Could not restore the {profile} profile; retry remains pending") from error
        config = scheduler._load_config(self.host)
        restored_mode = None
        if config.get("dashboard_cool_token") == token:
            restored_mode = state["previous_mode"]
            scheduler._set_config(self.host, mode=restored_mode, dashboard_cool_token=None)
        scheduler._atomic(self.data / "cooling.json", {})
        self.audit("cool_cancel" if force else "cool_restore", {"restored_mode": restored_mode})

    def restore(self, token):
        with scheduler.control_lock(self.host):
            self._restore_locked(token)

    def recover(self):
        with scheduler.control_lock(self.host):
            state = read_json(self.data / "cooling.json", {}) or {}
            if state.get("token") and (state.get("phase") != "active" or time.time() >= state.get("until", 0)):
                self._restore_locked(state["token"], force=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "LayaCohorts/1"

    def safe_host(self):
        host = self.headers.get("Host", "")
        return host in {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"}

    def send(self, code, data, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.safe_host():
            return self.send(403, b"Forbidden", "text/plain")
        if self.path == "/api/snapshot":
            data = self.server.dashboard.snapshot()
            data["token"] = self.server.dashboard.token
            return self.send(200, json.dumps(data, allow_nan=False).encode(), "application/json")
        parsed = urlsplit(self.path)
        if parsed.path == "/api/history":
            try:
                hours = int(parse_qs(parsed.query).get("hours", ["6"])[0])
                data = self.server.dashboard.history.window(hours)
                return self.send(200, json.dumps(data, allow_nan=False).encode(), "application/json")
            except ValueError as error:
                return self.send(400, json.dumps({"error": str(error)}).encode(), "application/json")
        if parsed.path == "/api/report":
            try:
                relative = parse_qs(parsed.query).get("path", [""])[0]
                path = (self.server.dashboard.repo / relative).resolve()
                path.relative_to(self.server.dashboard.repo.resolve())
                curated = self.server.dashboard.cached(self.server.dashboard.repo / "docs/rl/laya_cohort_curated_history.json", {}) or {}
                sources = {source for row in curated.get("cohorts", []) for source in row.get("sources", [])}
                if not relative.startswith("docs/rl/") and relative not in sources:
                    raise ValueError("Report is not part of the curated review")
                if path.suffix not in {".md", ".json"} or path.stat().st_size > MAX_JSON:
                    raise ValueError("Unsupported report")
                return self.send(200, path.read_bytes(), "text/plain; charset=utf-8")
            except (OSError, ValueError):
                return self.send(404, b"Report unavailable", "text/plain")
        if parsed.path in {"/api/run", "/api/notes"}:
            query = parse_qs(parsed.query)
            try:
                cohort, run = query.get("cohort", [""])[0], query.get("run", [""])[0]
                data = (self.server.dashboard.run_details(cohort, run) if parsed.path == "/api/run" else
                        self.server.dashboard.notes_page(cohort, run, query.get("cursor", [None])[0]))
                return self.send(200, json.dumps(data, allow_nan=False).encode(), "application/json")
            except (ValueError, OSError, TypeError) as error:
                return self.send(400, json.dumps({"error": str(error)}).encode(), "application/json")
        files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.path in files:
            name, content_type = files[self.path]
            return self.send(200, (ASSETS / name).read_bytes(), content_type)
        self.send(404, b"Not found", "text/plain")

    def do_POST(self):
        origin = self.headers.get("Origin", "")
        if not self.safe_host() or origin != f"http://{self.headers.get('Host')}" or not hmac.compare_digest(self.headers.get("X-Laya-Token", ""), self.server.dashboard.token):
            return self.send(403, b"Forbidden", "text/plain")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16384 or self.headers.get_content_type() != "application/json":
                raise ValueError("Invalid request")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Invalid request")
            result = {}
            if self.path == "/api/note":
                self.server.dashboard.note(body)
            elif self.path == "/api/sync-mods":
                result = self.server.dashboard.sync_mods(body)
            elif self.path == "/api/view":
                self.server.dashboard.view(body)
            elif self.path == "/api/control":
                result = self.server.dashboard.control(body) or {}
            else:
                return self.send(404, b"Not found", "text/plain")
            self.send(200, json.dumps({"ok": True, **result}).encode(), "application/json")
        except (ValueError, TypeError, OSError, subprocess.SubprocessError) as error:
            if self.path == "/api/control":
                with scheduler.control_lock(self.server.dashboard.host):
                    self.server.dashboard.audit("control_rejected", {"error": str(error)[:500]})
            self.send(400, json.dumps({"error": str(error)}).encode(), "application/json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--host-root", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--port", type=int, default=9138)
    parser.add_argument("--enable-controls", action="store_true", help="enable only after deploying and restarting the updated scheduler")
    parser.add_argument("--viewer-bind", help="Dedicated private IPv4 address for optional UDP viewing relays")
    parser.add_argument("--viewer-address", help="Only this Starship IPv4 address can send viewer packets")
    parser.add_argument("--restore-cooling", help=argparse.SUPPRESS)
    parser.add_argument("--recover-cooling", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    dashboard = Dashboard(repo, args.registry or repo / "data/laya-experiments", args.host_root or repo / "data/laya-host", args.data_root or repo / "data/laya-cohort-dashboard", args.enable_controls, args.viewer_bind, args.viewer_address)
    if args.restore_cooling:
        dashboard.restore(args.restore_cooling)
        return
    if args.recover_cooling:
        dashboard.recover()
        return
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.dashboard = dashboard
    print(f"Laya cohort dashboard: http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
