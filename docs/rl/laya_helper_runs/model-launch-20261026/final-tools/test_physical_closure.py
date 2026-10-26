# Path: data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/test_physical_closure.py | Purpose: Exercise physical collector hazards using synthetic snapshots and an entirely fake host.
"""Run with python -B; no live collector invocation, subprocesses or host observations."""

from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest


spec = importlib.util.spec_from_file_location("collect_physical_closure", Path(__file__).with_name("collect_physical_closure.py"))
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
adapter = collector.adapter
BOOT = "ad3193c0-f343-41bf-875f-9635fd77e942"


class FakeHost:
    uid = 1000
    self_pid = 99999

    def __init__(self):
        self.files, self.dirs, self.links = {}, set(), {}
        self.failures, self.units, self.calls = {}, {}, []
        self.port_rows = []
        self.add("/proc/sys/kernel/random/boot_id", BOOT)
        self.add("/proc/stat", f"btime {int(datetime.now(timezone.utc).timestamp()) - 3600}\n")
        self.add("/proc/self/mountinfo", "1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n"
                 "2 0 0:2 / /proc rw - proc proc rw\n")
        self.links.update({"/proc/self/ns/pid": "pid:[123]", "/proc/self/ns/net": "net:[123]"})

    def add(self, path, value):
        self.files[path] = value.encode() if isinstance(value, str) else value
        self.dirs.update(str(p) for p in Path(path).parents)

    def check(self, operation, path):
        failure = self.failures.get((operation, path))
        if failure:
            raise failure

    def read(self, path):
        self.calls.append(("read", path))
        self.check("read", path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def names(self, path):
        self.check("names", path)
        if path not in self.dirs:
            raise FileNotFoundError(path)
        return sorted({Path(p).relative_to(path).parts[0] for p in self.files.keys() | self.dirs
                       if p != path and Path(p).is_relative_to(path)})

    def lstat(self, path):
        self.check("lstat", path)
        if path in self.dirs:
            return SimpleNamespace(st_mode=stat.S_IFDIR)
        if path in self.files:
            return SimpleNamespace(st_mode=stat.S_IFREG)
        if path in self.links:
            return SimpleNamespace(st_mode=stat.S_IFLNK)
        raise FileNotFoundError(path)

    def link(self, path):
        self.check("link", path)
        if path not in self.links:
            raise FileNotFoundError(path)
        return self.links[path]

    def canonical(self, path):
        if path in self.dirs:
            return path
        raise FileNotFoundError(path)

    def unit(self, name):
        self.calls.append(("unit", name))
        self.check("unit", name)
        entry = self.units[name]
        if isinstance(entry, list):
            return deepcopy(entry.pop(0))
        return deepcopy(entry)

    def listeners(self, ports):
        self.calls.append(("listeners", sorted(ports)))
        self.check("listeners", "ports")
        return deepcopy([p for p in self.port_rows if p["port"] in ports])

    def cgroup(self, scope, populated="0", pids=""):
        path = collector.CGROOT + scope
        self.add(path + "/cgroup.events", f"populated {populated}\nfrozen 0\n")
        self.add(path + "/cgroup.procs", pids)

    def process(self, pid, ticks="42", argv=("/usr/bin/foreign",), cwd="/tmp"):
        path = f"/proc/{pid}"
        # state starts field 3; starttime is index 19 in the suffix.
        self.add(path + "/stat", f"{pid} (comm has ) spaces) " + " ".join(["S"] + ["0"] * 18 + [ticks]) + "\n")
        self.add(path + "/status", f"Name:\tprocess\nUid:\t{self.uid}\t{self.uid}\t{self.uid}\t{self.uid}\n")
        self.add(path + "/cmdline", "\0".join(argv) + "\0")
        self.links[path + "/cwd"] = cwd


class PhysicalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="physical-closure-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = FakeHost()
        self.source = str(self.root / "registries" / adapter.BASE_UNIT.removesuffix(".service"))
        self.owners = {arm: str(self.root / "registries" / f"laya-model-scale15-{arm}-2123-miniship-20261027")
                       for arm in adapter.ARMS}
        self.registries = {r: {"jobs": []} for r in [self.source, *self.owners.values()]}
        self.jobs, assignments = [], []
        for arm in adapter.ARMS:
            for seed in adapter.SEEDS:
                jid = f"{arm}-s{seed}"
                original = jid in adapter.ORIGINAL
                registry = self.source if original else self.owners[arm]
                row = {"id": jid, "arm": arm, "seed": seed, "attempt": 0,
                       "status": "completed" if original else "not_started_deadline", "admission_reserved": False}
                if original:
                    world = str(self.root / "worlds" / jid)
                    row.update(world=world, preparation_started=10, started=20, runner_pid=1000 + len(self.jobs))
                    self.host.add(world + "/manifest.json", json.dumps({"runtime": "disposable_saved_world",
                                  "production_authority": False, "game_port": 30000 + len(self.jobs),
                                  "rcon_port": 40000 + len(self.jobs)}))
                else:
                    assignments.append({"id": jid, "arm": arm, "seed": seed, "source": self.source, "registry": registry})
                    self.registries[self.source]["jobs"].append({"id": jid, "arm": arm, "seed": seed,
                                                               "status": "cancelled_queued", "admission_reserved": False})
                self.registries[registry]["jobs"].append(row)
                self.jobs.append(dict(row, registry=registry))
        self.plan = {"version": "laya-model-capacity-transfer-v1", "logical_planned_jobs": 120,
                     "transferred_jobs": 113, "source_registry": self.source,
                     "untouched_started_jobs": sorted(adapter.ORIGINAL), "assignments": assignments}
        for name in adapter.UNITS:
            self.host.units[name] = {"Id": name, "LoadState": "loaded", "ActiveState": "inactive",
                                     "SubState": "dead", "MainPID": "0", "ControlPID": "0", "Result": "success",
                                     "ExecMainCode": "1", "ExecMainStatus": "0", "InvocationID": "a" * 32,
                                     "ControlGroup": f"/user.slice/user-1000.slice/user@1000.service/{name}"}
        self.health = {"version": "laya-model-cohort-host-health-v1", "checked_utc": collector.now(),
                       "logical_planned": 120, "logical_jobs": self.jobs,
                       "source_administrative_transfers": 113, "units": deepcopy(self.host.units),
                       "running_jobs": [], "active_by_arm": {}, "parse_errors": []}
        self.out = self.root / "new-evidence"
        self.explicit = None
        self.histories = []

    def pin(self, name, data):
        path = self.root / name
        raw = (json.dumps(data, sort_keys=True) + "\n").encode()
        path.write_bytes(raw)
        return str(path), hashlib.sha256(raw).hexdigest()

    def sync(self):
        for row in self.jobs:
            target = next(r for r in self.registries[row["registry"]]["jobs"] if r["id"] == row["id"])
            target.clear()
            target.update({k: v for k, v in row.items() if k != "registry"})
        refs = {r: self.pin(f"copy-{i}.json", data) for i, (r, data) in enumerate(self.registries.items())}
        self.health["registry_metadata"] = {r: {"sha256": ref[1]} for r, ref in refs.items()}
        self.health["logical_counts"] = dict(Counter(j["status"] for j in self.jobs))
        return self.pin("health.json", self.health), self.pin("plan.json", self.plan), refs

    def run_collect(self):
        snapshot, plan, refs = self.sync()
        scope = self.pin("scope.json", self.explicit) if self.explicit else None
        histories = [self.pin(f"history-{i}.json", h) for i, h in enumerate(self.histories)]
        return collector.collect(snapshot, plan, refs, self.out, historical_health=histories,
                                 scope=scope, host=self.host)

    def first_actual(self):
        return next(j for j in self.jobs if j.get("world"))

    def identities(self, row, ticks="41"):
        self.explicit = {"version": "laya-physical-scope-v1", "boot_id": BOOT,
                         "pid_identities": [{"job_id": row["id"], "attempt": 0, "role": "runner",
                                             "registry": row["registry"], "world": row["world"],
                                             "pid": row["runner_pid"], "start_ticks": ticks, "boot_id": BOOT}]}

    def test_closed_packet_pins_admin_and_preserves_inputs(self):
        snapshot, plan, refs = self.sync()
        before = {p: Path(p).read_bytes() for p in [snapshot[0], plan[0], *[r[0] for r in refs.values()]]}
        packet = collector.collect(snapshot, plan, refs, self.out, host=self.host)
        self.assertEqual(packet["status"], "observed_clear")
        self.assertEqual(packet["ownership"]["actual_attempts"], 7)
        self.assertEqual(len(packet["ownership"]["administrative_rows"]), 113)
        self.assertEqual(packet["ownership"]["administrative_additional_trials"], 0)
        self.assertEqual(packet["closure_mapping"]["owned_reservations"], [])
        for unit in packet["units"].values():
            self.assertTrue(unit["cgroup_empty"])
            self.assertEqual(unit["cgroups"][0]["state"], "explicitly_absent")
        manifest = json.loads((self.out / "packet-sha256.json").read_text())
        for ref in manifest.values():
            self.assertEqual(hashlib.sha256(Path(ref["path"]).read_bytes()).hexdigest(), ref["sha256"])
        for path, raw in before.items():
            self.assertEqual(Path(path).read_bytes(), raw)

    def test_nonterminal_and_reserved_refuse_before_host_queries(self):
        row = self.first_actual()
        row["status"] = "running"
        packet = self.run_collect()
        self.assertFalse(packet["physical_closure_observed"])
        self.assertFalse(self.host.calls)
        self.assertIsNone(packet["processes"]["empty"])
        self.assertIn("nonterminal", packet["blockers"][0])
        self.out = self.root / "reserved-evidence"
        row.update(status="completed", admission_reserved=True)
        packet = self.run_collect()
        self.assertEqual(len(packet["reservations"]["owned_reservations"]), 1)
        self.assertIn("reserved", packet["blockers"][0])

    def test_retained_nonterminal_attempt_refuses(self):
        row = self.first_actual()
        old = {k: v for k, v in row.items() if k != "registry"}
        old["status"] = "running"
        row.update(attempt=1, previous_attempts=[old])
        packet = self.run_collect()
        self.assertFalse(self.host.calls)
        self.assertIn("retained attempt nonterminal", packet["blockers"][0])

    def test_active_and_nonzero_supervisors_block(self):
        name = sorted(adapter.UNITS)[0]
        self.host.units[name].update(ActiveState="active", MainPID="123")
        packet = self.run_collect()
        self.assertFalse(packet["physical_closure_observed"])
        self.assertIsNone(packet["units"][name]["cgroup_empty"])
        self.assertIsNone(packet["units"][name]["processes_empty"])

    def test_empty_parent_does_not_hide_descendant_process(self):
        name = sorted(adapter.UNITS)[0]
        scope = self.host.units[name]["ControlGroup"]
        self.host.cgroup(scope)
        self.host.cgroup(scope + "/worker", "1", "8765\n")
        packet = self.run_collect()
        unit = packet["units"][name]
        self.assertFalse(unit["cgroup_empty"])
        self.assertFalse(unit["processes_empty"])
        self.assertEqual(unit["cgroups"][0]["nodes"][1]["pids"], [8765])

    def test_permission_failures_are_unknown(self):
        name = sorted(adapter.UNITS)[0]
        scope = self.host.units[name]["ControlGroup"]
        self.host.cgroup(scope)
        self.host.failures[("read", collector.CGROOT + scope + "/cgroup.events")] = PermissionError()
        packet = self.run_collect()
        self.assertIsNone(packet["units"][name]["cgroup_empty"])
        self.assertIsNone(packet["units"][name]["processes_empty"])
        self.out = self.root / "query-denied"
        self.host.failures[("unit", name)] = PermissionError()
        packet = self.run_collect()
        self.assertFalse(packet["units"][name]["query_ok"])
        self.assertIsNone(packet["units"][name]["cgroup_empty"])
        self.assertIsNone(packet["units"][name]["processes_empty"])

    def test_unknown_cgroup_requires_pinned_historical_scope(self):
        name = sorted(adapter.UNITS)[0]
        old = deepcopy(self.health)
        self.host.units[name]["ControlGroup"] = ""
        self.health["units"][name]["ControlGroup"] = ""
        packet = self.run_collect()
        self.assertIsNone(packet["units"][name]["cgroup_empty"])
        self.out = self.root / "with-history"
        self.histories = [old]
        packet = self.run_collect()
        self.assertTrue(packet["physical_closure_observed"])

    def absent_transient(self):
        name = adapter.BASE_UNIT
        old = deepcopy(self.health)
        self.host.units[name].update(LoadState="not-found", ActiveState="inactive", SubState="dead",
                                     MainPID="0", ControlPID="0", ControlGroup="", InvocationID="",
                                     ExecMainCode="0")
        self.health["units"][name] = deepcopy(self.host.units[name])
        return name, old

    def test_absent_transient_requires_historical_scope(self):
        name, old = self.absent_transient()
        packet = self.run_collect()
        self.assertTrue(packet["units"][name]["query_ok"])
        self.assertIsNone(packet["units"][name]["cgroup_empty"])
        self.assertIsNone(packet["closure_mapping"])
        self.histories = [old]
        self.out = self.root / "with-absent-history"
        packet = self.run_collect()
        self.assertTrue(packet["physical_closure_observed"])
        self.assertEqual(packet["units"][name]["LoadState"], "not-found")

    def test_absent_transient_does_not_hide_populated_historical_cgroup(self):
        name, old = self.absent_transient()
        self.histories = [old]
        self.host.cgroup(old["units"][name]["ControlGroup"], populated="1", pids="8765")
        packet = self.run_collect()
        self.assertFalse(packet["physical_closure_observed"])
        self.assertFalse(packet["units"][name]["cgroup_empty"])
        self.assertIsNone(packet["closure_mapping"])

    def test_inconsistent_absent_transient_remains_unknown(self):
        name, old = self.absent_transient()
        self.histories = [old]
        self.host.units[name]["MainPID"] = "123"
        packet = self.run_collect()
        self.assertFalse(packet["units"][name]["query_ok"])
        self.assertIsNone(packet["units"][name]["cgroup_empty"])
        self.assertIsNone(packet["closure_mapping"])

    def test_descendant_enumeration_failure_and_changed_tree_are_unknown(self):
        name = sorted(adapter.UNITS)[0]
        scope = self.host.units[name]["ControlGroup"]
        self.host.cgroup(scope)
        root = collector.CGROOT + scope
        self.host.failures[("names", root)] = PermissionError()
        packet = self.run_collect()
        self.assertIsNone(packet["units"][name]["cgroup_empty"])
        self.assertIsNone(packet["closure_mapping"])
        del self.host.failures[("names", root)]
        original = self.host.read
        reads = []
        def changing_read(path):
            if path == root + "/cgroup.events":
                reads.append(path)
                if len(reads) > 1:
                    return b"populated 1\nfrozen 0\n"
            return original(path)
        self.host.read = changing_read
        self.out = self.root / "tree-changed"
        packet = self.run_collect()
        self.assertIsNone(packet["units"][name]["cgroup_empty"])
        self.assertFalse(packet["physical_closure_observed"])

    def test_populated_parent_event_blocks_even_without_visible_pids(self):
        name = sorted(adapter.UNITS)[0]
        self.host.cgroup(self.host.units[name]["ControlGroup"], populated="1")
        packet = self.run_collect()
        self.assertFalse(packet["units"][name]["cgroup_empty"])

    def test_historical_pid_identity_binds_exact_world_and_registry(self):
        row = self.first_actual()
        self.host.process(row["runner_pid"], ticks="42")
        history = deepcopy(self.health)
        history["running_jobs"] = [{"id": row["id"], "registry": row["registry"], "world": row["world"],
                                    "runner": {"pid": row["runner_pid"], "start_ticks": "41"}}]
        self.histories = [history]
        packet = self.run_collect()
        self.assertTrue(packet["physical_closure_observed"])
        history["running_jobs"][0]["registry"] += "-foreign"
        self.out = self.root / "foreign-history"
        packet = self.run_collect()
        self.assertIsNone(packet["processes"]["empty"])

    def test_reused_foreign_pid_is_not_owned_and_argv_is_private(self):
        row = self.first_actual()
        self.identities(row)
        self.host.process(row["runner_pid"], ticks="42", argv=("/usr/bin/other", "private-unrelated-argument"))
        packet = self.run_collect()
        claim = next(c for c in packet["processes"]["claims"] if c["pid"] == row["runner_pid"])
        self.assertEqual(claim["state"], "reused_foreign_pid")
        self.assertFalse(packet["processes"]["matched"])
        self.assertTrue(packet["physical_closure_observed"])
        self.assertNotIn("private-unrelated-argument", (self.out / "physical-closure.json").read_text())

    def test_live_unpinned_pid_is_ambiguous(self):
        row = self.first_actual()
        self.host.process(row["runner_pid"])
        packet = self.run_collect()
        self.assertIsNone(packet["processes"]["empty"])
        self.assertIn("ambiguous_pid_identity", {c["state"] for c in packet["processes"]["claims"]})
        self.assertFalse(packet["physical_closure_observed"])

    def test_pinned_identity_present_even_without_path_blocks(self):
        row = self.first_actual()
        self.identities(row, "42")
        self.host.process(row["runner_pid"])
        packet = self.run_collect()
        self.assertFalse(packet["processes"]["empty"])
        self.assertIn("pinned_identity_present", {c["state"] for c in packet["processes"]["claims"]})

    def test_process_paths_use_complete_tokens_and_detect_escape(self):
        row = self.first_actual()
        self.host.process(7000, argv=("factorio", row["world"] + "-foreign/config.ini"))
        self.host.process(7001, argv=("unrelated", "embedded=" + row["world"]))
        self.host.process(7002, argv=("python", "--registry=" + self.source))
        self.host.process(7003, argv=("factorio", "--config", row["world"] + "/server/config.ini"))
        packet = self.run_collect()
        self.assertEqual({p["pid"] for p in packet["processes"]["matched"]}, {7002, 7003})
        self.assertFalse(packet["physical_closure_observed"])

    def test_collector_itself_is_excluded_from_path_scan(self):
        self.host.process(self.host.self_pid, argv=("python", "--registry", self.source))
        packet = self.run_collect()
        self.assertTrue(packet["physical_closure_observed"])

    def test_process_permission_query_stays_unknown(self):
        self.host.process(7000)
        self.host.failures[("read", "/proc/7000/cmdline")] = PermissionError()
        packet = self.run_collect()
        self.assertIsNone(packet["processes"]["empty"])
        self.assertTrue(all(u["processes_empty"] is None for u in packet["units"].values()))

    def test_manifest_missing_does_not_certify(self):
        row = self.first_actual()
        del self.host.files[row["world"] + "/manifest.json"]
        packet = self.run_collect()
        self.assertIsNone(packet["processes"]["empty"])
        self.assertIsNone(packet["ports"]["empty"])
        self.assertFalse(packet["physical_closure_observed"])

    def test_listener_query_error_remains_unknown(self):
        self.host.failures[("listeners", "ports")] = PermissionError()
        packet = self.run_collect()
        self.assertIsNone(packet["ports"]["empty"])
        self.assertTrue(all(u["processes_empty"] is None for u in packet["units"].values()))

    def test_aliased_world_path_stays_unknown(self):
        original = self.host.canonical
        world = self.first_actual()["world"]
        self.host.canonical = lambda path: path + "-other" if path == world else original(path)
        packet = self.run_collect()
        self.assertIsNone(packet["processes"]["empty"])
        self.assertIsNone(packet["ports"]["empty"])
        self.assertFalse(packet["physical_closure_observed"])

    def test_ambiguous_json_manifests_stay_unknown(self):
        self.host.add(self.first_actual()["world"] + "/manifest.json", '{"game_port":30000,"game_port":30001}')
        packet = self.run_collect()
        self.assertIsNone(packet["processes"]["empty"])
        self.assertIsNone(packet["ports"]["empty"])
        self.assertFalse(packet["physical_closure_observed"])

    def test_unexpected_listener_is_blocker_with_no_stop_authority(self):
        row = self.first_actual()
        port = json.loads(self.host.files[row["world"] + "/manifest.json"])["rcon_port"]
        self.host.port_rows = [{"protocol": "udp", "port": port, "local": f"*:{port}", "ownership": "unknown"}]
        packet = self.run_collect()
        self.assertFalse(packet["ports"]["empty"])
        self.assertEqual(packet["ports"]["listeners"][0]["ownership"], "unknown")
        self.assertFalse(packet["physical_closure_observed"])

    def test_admin_transfer_cannot_become_an_attempt(self):
        admin = next(r for r in self.registries[self.source]["jobs"] if r["id"] not in adapter.ORIGINAL)
        admin.update(world=str(self.root / "worlds" / "fake-admin-attempt"))
        packet = self.run_collect()
        self.assertIn("administrative copy was attempted", packet["blockers"][0])
        self.assertFalse(self.host.calls)

    def test_extra_and_wrong_owner_ids_refuse(self):
        self.plan["assignments"][0]["registry"] = self.owners["jiwo"]
        packet = self.run_collect()
        self.assertIn("wrong owner", packet["blockers"][0])
        self.assertFalse(self.host.calls)

    def test_existing_and_world_outputs_protected_before_queries(self):
        self.out.mkdir()
        marker = self.out / "keep"
        marker.write_text("unchanged")
        with self.assertRaisesRegex(collector.GuardError, "must not exist"):
            self.run_collect()
        self.assertEqual(marker.read_text(), "unchanged")
        world = Path(self.first_actual()["world"])
        world.mkdir(parents=True)
        self.out = world / "forbidden-output"
        with self.assertRaisesRegex(collector.GuardError, "inside registry/world"):
            self.run_collect()
        self.assertFalse(self.host.calls)
        self.assertFalse(self.out.exists())

    def test_dangling_symlink_output_and_hash_mismatch_refuse(self):
        self.out.symlink_to(self.root / "nonexistent-target")
        with self.assertRaisesRegex(collector.GuardError, "must not exist"):
            self.run_collect()
        snapshot, plan, refs = self.sync()
        with self.assertRaisesRegex(collector.GuardError, "hash mismatch"):
            collector.collect((snapshot[0], "0" * 64), plan, refs, self.root / "fresh", host=self.host)

    def test_failed_unit_result_remains_a_fact(self):
        name = sorted(adapter.UNITS)[0]
        self.host.units[name].update(ActiveState="failed", SubState="failed", Result="exit-code", ExecMainStatus="7")
        self.health["units"][name] = deepcopy(self.host.units[name])
        packet = self.run_collect()
        self.assertTrue(packet["physical_closure_observed"])
        self.assertEqual(packet["units"][name]["Result"], "exit-code")
        self.assertEqual(packet["units"][name]["ExecMainStatus"], "7")
        self.assertEqual(packet["units"][name]["ActiveState"], "failed")

    def test_changed_unit_queries_and_wrong_boot_stay_unknown(self):
        name = sorted(adapter.UNITS)[0]
        old = deepcopy(self.host.units[name])
        changed = dict(old, InvocationID="b" * 32)
        self.host.units[name] = [old, changed]
        packet = self.run_collect()
        self.assertFalse(packet["units"][name]["query_ok"])
        self.assertIsNone(packet["units"][name]["cgroup_empty"])
        self.host.units[name] = old
        self.out = self.root / "wrong-boot"
        self.explicit = {"version": "laya-physical-scope-v1", "boot_id": "another-boot"}
        packet = self.run_collect()
        self.assertFalse(packet["physical_closure_observed"])
        self.assertIsNone(packet["processes"]["empty"])

    def test_exact_unit_and_scoped_port_command_parsers(self):
        host = collector.Host()
        calls = []
        name = sorted(adapter.UNITS)[0]
        def command(argv):
            calls.append(argv)
            if argv[0] == "systemctl":
                return "\n".join(f"{k}={v}" for k, v in self.host.units[name].items())
            return "tcp LISTEN 0 128 127.0.0.1:33333 0.0.0.0:*\n" + "udp UNCONN 0 0 *:44444 *:*\n"
        host.command = command
        self.assertTrue(collector.query_unit(host, name)["query_ok"])
        self.assertEqual(calls[0][:4], ["systemctl", "--user", "show", name])
        listeners = host.listeners({33333})
        self.assertEqual([r["port"] for r in listeners], [33333])
        self.assertNotIn("44444", json.dumps(listeners))
        host.command = lambda argv: "Id=foreign.service\n"
        self.assertFalse(collector.query_unit(host, name)["query_ok"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
