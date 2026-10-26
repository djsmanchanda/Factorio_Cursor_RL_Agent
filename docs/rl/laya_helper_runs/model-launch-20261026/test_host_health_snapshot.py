# Path: docs/rl/laya_helper_runs/model-launch-20261026/test_host_health_snapshot.py | Purpose: Verify byte-exact cohort snapshots across a concurrent registry update.
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


COLLECTOR = Path(__file__).with_name("check-host-health.py")


class SnapshotTests(unittest.TestCase):
    def test_original_bytes_survive_live_update_after_collection(self):
        with tempfile.TemporaryDirectory(prefix="laya-health-snapshot-") as directory:
            root = Path(directory)
            task = root / "data/laya-orchestration/model-launch-20261026"
            script = task / "supervision/check-host-health.py"
            script.parent.mkdir(parents=True)
            shutil.copyfile(COLLECTOR, script)
            registries = root / "data/laya-experiments"
            registries.mkdir()
            (registries / ".host-admission.lock").touch()
            source = registries / "laya-model-base-2123-miniship-20261026"
            originals = {"guided-s80300", "kev-s80300", "kev-s80301", "vela-s80300",
                         "vela-s80301", "jiwo-s80300", "jiwo-s80301"}
            plan = {"source_registry": str(source), "untouched_started_jobs": sorted(originals),
                    "assignments": []}
            jobs = {source: []}
            for arm in ("guided", "kev", "vela", "jiwo"):
                segment = registries / f"laya-model-scale15-{arm}-2123-miniship-20261027"
                jobs[segment] = []
                for seed in range(80300, 80330):
                    jid = f"{arm}-s{seed}"
                    row = {"id": jid, "arm": arm, "seed": seed, "status": "queued"}
                    if jid in originals:
                        jobs[source].append(dict(row, status="completed", started=1))
                    else:
                        jobs[source].append(dict(row, status="cancelled_queued"))
                        jobs[segment].append(row)
                        plan["assignments"].append({"id": jid, "registry": str(segment)})
            initial = {}
            for registry, rows in jobs.items():
                registry.mkdir()
                # Deliberately retain formatting a JSON re-serialization cannot preserve.
                raw = (json.dumps({"jobs": rows, "updated": time.time()}, indent="\t") + "  \n\n").encode()
                (registry / "job-status.json").write_bytes(raw)
                initial[str(registry)] = raw
            gate = registries / "_host-scheduler-gate"
            gate.mkdir()
            (gate / "job-status.json").write_text(json.dumps({"updated": time.time()}))
            (task / "capacity15-20261027").mkdir()
            (task / "capacity15-20261027/transfer-plan.json").write_text(json.dumps(plan))
            binaries = root / "bin"
            binaries.mkdir()
            fake_systemctl = binaries / "systemctl"
            fake_systemctl.write_text(
                "#!/usr/bin/python3\nimport os,pathlib\n"
                "pathlib.Path(os.environ['LAYA_TEST_MUTATE_REGISTRY']).write_text('{}')\n"
                "print('ActiveState=inactive\\nMainPID=0\\nResult=success\\nExecMainStatus=0')\n")
            fake_systemctl.chmod(0o755)
            output = root / "evidence"
            result = subprocess.run(["python3", "-B", str(script), str(output)],
                                    env=dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"],
                                             LAYA_TEST_MUTATE_REGISTRY=str(source / "job-status.json")),
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            health = json.loads((output / "host-health.json").read_text())
            self.assertEqual(health["logical_counts"], {"completed": 7, "queued": 113})
            self.assertEqual((source / "job-status.json").read_text(), "{}")
            for registry, raw in initial.items():
                copied = (output / (Path(registry).name + "-host-status.json")).read_bytes()
                self.assertEqual(copied, raw)
                self.assertEqual(hashlib.sha256(copied).hexdigest(),
                                 health["registry_metadata"][registry]["sha256"])


if __name__ == "__main__":
    unittest.main()
