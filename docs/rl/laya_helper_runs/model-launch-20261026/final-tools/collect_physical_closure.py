# Path: data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/collect_physical_closure.py | Purpose: Collect pinned, read-only physical observations before parent final accounting.
"""No lifecycle actions, journal certification, or final accounting. See physical-closure-notes.md."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys


# Loading the pure sibling helpers must not write into the existing adapter directory.
sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location("physical_closure_adapter", Path(__file__).with_name("final_accounting.py"))
adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(adapter)
GuardError = adapter.GuardError
require = adapter.require
CGROOT = "/sys/fs/cgroup"
FIELDS = ("Id", "LoadState", "ActiveState", "SubState", "MainPID", "ControlPID",
          "ControlGroup", "InvocationID", "Result", "ExecMainCode", "ExecMainStatus")
CLOSURE_FIELDS = ("ActiveState", "MainPID", "Result", "ExecMainStatus")


def now():
    return datetime.now(timezone.utc).isoformat()


def absolute(path):
    p = Path(path)
    require(p.is_absolute() and ".." not in p.parts, "explicit absolute path required")
    return p


def json_object(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def nonfinite(value):
        raise GuardError("non-finite JSON value")
    data = json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
    require(isinstance(data, dict), "JSON input must be an object")
    return data


def pinned_json(path, expected):
    """Pin exact bytes and detect ordinary concurrent input replacement/writes."""
    p = absolute(path).resolve(strict=True)
    require(adapter.is_hash(expected), "explicit lowercase SHA256 required")
    with p.open("rb") as f:
        before = os.fstat(f.fileno())
        raw = f.read()
        after = os.fstat(f.fileno())
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(stat.S_ISREG(before.st_mode) and signature(before) == signature(after) == signature(p.stat()),
            "input changed during read")
    require(hashlib.sha256(raw).hexdigest() == expected, "input hash mismatch")
    data = json_object(raw)
    return data, {"path": str(p), "sha256": expected, "bytes": len(raw)}


class ObservationError(RuntimeError):
    pass


class Host:
    """The only fresh-query seam. Tests replace this object; no shell or environment reads."""

    uid = os.getuid()
    self_pid = os.getpid()

    def read(self, path):
        return Path(path).read_bytes()

    def names(self, path):
        return sorted(os.listdir(path))

    def lstat(self, path):
        return os.lstat(path)

    def link(self, path):
        return os.readlink(path)

    def canonical(self, path):
        return str(Path(path).resolve(strict=True))

    def command(self, argv):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)
        if result.returncode:
            # stderr can contain unrelated data; retain the failure code, never raw stderr.
            raise ObservationError(f"query_exit_{result.returncode}")
        return result.stdout

    def unit(self, name):
        raw = self.command(["systemctl", "--user", "show", name, "--no-pager",
                            *[f"--property={k}" for k in FIELDS]])
        rows = [line.split("=", 1) for line in raw.splitlines()]
        if any(len(r) != 2 or r[0] not in FIELDS for r in rows) or len({r[0] for r in rows}) != len(rows):
            raise ObservationError("invalid_systemctl_response")
        return dict(rows)

    def listeners(self, ports):
        raw = self.command(["ss", "--no-header", "--listening", "--numeric", "--tcp", "--udp"])
        scoped = []
        for line in raw.splitlines():
            fields = line.split()
            if len(fields) != 6 or fields[0] not in {"tcp", "udp"}:
                raise ObservationError("invalid_ss_response")
            endpoint = fields[4]
            try:
                port = int(endpoint.rsplit(":", 1)[1])
            except (ValueError, IndexError):
                raise ObservationError("invalid_ss_endpoint") from None
            if port in ports:
                scoped.append({"protocol": fields[0], "state": fields[1], "local": endpoint, "port": port,
                               "ownership": "unknown; scoped listener blocks release"})
        return scoped


def error(exc):
    return str(exc) if isinstance(exc, (ObservationError, GuardError)) else type(exc).__name__


def query_unit(host, name):
    result = {"started_utc": now(), "query_ok": False}
    try:
        data = host.unit(name)
        require(set(FIELDS) <= set(data) and data["Id"] == name and data["LoadState"] in {"loaded", "not-found"},
                "incomplete/unloaded/mismatched unit")
        require(all(isinstance(data[k], str) for k in FIELDS) and data["Result"] and
                data["ExecMainStatus"] and data["MainPID"].isdigit() and data["ControlPID"].isdigit(),
                "invalid unit fields")
        # systemd garbage-collects completed transient units. Absence is only a
        # query fact; the collector still requires pinned scopes and empty resources.
        if data["LoadState"] == "not-found":
            require(data["ActiveState"] == "inactive" and data["SubState"] == "dead" and
                    data["MainPID"] == data["ControlPID"] == "0" and
                    data["ControlGroup"] == data["InvocationID"] == "",
                    "inconsistent absent transient unit")
        result.update(data, query_ok=True)
    except (OSError, ValueError, ObservationError, subprocess.SubprocessError) as exc:
        result["error"] = error(exc)
    result["ended_utc"] = now()
    return result


def inactive(unit):
    return unit.get("query_ok") is True and unit.get("ActiveState") in {"inactive", "failed"} and all(
        unit.get(k) == "0" for k in ("MainPID", "ControlPID"))


def owned_cgroup(path, name, uid):
    p = PurePosixPath(path)
    prefix = PurePosixPath(f"/user.slice/user-{uid}.slice/user@{uid}.service")
    return (str(p) == path and ".." not in p.parts and p.is_relative_to(prefix) and
            name in p.relative_to(prefix).parts)


def cgroup_tree(host, scope, can_be_absent):
    result = {"scope": scope, "started_utc": now(), "state": "unknown", "empty": None, "nodes": []}
    root = CGROOT + scope
    try:
        try:
            host.lstat(root)
        except FileNotFoundError:
            if can_be_absent:
                result.update(state="explicitly_absent", empty=True)
                return result
            raise ObservationError("absent_scope_without_inactive_unit")

        def walk(path):
            require(stat.S_ISDIR(host.lstat(path).st_mode), "non-directory/symlink cgroup")
            events = host.read(path + "/cgroup.events").decode()
            procs = host.read(path + "/cgroup.procs").decode()
            pairs = [line.split() for line in events.splitlines()]
            require(all(len(p) == 2 for p in pairs) and len({p[0] for p in pairs}) == len(pairs),
                    "invalid cgroup.events")
            parsed = dict(pairs)
            require(parsed.get("populated") in {"0", "1"}, "missing populated event")
            require(all(p.isdigit() and int(p) > 0 for p in procs.split()), "invalid cgroup.procs")
            children = []
            for name in host.names(path):
                kind = host.lstat(path + "/" + name).st_mode
                require(not stat.S_ISLNK(kind), "symlink in cgroup tree")
                if stat.S_ISDIR(kind):
                    children.append(name)
            result["nodes"].append({"scope": path[len(CGROOT):], "events": parsed,
                                    "pids": sorted(set(map(int, procs.split())))})
            for child in children:
                walk(path + "/" + child)
            after_children = [name for name in host.names(path)
                              if stat.S_ISDIR(host.lstat(path + "/" + name).st_mode)]
            require(children == after_children and events == host.read(path + "/cgroup.events").decode() and
                    procs == host.read(path + "/cgroup.procs").decode(), "cgroup changed during traversal")

        walk(root)
        result.update(state="observed", empty=not any(n["pids"] or n["events"]["populated"] == "1"
                                                     for n in result["nodes"]))
    except (OSError, ValueError, UnicodeError, ObservationError) as exc:
        result["error"] = error(exc)
    finally:
        result["ended_utc"] = now()
    return result


def path_matches(token, roots):
    """Only complete argv path values or their path-component descendants count."""
    if token.startswith("--") and "=" in token:
        token = token.split("=", 1)[1]
    return sorted(p for p in roots if token == p or token.startswith(p + "/"))


def pid_stat(raw):
    # comm can contain spaces and ')'; starttime is field 22 after the final ')'.
    fields = raw.decode().rsplit(")", 1)[1].split()
    ticks = fields[19]
    require(ticks.isdigit(), "invalid process starttime")
    return ticks


def process_scan(host, roots, claims, boot_id):
    result = {"started_utc": now(), "query_ok": False, "empty": None, "matched": [], "claims": [], "errors": []}
    cache = {}

    def inspect(pid):
        if pid in cache:
            return cache[pid]
        base = f"/proc/{pid}"
        try:
            first = host.read(base + "/stat")
        except FileNotFoundError:
            cache[pid] = None
            return None
        start = pid_stat(first)
        status = host.read(base + "/status").decode()
        uid = next(line.split()[1] for line in status.splitlines() if line.startswith("Uid:"))
        matches = []
        if int(uid) == host.uid and pid != host.self_pid:
            argv = host.read(base + "/cmdline").decode().split("\0")
            matches = sorted({p for token in argv for p in path_matches(token, roots)})
            # Descendants may no longer carry a path in argv, but can retain an owned cwd.
            cwd = host.link(base + "/cwd")
            matches = sorted(set(matches) | set(path_matches(cwd, roots)))
        require(pid_stat(host.read(base + "/stat")) == start, "PID changed during inspection")
        info = {"pid": pid, "start_ticks": start, "boot_id": boot_id, "matched_paths": matches}
        cache[pid] = info
        return info

    try:
        for value in host.names("/proc"):
            if not value.isdigit() or int(value) == host.self_pid:
                continue
            try:
                info = inspect(int(value))
                if info and info["matched_paths"]:
                    result["matched"].append(info)
            except (OSError, ValueError, UnicodeError, IndexError, StopIteration) as exc:
                result["errors"].append({"pid": int(value), "error": error(exc)})
        for claim in claims:
            entry = dict(claim)
            try:
                info = inspect(claim["pid"])
                if info is None:
                    entry["state"] = "absent"
                elif info["matched_paths"]:
                    entry.update(state="scoped_process_present", observed=info)
                elif claim.get("start_ticks") and claim.get("boot_id"):
                    same = claim["boot_id"] == boot_id and str(claim["start_ticks"]) == info["start_ticks"]
                    entry.update(state="pinned_identity_present" if same else "reused_foreign_pid", observed=info)
                else:
                    entry.update(state="ambiguous_pid_identity", observed=info)
            except (OSError, ValueError, UnicodeError, IndexError, StopIteration) as exc:
                entry.update(state="unknown", error=error(exc))
            result["claims"].append(entry)
        ambiguous = any(c["state"] in {"unknown", "ambiguous_pid_identity"} for c in result["claims"])
        present = bool(result["matched"]) or any(c["state"] in {"scoped_process_present", "pinned_identity_present"}
                                               for c in result["claims"])
        result["query_ok"] = not result["errors"] and not ambiguous
        if result["query_ok"]:
            result["empty"] = not present
    except (OSError, ValueError, ObservationError) as exc:
        result["errors"].append({"error": error(exc)})
    result["ended_utc"] = now()
    return result


def validate_output(output, inputs, protected):
    raw = absolute(output)
    require(not os.path.lexists(raw), "output must not exist (including symlinks)")
    output = raw.resolve()
    require(output.parent.is_dir(), "output parent must already exist")
    require(not any(output.is_relative_to(absolute(p).resolve()) for p in protected),
            "output inside registry/world/evidence input")
    require(not any(absolute(p).resolve().is_relative_to(output) for p in inputs), "output contains an input")
    return output


def all_rows(registries):
    for registry, data in registries.items():
        for row in data.get("jobs", []):
            yield registry, row
            for old in row.get("previous_attempts", []):
                yield registry, old


def collect(snapshot, transfer_plan, registry_snapshots, output, *, historical_health=(), scope=None, host=None):
    """Arguments are (absolute path, expected hash); registry map keys are absolute owned paths."""
    started = now()
    health, health_ref = pinned_json(*snapshot)
    plan, plan_ref = pinned_json(*transfer_plan)
    registries, refs = {}, {}
    require(len(registry_snapshots) == 5, "five copied registry snapshots required")
    for registry, ref in registry_snapshots.items():
        absolute(registry)
        registries[registry], refs[registry] = pinned_json(*ref)
        require(refs[registry]["sha256"] == health.get("registry_metadata", {}).get(registry, {}).get("sha256"),
                "registry hash differs from health snapshot")
        require(not Path(refs[registry]["path"]).is_relative_to(absolute(registry).resolve()),
                "registry snapshot must be a copied immutable file")
    histories, history_refs = [], []
    for ref in historical_health:
        data, receipt = pinned_json(*ref)
        require(data.get("version") == "laya-model-cohort-host-health-v1", "wrong historical health version")
        histories.append(data)
        history_refs.append(receipt)
    explicit, scope_ref = {}, None
    if scope:
        explicit, scope_ref = pinned_json(*scope)
        require(explicit.get("version") == "laya-physical-scope-v1", "wrong explicit scope version")
        require(isinstance(explicit.get("units", {}), dict) and set(explicit.get("units", {})) <= adapter.UNITS,
                "explicit scope must name only owned units")
        require(all(isinstance(u, dict) and isinstance(u.get("cgroups", []), list) and
                    all(isinstance(p, str) for p in u.get("cgroups", []))
                    for u in explicit.get("units", {}).values()), "invalid explicit cgroup scope")
        require(isinstance(explicit.get("pid_identities", []), list) and all(isinstance(i, dict)
                    for i in explicit.get("pid_identities", [])), "invalid explicit PID identities")
    for h in [health, *histories]:
        require(isinstance(h.get("units"), dict) and all(isinstance(u, dict) for u in h["units"].values()) and
                isinstance(h.get("running_jobs"), list), "invalid health physical context")
    input_refs = [health_ref, plan_ref, *refs.values(), *history_refs] + ([scope_ref] if scope_ref else [])
    owned_roots = list(registries) + [r["world"] for _, r in all_rows(registries) if r.get("world")]
    protected = owned_roots + [r["path"] for r in input_refs]
    require(not any(Path(r["path"]).is_relative_to(absolute(p).resolve()) for r in refs.values()
                    for p in owned_roots),
            "registry snapshots must be copied outside owned registries/worlds")
    output = validate_output(output, [r["path"] for r in input_refs], protected)
    packet = {"version": "laya-physical-closure-observation-v1", "started_utc": started,
              "status": "blocked", "physical_closure_observed": False, "blockers": [],
              "provenance": {"snapshot": health_ref, "transfer_plan": plan_ref, "registry_snapshots": refs,
                             "historical_health": history_refs, "explicit_scope": scope_ref},
              "units": {}, "worlds": [], "processes": {"empty": None, "query_ok": False},
              "ports": {"empty": None, "query_ok": False},
              "limits": ["Sequential observations; hashes and stable reads do not prove atomicity or prevent later activity.",
                         "Parent-supplied history/scope must belong to this host; current PID/cgroup/network namespaces only.",
                         "No final120 certification, raw joins, attempt-event certification, adoption or lifecycle authority."]}
    reservations = [{"registry": registry, "job_id": r.get("id"), "attempt": r.get("attempt", 0)}
                    for registry, r in all_rows(registries) if r.get("admission_reserved")]
    packet["reservations"] = {"version": "laya-physical-reservation-observation-v1",
                              "basis": "five pinned copied registries, including administrative and retained rows",
                              "source_checked_utc": health.get("checked_utc"), "read_started_utc": started,
                              "registry_snapshots": refs, "ownership_validated": False,
                              "owned_reservations": reservations, "snapshot_only": True}
    try:
        jobs, admin = adapter.guarded_union(health, plan, registries)
        require(not health.get("parse_errors") and not health.get("running_jobs") and
                not any(health.get("active_by_arm", {}).values()), "active/unparseable health snapshot")
        require(not reservations, "retained reservations present")
        actual = []
        for job in jobs:
            _, attempts = adapter.attempt_rows(job)
            for row in attempts:
                require(row.get("status") in adapter.TERMINAL and row.get("admission_reserved") is False,
                        "retained attempt nonterminal or reservation unknown")
                actual.append((job["registry"], row))
        packet["ownership"] = {"validated": True, "logical_jobs": 120, "original": sorted(adapter.ORIGINAL),
                               "transferred": 113, "administrative_rows": admin, "administrative_additional_trials": 0,
                               "actual_attempts": len(actual)}
        packet["reservations"]["ownership_validated"] = True
    except (ValueError, KeyError, TypeError) as exc:
        packet["blockers"].append("logical/ownership refusal: " + str(exc))
        return write_packet(packet, output)

    host = host or Host()
    first = {name: query_unit(host, name) for name in sorted(adapter.UNITS)}
    blockers = packet["blockers"]
    boot_id = None
    try:
        boot_id = host.read("/proc/sys/kernel/random/boot_id").decode().strip()
        require(bool(boot_id), "missing boot identity")
        mounts = host.read("/proc/self/mountinfo").decode().splitlines()
        require(any(line.split()[4] == CGROOT and line.split()[3] == "/" and
                    line.split(" - ")[1].split()[0] == "cgroup2" for line in mounts), "unknown cgroup2 mount scope")
        require(any(line.split()[4] == "/proc" and line.split()[3] == "/" and
                    line.split(" - ")[1].split()[0] == "proc" and
                    "hidepid=" not in line for line in mounts), "unknown/hidden proc scope")
        btime = int(next(line.split()[1] for line in host.read("/proc/stat").decode().splitlines()
                         if line.startswith("btime ")))
        packet["host_scope"] = {"boot_id": boot_id, "boot_epoch": btime, "uid": host.uid,
                                "cgroup_root": CGROOT, "pid_namespace": host.link("/proc/self/ns/pid"),
                                "network_namespace": host.link("/proc/self/ns/net")}
        for h in [health, *histories]:
            checked = datetime.fromisoformat(h["checked_utc"])
            require(checked.tzinfo is not None and btime <= checked.timestamp() <= datetime.now(timezone.utc).timestamp(),
                    "health observation outside current boot")
            require(h.get("boot_id", boot_id) == boot_id, "health boot mismatch")
        if explicit:
            require(explicit.get("boot_id") == boot_id, "explicit scope boot mismatch")
    except (OSError, ValueError, KeyError, IndexError, StopIteration, UnicodeError) as exc:
        blockers.append("unknown host scope: " + error(exc))
        boot_id = None

    scopes = {name: set() for name in adapter.UNITS}
    for name in scopes:
        candidates = [h.get("units", {}).get(name, {}).get("ControlGroup") for h in [health, *histories]]
        candidates += [first[name].get("ControlGroup")]
        candidates += explicit.get("units", {}).get(name, {}).get("cgroups", [])
        for path in filter(None, candidates):
            if isinstance(path, str) and owned_cgroup(path, name, host.uid):
                scopes[name].add(path)
            else:
                blockers.append("invalid owned cgroup scope: " + name)
    claims, roots, ports, worlds = [], set(registries), set(), set()
    for registry, row in actual:
        jid, attempt = row["id"], row.get("attempt", 0)
        world = row.get("world")
        record = {"job_id": jid, "attempt": attempt, "registry": registry, "world": world, "query_ok": False}
        packet["worlds"].append(record)
        if row.get("runner_pid"):
            claims.append({"job_id": jid, "attempt": attempt, "role": "runner", "pid": row["runner_pid"],
                           "registry": registry, "world": world})
        try:
            require(bool(world), "actual attempt lacks world scope")
            w = absolute(world)
            require(str(w) == world and host.canonical(world) == world and world not in worlds,
                    "duplicate/aliased/noncanonical world scope")
            worlds.add(world)
            roots.add(world)
            raw = host.read(world + "/manifest.json")
            manifest = json_object(raw)
            require(manifest.get("runtime") in {"disposable_saved_world", "disposable_mall"} and
                    manifest.get("production_authority") is False, "missing disposable manifest identity")
            scoped_ports = {k: manifest.get(k) for k in ("game_port", "rcon_port")}
            require(all(type(p) is int and 0 < p < 65536 for p in scoped_ports.values()), "unknown manifest ports")
            ports.update(scoped_ports.values())
            record.update(query_ok=True, ports=scoped_ports,
                          manifest={"path": world + "/manifest.json", "sha256": hashlib.sha256(raw).hexdigest(),
                                    "bytes": len(raw)})
            try:
                pidraw = host.read(world + "/server/factorio.pid")
                pid = int(pidraw.strip())
                require(pid > 0, "invalid Factorio PID")
                claims.append({"job_id": jid, "attempt": attempt, "role": "factorio", "pid": pid,
                               "registry": registry, "world": world})
                record["pid_file"] = {"pid": pid, "sha256": hashlib.sha256(pidraw).hexdigest()}
            except FileNotFoundError:
                record["pid_file"] = {"state": "absent; process path scan still required"}
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            record.update(query_ok=False, error=error(exc))
            blockers.append("unknown world/manifest scope: " + jid)

    # Only the exact retained job/attempt/PID tuple can acquire a historical start identity.
    identities = list(explicit.get("pid_identities", []))
    for h in [health, *histories]:
        for r in h.get("running_jobs", []):
            require(isinstance(r, dict), "invalid historical running row")
            for role in ("runner", "factorio"):
                info = r.get(role, {})
                require(isinstance(info, dict), "invalid historical PID observation")
                if info.get("start_ticks"):
                    identities.append({"job_id": r["id"], "world": r.get("world"), "role": role,
                                       "registry": r.get("registry"),
                                       "pid": info.get("pid"), "start_ticks": info["start_ticks"], "boot_id": boot_id})
    for claim in claims:
        require(type(claim["pid"]) is int and claim["pid"] > 0, "invalid retained PID")
        world = claim["world"]
        matches = [i for i in identities if i.get("job_id") == claim["job_id"] and i.get("role") == claim["role"]
                   and i.get("pid") == claim["pid"] and i.get("world") == world and world is not None and
                   i.get("registry") == claim["registry"] and
                   i.get("attempt", claim["attempt"]) == claim["attempt"]]
        values = {(i.get("boot_id"), str(i.get("start_ticks"))) for i in matches}
        if len(values) == 1 and next(iter(values))[0] == boot_id and next(iter(values))[1].isdigit():
            claim["boot_id"], claim["start_ticks"] = next(iter(values))
        elif values:
            blockers.append("conflicting PID identity: " + claim["job_id"])

    if boot_id:
        packet["processes"] = process_scan(host, roots, claims, boot_id)
        worlds_known = all(r["query_ok"] for r in packet["worlds"])
        if not worlds_known:
            packet["processes"]["path_scan_empty"] = packet["processes"]["empty"]
            packet["processes"].update(empty=None, query_ok=False)
        if packet["processes"]["empty"] is not True:
            blockers.append("owned processes present or process scope unknown")
        port_result = {"started_utc": now(), "query_ok": False, "empty": None, "scoped_ports": sorted(ports)}
        try:
            port_result.update(listeners=host.listeners(ports), query_ok=True)
            port_result["empty"] = not port_result["listeners"]
            if not worlds_known:
                port_result.update(empty=None, query_ok=False, error="incomplete_manifest_port_scope")
        except (OSError, ValueError, ObservationError, subprocess.SubprocessError) as exc:
            port_result["error"] = error(exc)
        port_result["ended_utc"] = now()
        packet["ports"] = port_result
        if port_result["empty"] is not True:
            blockers.append("scoped ports listening or port query unknown")

    for name in sorted(adapter.UNITS):
        before = first[name]
        trees = [cgroup_tree(host, s, inactive(before)) for s in sorted(scopes[name])] if boot_id else []
        after = query_unit(host, name)
        query_ok = before["query_ok"] and after["query_ok"] and all(before.get(k) == after.get(k) for k in FIELDS)
        unit = {k: before.get(k) for k in FIELDS}
        unit.update(query_ok=query_ok, before=before, after=after, cgroups=trees,
                    cgroup_empty=all(t["empty"] is True for t in trees) if trees and all(
                        t["empty"] is not None for t in trees) else None,
                    processes_empty=packet["processes"]["empty"])
        if not query_ok:
            unit.update(cgroup_empty=None, processes_empty=None)
        elif not inactive(before):
            unit.update(cgroup_empty=None if unit["cgroup_empty"] is True else unit["cgroup_empty"],
                        processes_empty=None)
        elif unit["cgroup_empty"] is not True:
            unit["processes_empty"] = unit["cgroup_empty"]
        elif packet["ports"]["empty"] is not True:
            unit["processes_empty"] = None
        packet["units"][name] = unit
        if not inactive(before) or not inactive(after):
            blockers.append("owned supervisor active or query unknown: " + name)
        if not scopes[name]:
            blockers.append("unknown pinned cgroup scope: " + name)
        if not query_ok or any(before.get(k) != after.get(k) for k in FIELDS):
            blockers.append("unit query failed or changed during collection: " + name)
        if any(before.get(k) != health.get("units", {}).get(name, {}).get(k) for k in CLOSURE_FIELDS):
            blockers.append("fresh unit differs from pinned health; parent needs a new snapshot: " + name)
        if unit["cgroup_empty"] is not True:
            blockers.append("descendant cgroup populated or unknown: " + name)
    packet["status"] = "observed_clear" if not blockers else "blocked"
    packet["physical_closure_observed"] = not blockers
    return write_packet(packet, output)


def write_packet(packet, output):
    # Exclusive directory creation is the final race-safe overwrite guard. Never use exist_ok.
    output.mkdir()

    def write(name, data):
        raw = (json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        with (output / name).open("xb") as f:
            f.write(raw)
        return {"path": str(output / name), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    packet["ended_utc"] = now()
    packet["reservations"]["read_ended_utc"] = packet["ended_utc"]
    unit_ref = write("unit-evidence.json", {"started_utc": packet["started_utc"], "ended_utc": packet["ended_utc"],
                                           "units": packet["units"]})
    reservation_ref = write("reservation-evidence.json", packet["reservations"])
    for unit in packet["units"].values():
        unit["receipt"] = unit_ref
    packet.update(unit_receipt=unit_ref, reservation_receipt=reservation_ref)
    packet["closure_mapping"] = {"snapshot_sha256": packet["provenance"]["snapshot"]["sha256"],
                                 "transfer_plan_sha256": packet["provenance"]["transfer_plan"]["sha256"],
                                 "registry_snapshots": packet["provenance"]["registry_snapshots"],
                                 "owned_reservations": packet["reservations"]["owned_reservations"],
                                 "reservation_receipt": reservation_ref, "units": packet["units"]} if packet[
                                     "physical_closure_observed"] else None
    physical_ref = write("physical-closure.json", packet)
    lines = ["# Physical observation packet", "", f"Status: {packet['status']}.", "",
             f"Collection: {packet['started_utc']} to {packet['ended_utc']}.", "",
             "Sequential reads and hashes do not prove atomicity. This is not final120 certification or adoption.",
             "Parent must certify attempt start/terminal/release receipts and raw acceptance joins before final accounting.",
             "No services, processes, ports or input files were changed.", "", "Blockers:", ""]
    lines += ["- " + b for b in packet["blockers"]] or ["- None observed within the declared physical scope."]
    raw = ("\n".join(lines) + "\n").encode()
    with (output / "notes.md").open("xb") as f:
        f.write(raw)
    note_ref = {"path": str(output / "notes.md"), "sha256": hashlib.sha256(raw).hexdigest()}
    write("packet-sha256.json", {"physical": physical_ref, "units": unit_ref, "reservations": reservation_ref,
                                 "notes": note_ref})
    return packet


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", nargs=2, metavar=("ABS_PATH", "SHA256"), required=True)
    parser.add_argument("--transfer-plan", nargs=2, metavar=("ABS_PATH", "SHA256"), required=True)
    parser.add_argument("--registry", nargs=3, metavar=("ABS_REGISTRY", "ABS_COPY", "SHA256"), action="append", required=True)
    parser.add_argument("--historical-health", nargs=2, metavar=("ABS_PATH", "SHA256"), action="append", default=[])
    parser.add_argument("--scope", nargs=2, metavar=("ABS_PATH", "SHA256"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(len({r[0] for r in args.registry}) == len(args.registry), "duplicate registry argument")
        packet = collect(args.snapshot, args.transfer_plan, {r[0]: r[1:] for r in args.registry}, args.output_dir,
                         historical_health=args.historical_health, scope=args.scope)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "refused", "error": error(exc)}))
        return 2
    print(json.dumps({"status": packet["status"], "blockers": len(packet["blockers"]),
                      "output": str(args.output_dir)}))
    return 0 if packet["physical_closure_observed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
