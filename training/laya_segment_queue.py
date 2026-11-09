# Path: training/laya_segment_queue.py | Purpose: Persist matched disposable regression jobs and report all planned outcomes.
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import uuid

from training.laya_checkpoint_bundle import digest, validate

TERMINAL = frozenset({"completed", "functional_failure", "infrastructure_failure", "timeout", "cancelled", "incompatible"})
PREDICATE = "c6-v1"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _safe_id(value):
    return isinstance(value, str) and 0 < len(value) <= 100 and all(c.isascii() and (c.isalnum() or c in "-_.") for c in value) and value not in (".", "..")


class Queue:
    def __init__(self, path: Path, catalog: Path):
        self.catalog = Path(catalog).resolve()
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS suites (id TEXT PRIMARY KEY, spec TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, suite TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, result TEXT)")

    def close(self):
        self.db.close()

    def enqueue(self, spec: dict) -> list[str]:
        suite = spec.get("suite_id")
        arms = spec.get("arms")
        seeds = spec.get("seeds")
        generations = spec.get("generations")
        if not _safe_id(suite) or not isinstance(arms, dict) or set(arms) != {"candidate", "reference"}:
            raise ValueError("suite and candidate/reference arms required")
        if not isinstance(seeds, list) or not seeds or any(type(seed) is not int or seed < 0 for seed in seeds) or len(set(seeds)) != len(seeds):
            raise ValueError("distinct nonnegative seeds required")
        if not isinstance(generations, list) or any(not _safe_id(item) for item in generations) or len(set(generations)) != len(generations):
            raise ValueError("generations must be distinct")
        if spec.get("predicate_version") != PREDICATE or type(spec.get("budget_ticks")) is not int or spec["budget_ticks"] <= 0:
            raise ValueError("strict predicate and positive budget required")
        if not spec.get("factorio_version") or not spec.get("mod_sha256") or not spec.get("source_sha256") or not spec.get("state_schema") or not spec.get("predicate_sha256"):
            raise ValueError("runtime identities required")
        for arm in arms.values():
            if not isinstance(arm, dict) or not all(arm.get(key) for key in ("commit", "code_sha256", "freeze_sha256")) or arm.get("dirty") is not False:
                raise ValueError("explicit clean frozen arm provenance required")
            if any(len(arm[key]) != 64 or any(char not in "0123456789abcdef" for char in arm[key]) for key in ("code_sha256", "freeze_sha256")):
                raise ValueError("invalid frozen arm hashes")
        inputs = [("C0", None, None, None)]
        for generation in generations:
            manifest = validate(self.catalog, generation, expected={key: spec[key] for key in ("factorio_version", "mod_sha256", "predicate_version", "predicate_sha256", "state_schema", "source_sha256")})
            inputs.append((manifest["checkpoint"], generation, digest(self.catalog / generation / "checkpoint.json"), manifest["tick"]))
        if len({entry[0] for entry in inputs}) != len(inputs):
            raise ValueError("one generation per checkpoint required")
        jobs = []
        for checkpoint, generation, manifest_hash, tick in inputs:
            for seed in seeds:
                for arm, identity in sorted(arms.items()):
                    job_id = uuid.uuid4().hex
                    payload = {"suite_id": suite, "attempt_id": job_id, "arm": arm, "arm_identity": identity,
                               "seed": seed, "checkpoint": checkpoint, "generation": generation,
                               "manifest_sha256": manifest_hash, "baseline_tick": tick,
                               "successor": f"C{int(checkpoint[1:])+1}" if checkpoint != "C6" else None,
                               "budget_ticks": spec["budget_ticks"], "predicate_version": PREDICATE,
                               "predicate_sha256": spec["predicate_sha256"]}
                    jobs.append((job_id, suite, _canonical(payload), "queued", None))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT INTO suites VALUES (?, ?)", (suite, _canonical(spec)))
            self.db.executemany("INSERT INTO jobs VALUES (?, ?, ?, ?, ?)", jobs)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return [job[0] for job in jobs]

    def jobs(self, suite: str) -> list[dict]:
        rows = self.db.execute("SELECT payload, status, result FROM jobs WHERE suite=? ORDER BY rowid", (suite,)).fetchall()
        return [{**json.loads(payload), "status": status, "result": json.loads(result) if result else None} for payload, status, result in rows]

    def dispatch(self, suite: str, manager) -> None:
        """Manager must durably deduplicate attempt IDs and own global admission.

        A reserved job is reconciled by ID after any coordinator crash. No local
        server cap, port allocator, or process killing is implemented here.
        """
        if manager.global_admission_contract != "laya-global-admission-v1":
            raise ValueError("global admission manager required")
        for job in self.jobs(suite):
            if job["status"] not in ("queued", "reserved", "running"):
                continue
            if job["status"] == "queued":
                if job["generation"]:
                    try:
                        validate(self.catalog, job["generation"])
                        if digest(self.catalog / job["generation"] / "checkpoint.json") != job["manifest_sha256"]:
                            raise ValueError("manifest changed")
                    except ValueError as error:
                        self.finish(job["attempt_id"], "incompatible", {"reason": str(error)})
                        continue
                changed = self.db.execute("UPDATE jobs SET status='reserved' WHERE id=? AND status='queued'", (job["attempt_id"],)).rowcount
                if not changed:
                    continue
            # submit_or_observe is an atomic, idempotent scheduler operation.
            response = manager.submit_or_observe(job)
            state = response.get("status")
            if state in TERMINAL:
                self.finish(job["attempt_id"], state, response.get("result", {}))
            elif state == "running":
                self.db.execute("UPDATE jobs SET status='running' WHERE id=? AND status IN ('reserved','running')", (job["attempt_id"],))
            elif state != "waiting":
                raise ValueError("invalid manager status")

    def finish(self, attempt: str, status: str, result: dict) -> None:
        if status not in TERMINAL or not isinstance(result, dict):
            raise ValueError("invalid terminal result")
        changed = self.db.execute("UPDATE jobs SET status=?, result=? WHERE id=? AND status NOT IN ('completed','functional_failure','infrastructure_failure','timeout','cancelled','incompatible')", (status, _canonical(result), attempt)).rowcount
        if not changed:
            raise ValueError("unknown or already terminal attempt")

    def report(self, suite: str) -> dict:
        jobs = self.jobs(suite)
        if not jobs:
            raise ValueError("unknown suite")
        rows = []
        for job in jobs:
            result = job["result"] or {}
            reached = result.get("strict_reached")
            successor = job["successor"]
            tick = reached.get(successor) if isinstance(reached, dict) and successor else None
            baseline = job["baseline_tick"] if job["baseline_tick"] is not None else result.get("baseline_tick")
            qualified = result.get("predicate_version") == (f"{successor.lower()}-v1" if successor else None) and result.get("predicate_sha256") == job["predicate_sha256"] and result.get("baseline_verified") is True and type(baseline) is int and baseline >= 0
            observed = qualified and result.get("strict_telemetry_complete") is True and isinstance(reached, dict)
            passed = bool(type(tick) is int and tick > baseline) if observed and successor else None
            rows.append({"attempt_id": job["attempt_id"], "arm": job["arm"], "seed": job["seed"],
                         "checkpoint": job["checkpoint"], "status": job["status"], "successor": successor,
                         "successor_passed": passed,
                         "later_failure": passed and job["status"] in {"functional_failure", "timeout"},
                         "slow_warning": result.get("slow_warning"), "proxy": result.get("proxy"),
                         "reason": result.get("reason")})
        return {"version": "laya-segment-report-v1", "suite_id": suite, "planned": len(rows),
                "terminal": sum(row["status"] in TERMINAL for row in rows),
                "unknown": sum(row["successor_passed"] is None for row in rows),
                "full_run_gate": "not_evaluated", "segment_results_are_conditional": True, "rows": rows}
