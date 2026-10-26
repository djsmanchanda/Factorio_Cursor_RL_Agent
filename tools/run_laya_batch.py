# Path: tools/run_laya_batch.py | Purpose: Run a matched-seed Laya experiment batch (one factor per arm) at a fixed concurrency until a deadline, and report per-arm outcomes.
"""Fixed-concurrency experiment batch over frozen code arms.

Every arm runs the same seeds from the same base save. Jobs are ordered seed-major
and arm round-robin, so a deadline cut leaves the arms matched. Each trial is a
fresh `run_laya_clean prepare` + `run` with a game-tick window and a wall cap;
the world is stopped afterwards. Concurrency is a fixed worker-pool ceiling;
an optional host budget gates admissions across cohorts. Unstarted jobs stay
listed as such in job-status.json.

batch.json:
  {"name": str, "until": ISO time, "concurrency": int, "game_port_base": int, "rcon_port_base": int,
   "trial_wall_seconds": optional int (default 7200), "max_game_ticks": int, "source_save": path, "python": path,
   "seeds": [int, ...],
   "host_budget": optional {"registry": path, "stale_seconds": 180,
                             "admit_interval_seconds": 10, "admit_min_available_gb": 4.5},
   "runtime": optional {"update_threads": 1..64, "cpu_affinity": "slot" | taskset list,
                        "nice": 0..19, "python_nice": 0..19},
   "retention": optional {"enabled": true, "keep_recent_runs": 300, "corpus": path}; with a host budget,
                a finished cohort condenses older host runs (tools/laya_run_retention.py),
   "arms": [{"id": str, "code": frozen worktree, "mods": staged mods dir, "revision": sha,
             "change": str, "variant": optional path, "extra_args": optional [str],
             "runtime": optional arm override of the batch runtime}]}
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import signal
import statistics
import subprocess
import threading
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
from tools.laya_cohort_report import metrics as cohort_metrics, markdown as cohort_markdown
from tools.laya_batch_defaults import DEFAULT_TRIAL_WALL_SECONDS
from tools.laya_batch_state import CohortLedger, dependencies_ready, file_lock, read_records, request_admission
from tools.laya_run_retention import DEFAULT_KEEP_RECENT_RUNS, apply as apply_retention

PHASE = re.compile(r"^\+(\d+)s PHASE REACHED: (C\d) .* at tick (\d+)")
SUMMARY = re.compile(r"SUMMARY: stop=(\S+) decisions=(\d+) failures=(\d+) phases=(\S*) accepted=(\S*)")
FINAL_ACCEPTANCE = re.compile(r"\baccepted_final=(\S+)")


def _atomic(path: Path, data: object) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


class HostBudget:
    """Serialize reservations against a fresh scheduler gate, counting legacy worlds too."""

    def __init__(self, root: Path, options: dict) -> None:
        self.registry = Path(options["registry"]).resolve()
        if root.resolve().parent != self.registry:
            raise ValueError("host-budget cohort must be directly inside its registry")
        self.stale_s = float(options.get("stale_seconds", 180))
        self.interval_s = float(options.get("admit_interval_seconds", 10))
        self.min_gb = float(options.get("admit_min_available_gb", 4.5))
        self.next_admit = 0.0
        self.reason = "waiting_for_gate"

    @contextmanager
    def lock(self):
        with (self.registry / ".host-admission.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def may_admit(self, now: float) -> bool:
        # A missing/stale gate blocks admissions, including after a timed pause.
        # Old cohort caps are not host limits; their actual worlds still count.
        try:
            gate = json.loads((self.registry / "_host-scheduler-gate/job-status.json").read_text())
            cap = gate["controller"]["slots"]
            age = now - float(gate["updated"])
            if type(cap) is not int or cap < 0 or not 0 <= age <= self.stale_s:
                raise ValueError("invalid or stale gate")
            occupied = 0
            for path in self.registry.glob("*/job-status.json"):
                if path.parent.name.startswith("_host-scheduler"):
                    continue
                data = json.loads(path.read_text())
                if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
                    continue  # Pre-registry cohorts wrote other shapes (a bare list); they own no live worlds.
                # Stale running records remain reserved until the owner reconciles
                # them; a dead supervisor may have left its Factorio worlds alive.
                occupied += sum(bool(job.get("admission_reserved")) or
                                job.get("status") in {"preparing", "running"}
                                for job in data["jobs"])
        except (OSError, ValueError, KeyError, TypeError):
            self.reason = "unavailable_host_budget"
            return False
        memory = None
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    memory = int(line.split()[1]) / 1048576
        except OSError:
            pass
        if occupied >= cap:
            self.reason = "host_full_or_paused"
        elif memory is not None and memory < self.min_gb:
            self.reason = "memory"
        elif now < self.next_admit:
            self.reason = "admission_pacing"
        else:
            self.reason = "admitted"
            return True
        return False


def summarize(world: Path) -> dict:
    """Outcome facts from the runner log and journal: stop, phases, strict acceptance, C5 predicate version.

    A missing runner log (crash before SUMMARY, or a journal-only artifact) still lets the journal contribute
    observed acceptance. Final acceptance is only taken from an explicit end-of-run fresh sample, never invented.
    """
    log = world / "logs/laya-runner.log"
    text = log.read_text(errors="replace") if log.exists() else ""
    start_tick, phases, out = None, {}, {"runner_stop": None, "accepted_final": None}
    log_codes, recent, upkeep = Counter(), deque(maxlen=20), 0
    for line in text.splitlines():
        if (outcome := re.search(r"\bOUTCOME: ([a-z_]+)", line)) and outcome[1] != "ok":
            log_codes[outcome[1]] += 1
            recent.append(outcome[1])
        if "LOAN RELEASE FAILED:" in line or "MALL CHEST CAP FAILED:" in line:
            upkeep += 1
        if (match := PHASE.match(line)):
            phases[match.group(2)] = {"seconds": int(match.group(1)), "tick": int(match.group(3))}
        elif (match := SUMMARY.search(line)):
            out.update(runner_stop=match.group(1), decisions=int(match.group(2)), failures=int(match.group(3)),
                       phases=[p for p in match.group(4).split(",") if p and p != "none"],
                       accepted=[p for p in match.group(5).split(",") if p and p != "none"])
            # Ever-passed is the latched history; final is the fresh predicate on the last sample (unknown -> None).
            out["accepted_ever"] = list(out["accepted"])
            final = FINAL_ACCEPTANCE.search(line)
            out["accepted_final"] = (None if final is None or final.group(1) == "unknown" else
                                     [p for p in final.group(1).split(",") if p and p != "none"])
        elif start_tick is None and " tick=" in line and "DECISION 0 " in line:
            start_tick = int(line.split(" tick=")[1].split()[0])
    for phase in phases.values():
        phase["game_seconds"] = (phase["tick"] - start_tick) / 60 if start_tick else None
    out["phase_times"] = phases
    out["untyped_stuck"] = text.count("OUTCOME: untyped_stuck")
    out.update(typed_outcomes=dict(log_codes), recent_typed_outcomes=dict(Counter(recent)),
               maintenance_failure_events=upkeep, c2_latest_checks=None, malformed_journal_rows=0,
               c5_predicate_version=None, c5_predicate_versions=[])
    codes, classifications = Counter(), Counter()
    observed = 0
    journal_passes = set(out.get("accepted_ever") or [])
    observed_acceptance = False
    observed_c5_versions = set()
    out["evidence_errors"] = 0
    for path in (world / "evidence/clean-decisions.jsonl", world / "evidence/decision-states.jsonl"):
        if not path.exists():
            continue
        with path.open() as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("record must be an object")
                except (ValueError, TypeError):
                    out["evidence_errors"] += 1
                    if path.name == "clean-decisions.jsonl":
                        out["malformed_journal_rows"] += 1
                    continue
                if path.name == "clean-decisions.jsonl" and row.get("event") == "acceptance":
                    statuses = row.get("statuses")
                    if isinstance(statuses, dict) and any(value in ("passed", "failed", "unknown") for value in statuses.values()):
                        observed_acceptance = True
                        journal_passes.update(k for k, value in statuses.items() if value == "passed")
                    if row.get("at") == "end" and isinstance(row.get("fresh"), dict):
                        out["accepted_final"] = sorted(k for k, value in row["fresh"].items() if value == "passed")
                    checks = row.get("checks")
                    c2 = checks.get("C2") if isinstance(checks, dict) else None
                    out["c2_latest_checks"] = c2.get("checks") if isinstance(c2, dict) else None
                    out["c2_checks_at"] = row.get("at")
                    evidence = row.get("evidence")
                    c5 = evidence.get("C5") if isinstance(evidence, dict) else None
                    version = c5.get("predicate_version") if isinstance(c5, dict) else None
                    if isinstance(version, str) and version:
                        observed_c5_versions.add(version)
                        out["c5_predicate_version"] = version
                if row.get("event") == "laya_acceptance" and row.get("phase") == "C6":
                    out["laya_acceptance"] = {"C6": {k: v for k, v in row.items() if k not in {"event", "phase"}}}
                if row.get("event") == "summary":
                    out.update({k: row[k] for k in ("laya_acceptance", "production_service", "research_acceptance") if k in row})
                outcome = row.get("outcome")
                if row.get("version") == "laya-decision-record-v1" and isinstance(outcome, dict):
                    observed += 1
                    if outcome.get("ok") is not True:
                        codes[outcome.get("code") or "untyped_result"] += 1
                        classifications[outcome.get("classification") or "unclassified"] += 1
    out["c5_predicate_versions"] = sorted(observed_c5_versions)
    if observed_acceptance:
        out["accepted"] = out["accepted_ever"] = sorted(journal_passes)
    out.update(outcome_observations=observed, outcome_codes=dict(codes) if observed else None,
               outcome_classifications=dict(classifications) if observed else None)
    return out


def c5_versions(job: dict) -> set[str]:
    """Observed C5 predicate versions; a historical row with neither field stays unknown, never c5-v1."""
    versions = job.get("c5_predicate_versions")
    found = {v for v in versions if isinstance(v, str) and v} if isinstance(versions, list) else set()
    latest = job.get("c5_predicate_version")
    if isinstance(latest, str) and latest:
        found.add(latest)
    return found


def c5_version_coverage(jobs, arms) -> dict:
    """Per-arm observed/unknown C5 version coverage over planned jobs."""
    coverage = {}
    for arm in arms:
        rows = [job for job in jobs if job.get("arm") == arm["id"]]
        versions, unknown = Counter(), 0
        for job in rows:
            found = c5_versions(job)
            if found:
                versions.update(found)
            else:
                unknown += 1
        coverage[arm["id"]] = {"planned": len(rows), "observed": len(rows) - unknown,
                               "unknown": unknown, "versions": dict(versions)}
    return coverage


def _c5_version(job: dict) -> str | None:
    """The single known C5 predicate version, else None: missing or mixed versions are not comparable."""
    versions = c5_versions(job)
    return next(iter(versions)) if len(versions) == 1 else None


def _same_c5_version(old: dict, new: dict) -> bool:
    version = _c5_version(old)
    return version is not None and version == _c5_version(new)


class Batch:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.root = path.parent
        self.cfg = json.loads(path.read_text())
        self.cfg.setdefault("trial_wall_seconds", DEFAULT_TRIAL_WALL_SECONDS)
        self.until = dt.datetime.fromisoformat(self.cfg["until"]).timestamp()
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.jobs = [{"id": f"{arm['id']}-s{seed}", "arm": arm["id"], "seed": seed, "status": "queued"}
                     for index, seed in enumerate(self.cfg["seeds"])
                     for arm in (self.cfg["arms"][index % len(self.cfg["arms"]):]
                                 + self.cfg["arms"][:index % len(self.cfg["arms"])])]
        self.arms = {arm["id"]: arm for arm in self.cfg["arms"]}
        self.host = HostBudget(self.root, self.cfg["host_budget"]) if self.cfg.get("host_budget") else None
        self.ledger = CohortLedger(self.root, self.cfg)
        self.admission = "run"
        self.admission_reason = "ready"
        self._resumed = False
        existing = {j["id"] for j in self.jobs}
        for job in self.ledger.plan["jobs"]:
            if job["id"] not in existing:
                self.jobs.append(dict(job, status="cancelled_queued", termination_cause="legacy_cut"))

    def apply_requests(self) -> None:
        with file_lock(self.root / ".admission-requests.lock"):
            requests = read_records(self.root / "admission-requests.jsonl")
        for row in requests:
            event_id = "request:" + row["request_id"]
            prior = next((r for r in self.ledger.events if r["event_id"] == event_id), None)
            if row["mode"] in {"run", "drain"}:
                self.admission = row["mode"]
            if prior is not None:
                for job in self.jobs:
                    if job["id"] in prior["cuts"] and job["status"] == "queued":
                        job.update(status="cancelled_queued", termination_cause="operator_cut")
                continue
            ids = row.get("job_ids")
            known = {j["id"] for j in self.jobs}
            if ids is not None and not set(ids) <= known:
                raise ValueError("cancellation contains unknown planned job IDs")
            cuts = []
            if row["mode"] == "cancel_queued":
                for job in self.jobs:
                    if job["status"] == "queued" and (ids is None or job["id"] in ids):
                        job.update(status="cancelled_queued", termination_cause="operator_cut")
                        cuts.append(job["id"])
            self.ledger.append(event_id, "admission_request", at=time.time(), request=row, cuts=cuts)

    def may_admit(self) -> bool:
        if self.admission != "run":
            self.admission_reason = "draining"
            return False
        ready, self.admission_reason = dependencies_ready(self.cfg.get("depends_on", []), own_root=self.root)
        gate = self.cfg.get("baseline_gate")
        if ready and gate:
            try:
                decision = json.loads(Path(gate["path"]).read_text())
                ready = (decision.get("status") == "ready" and decision.get("revision") == gate["revision"]
                         and bool(decision.get("evidence")) and bool(decision.get("decided_by")))
                self.admission_reason = "ready" if ready else "baseline_not_settled"
            except (OSError, ValueError, KeyError, TypeError):
                ready, self.admission_reason = False, "baseline_not_settled"
        return ready

    def submit(self, pool, job: dict, slot: int):
        if not self.may_admit():
            return None
        with self.host.lock() if self.host else nullcontext():
            if self.host and not self.host.may_admit(time.time()):
                return None
            job.update(status="preparing", admission_reserved=True)
            self.save()  # Publish the reservation before another cohort can admit.
            try:
                future = pool.submit(self.run_job, job, slot)
            except Exception:
                job.update(status="queued", admission_reserved=False)
                self.save()
                raise
            if self.host:
                self.host.next_admit = time.time() + self.host.interval_s
            return future

    def save(self) -> None:
        with self.lock:
            self.ledger.initialize()
            _atomic(self.root / "job-status.json", {"name": self.cfg["name"], "updated": time.time(), "jobs": self.jobs,
                    "owner_pid": os.getpid(), "admission": self.admission,
                    "admission_reason": self.admission_reason,
                    **({"host_budget": {"reason": self.host.reason}} if self.host else {})})

    def _runtime(self, arm: dict) -> dict:
        runtime = dict(self.cfg.get("runtime") or {})
        runtime.update(arm.get("runtime") or {})
        return runtime

    @staticmethod
    def _cpu_for_slot(slot: int) -> str:
        """Map a slot to an allowed logical CPU, respecting a cgroup/cpuset mask."""
        try:
            cpus = sorted(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            cpus = list(range(os.cpu_count() or 1))
        return str(cpus[slot % len(cpus)])

    def _cmd(self, arm: dict, *args: str) -> list[str]:
        command = [self.cfg["python"], str(Path(arm["code"]) / "tools/run_laya_clean.py"), *args]
        # Prepare launches Factorio, so lowering its Python priority would also
        # lower the engine's inherited priority. Apply this only to the runner.
        nice = int(self._runtime(arm).get("python_nice", 0)) if args and args[0] == "run" else 0
        return ["nice", "-n", str(nice), *command] if nice else command

    def runtime_args(self, slot: int, arm: dict | None = None) -> list[str]:
        """Per-world engine settings shared by every arm, with an optional arm override."""
        runtime = self._runtime(arm or {})
        args: list[str] = []
        threads = runtime.get("update_threads")
        if threads is not None:
            args += ["--update-threads", str(threads)]
        affinity = runtime.get("cpu_affinity")
        if affinity == "slot":
            affinity = self._cpu_for_slot(slot)
        if affinity:
            args += ["--cpu-affinity", str(affinity)]
        nice = int(runtime.get("nice", 0))
        if nice:
            args += ["--nice", str(nice)]
        return args

    def run_job(self, job: dict, slot: int) -> None:
        arm = self.arms[job["arm"]]
        code = Path(arm["code"])
        # A rerun after --resume gets a fresh world: a prepared root cannot be prepared again.
        world = code / "data/laya-saved-world" / self.cfg["name"] / (
            f"{job['id']}-r{job['attempt']}" if job.get("attempt") else job["id"])
        job.update(world=str(world), slot=slot, preparation_started=time.time())
        attempt_id = f"{job['id']}:{job.get('attempt', 0)}"
        self.ledger.append("start:" + attempt_id, "attempt_started", at=time.time(), job=dict(job))
        log_path = self.root / "logs" / f"{job['id']}.out"
        env = dict(os.environ, PYTHONPATH=str(code), PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
        ports = ["--game-port", str(self.cfg["game_port_base"] + slot), "--rcon-port", str(self.cfg["rcon_port_base"] + slot)]
        process = None
        resume_bundle = job.get("resume_bundle")
        recovery_args = ["--ops-resume-bundle", resume_bundle] if resume_bundle else []
        checkpoints = arm.get("ops_checkpoints", self.cfg.get("ops_checkpoints", False))
        try:
            with log_path.open("a") as log:
                job["status"] = "preparing"
                prepared = subprocess.run(
                    self._cmd(arm, "prepare", "--root", str(world), "--source-save", self.cfg["source_save"],
                              "--mods-source", arm["mods"], *ports, *self.runtime_args(slot, arm), *recovery_args),
                    cwd=code, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=240)
                if prepared.returncode:
                    raise RuntimeError(f"prepare exit {prepared.returncode}")
                run = self._cmd(arm, "run", "--root", str(world), "--seed", str(job["seed"]), *ports,
                                "--actions", "deterministic", "--policy", "guided", "--speed", "2", "--decisions", "0",
                                "--no-helper", "--max-game-ticks", str(self.cfg["max_game_ticks"]),
                                "--experiment", self.cfg["name"], *(["--variant", arm["variant"]] if arm.get("variant") else []),
                                *arm.get("extra_args", []), *recovery_args,
                                *(["--ops-checkpoint-dir", str(world / "checkpoints"),
                                   "--max-wall-seconds", str(self.cfg["trial_wall_seconds"])] if checkpoints else []))
                process = subprocess.Popen(run, cwd=code, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                job.update(status="running", started=time.time(), runner_pid=process.pid)
                self.save()
                allowance = self.cfg["trial_wall_seconds"]
                if resume_bundle:
                    meta = json.loads((Path(resume_bundle) / "checkpoint.json").read_text())
                    allowance = max(0, allowance - meta["cumulative_active_wall_seconds"]
                                    - (job["started"] - job["preparation_started"]))
                end = min(job["started"] + allowance, self.until)
                while process.poll() is None and time.time() < end and not self.stopping.is_set():
                    time.sleep(1)
                if process.poll() is None:  # The runner stops after its current decision on SIGTERM.
                    cause = ("operator_stop" if self.stopping.is_set() else
                             "cohort_deadline" if end == self.until else "wall_window")
                    job.update(status="interrupted" if self.stopping.is_set() else
                               "deadline_cap" if end == self.until else "wall_cap", termination_cause=cause)
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=120)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=30)
                else:
                    job["status"] = "completed" if process.returncode == 0 else "runner_error"
                job.update(runner_exited=time.time(), runner_returncode=process.returncode)
                job["elapsed_s"] = round(job["runner_exited"] - job["started"])
                job.update(summarize(world))
                job.setdefault("termination_cause", "game_window" if job.get("runner_stop") == "window_timeout"
                               else job.get("runner_stop") or job["status"])
        except Exception as error:
            job.update(status="harness_error" if "started" in job else "prepare_failed", error=f"{type(error).__name__}: {error}")
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=30)
                job.update(runner_exited=time.time(), runner_returncode=process.returncode)
            job["cleanup_started"] = time.time()
            try:
                subprocess.run(self._cmd(arm, "stop", "--root", str(world)), cwd=code, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=True)
                job["admission_reserved"] = False
                job["cleanup_completed"] = time.time()
            except Exception as error:
                job["cleanup_error"] = f"{type(error).__name__}: {error}"
            finally:
                self.ledger.append("terminal:" + attempt_id, "attempt_terminal", at=time.time(), job=dict(job))
                self.save()  # A failed cleanup retains the reservation for reconciliation.

    RERUN = ("queued", "preparing", "running", "interrupted", "not_started_interrupted")

    def resume(self) -> None:
        """Continue a cohort whose supervisor died or stopped: keep finished jobs, rerun only unfinished work.

        Terminal outcomes (completed, errors, wall caps, deadline censoring) are evidence and never rerun. A job left
        preparing/running/interrupted records its dead attempt and reruns in a fresh world; its stale reservation is
        released. Only call this when the previous supervisor and its worlds are gone. Host cohorts share the
        retention lock through publishing the resumed status, so a pass can never condense a run being revived.
        """
        lock = file_lock(self.host.registry / ".retention.lock") if self.host else nullcontext()
        with lock:
            self._resume()
            self.save()

    def _resume(self) -> None:
        previous = {job["id"]: job for job in json.loads((self.root / "job-status.json").read_text())["jobs"]}
        for index, planned in enumerate(self.jobs):
            job = previous.get(planned["id"])
            if job is None:
                continue
            if job["status"] in self.RERUN:
                world = Path(job["world"]) if job.get("world") else None
                if world and (world / "server/factorio.pid").exists():
                    pid = int((world / "server/factorio.pid").read_text())
                    proc = Path(f"/proc/{pid}/cmdline")
                    if proc.exists() and str(world / "server/config.ini").encode() in proc.read_bytes():
                        raise ValueError(f"resume requires stopped owned world: {job['id']}")
                if job.get("runner_pid") and Path(f"/proc/{job['runner_pid']}/cmdline").exists():
                    argv = Path(f"/proc/{job['runner_pid']}/cmdline").read_bytes()
                    if world and str(world).encode() in argv:
                        raise ValueError(f"resume requires stopped runner: {job['id']}")
                history = list(job.get("previous_attempts", ()))
                if job.get("started") or job.get("admission_reserved") or job["status"] != "queued":
                    attempt = {k: v for k, v in job.items() if k != "previous_attempts"}
                    history.append(attempt)
                    self.ledger.append(f"recovery:{job['id']}:{job.get('attempt', 0)}", "attempt_interrupted",
                                       at=time.time(), job=attempt)
                job = dict(planned, status="queued")
                if history:
                    job.update(attempt=len(history), previous_attempts=history)
                arm = self.arms[job["arm"]]
                if world and arm.get("ops_checkpoints", self.cfg.get("ops_checkpoints", False)):
                    candidates = []
                    for meta_path in (world / "checkpoints").glob("*/checkpoint.json"):
                        meta = json.loads(meta_path.read_text())
                        if meta.get("version") != "laya-ops-checkpoint-v1":
                            raise ValueError("incompatible ops checkpoint on interrupted attempt")
                        candidates.append((meta["created_at"], meta_path.parent))
                    if candidates:
                        job.update(resume_bundle=str(max(candidates)[1]), resume_mode="coherent_ops_generation")
                    elif history:
                        job.update(status="incompatible_checkpoint", termination_cause="no_complete_ops_generation")
            self.jobs[index] = job
        self._resumed = True

    def run(self, resume: bool = False) -> None:
        with file_lock(self.root / ".supervisor.lock", blocking=False):
            if resume and not self._resumed:
                self.resume()
            self._run(resume=resume)

    def _run(self, resume: bool = False) -> None:
        (self.root / "logs").mkdir(exist_ok=True)
        signal.signal(signal.SIGTERM, lambda *_: self.stopping.set())
        slots = list(range(self.cfg["concurrency"]))
        active: dict = {}
        with self.host.lock() if self.host else nullcontext():
            if (self.root / "job-status.json").exists() and not resume:
                raise ValueError("existing cohort status: use its frozen supervisor; this harness starts fresh cohorts")
            self.save()
        with ThreadPoolExecutor(self.cfg["concurrency"]) as pool:
            queue = [job for job in self.jobs if job["status"] == "queued"]
            while (queue or active) and not (self.stopping.is_set() and not active):
                self.apply_requests()
                queue = [j for j in queue if j["status"] == "queued"]
                if time.time() + self.cfg["trial_wall_seconds"] > self.until:
                    for job in queue:
                        job.update(status="not_started_deadline", termination_cause="cohort_deadline")
                    queue.clear()
                for future in [f for f in active if f.done()]:
                    slot = active.pop(future)
                    try:
                        future.result()
                    except Exception as error:
                        self.ledger.append(f"worker-error:{time.time_ns()}", "worker_error", at=time.time(),
                                           error=f"{type(error).__name__}: {error}", slot=slot)
                    # A failed cleanup retains its slot and host reservation.
                    if not any(j.get("slot") == slot and j.get("admission_reserved") for j in self.jobs):
                        slots.append(slot)
                # A trial that cannot finish its wall window before the deadline is not started (censored, listed).
                while queue and slots and not self.stopping.is_set() and self.may_admit():
                    if time.time() + self.cfg["trial_wall_seconds"] > self.until:
                        for job in queue:
                            job["status"] = "not_started_deadline"
                        queue.clear()
                        break
                    job, slot = queue[0], slots[0]
                    future = self.submit(pool, job, slot)
                    if future is None:
                        break
                    queue.pop(0)
                    slots.pop(0)
                    active[future] = slot
                if self.stopping.is_set():
                    for job in queue:
                        job["status"] = "not_started_interrupted"
                    queue.clear()
                time.sleep(2)
                self.save()  # Keep queued and long-running cohorts visible to the scheduler.
                self.report()
        self.save()
        self.report()
        if not self.stopping.is_set():
            self.retain()

    def retain(self) -> None:
        """Condense older host runs once this cohort ends; retention never fails the cohort."""
        options = self.cfg.get("retention") or {}
        if not self.host or options.get("enabled") is False:
            return
        try:
            result = apply_retention(self.host.registry,
                                     keep_recent_runs=int(options.get("keep_recent_runs", DEFAULT_KEEP_RECENT_RUNS)),
                                     corpus=Path(options["corpus"]) if options.get("corpus") else None)
            self.ledger.append(f"retention:{time.time_ns()}", "retention", at=time.time(), **result)
        except Exception as error:
            self.ledger.append(f"retention:{time.time_ns()}", "retention_error", at=time.time(),
                               error=f"{type(error).__name__}: {error}")

    def report(self) -> None:
        rows = defaultdict(list)
        for job in self.jobs:
            rows[job["arm"]].append(job)
        reliability = cohort_metrics(self.jobs, self.cfg["arms"])
        versions = c5_version_coverage(self.jobs, self.cfg["arms"])
        lines = [f"# {self.cfg['name']}", "", f"Planned jobs: {len(self.jobs)}; statuses: {dict(Counter(j['status'] for j in self.jobs))}", "",
                 *cohort_markdown(reliability), "",
                 "| Arm | Run | Stops | Proxy C1/C2/C3/C4 | Median game-min to C2 | Strict accepted (any) | untyped_stuck | Strict final | Laya C6 choice/output | Typed refusals |",
                 "|---|---:|---|---|---:|---|---:|---|---|---|"]
        for arm, jobs in rows.items():
            ran = [j for j in jobs if j.get("runner_stop") or j["status"] in ("completed", "wall_cap", "runner_error")]
            stops = dict(Counter(j.get("runner_stop") or j["status"] for j in ran))
            reached = [sum(p in (j.get("phases") or []) for j in ran) for p in ("C1", "C2", "C3", "C4")]
            c2 = [j["phase_times"]["C2"]["game_seconds"] / 60 for j in ran
                  if "C2" in (j.get("phase_times") or {}) and j["phase_times"]["C2"].get("game_seconds") is not None]
            accepted = dict(Counter(p for j in ran for p in (j.get("accepted") or [])))
            final_rows = [j for j in ran if isinstance(j.get("accepted_final"), list)]
            final = dict(Counter(p for j in final_rows for p in j["accepted_final"]))
            c6_rows = [(j.get("laya_acceptance") or {}).get("C6") for j in ran]
            c6_rows = [r for r in c6_rows if isinstance(r, dict) and
                       r.get("version") == "laya-c6-choice-output-v2" and r.get("status") in {"passed", "failed"}]
            c6_passed = sum(r["status"] == "passed" for r in c6_rows)
            codes = dict(sum((Counter(j["outcome_codes"]) for j in ran if j.get("outcome_codes") is not None), Counter()))
            code_observed = sum(j.get("outcome_codes") is not None for j in ran)
            median_c2 = f"{statistics.median(c2):.1f} (n={len(c2)})" if c2 else "-"
            lines.append(f"| {arm} | {len(ran)}/{len(jobs)} | {stops} | {'/'.join(map(str, reached))} | {median_c2} | "
                         f"{accepted} | {sum(j.get('untyped_stuck', 0) for j in ran)} | "
                         f"{final} (n={len(final_rows)}/{len(ran)}) | {c6_passed}/{len(c6_rows)} observed "
                         f"(planned={len(jobs)}) | {codes} (n={code_observed}/{len(ran)}) |")
        coverage = "; ".join(f"{arm}: {row['observed']}/{row['planned']} observed {row['versions']}, "
                             f"unknown {row['unknown']}" for arm, row in versions.items())
        lines += ["", "C5 predicate versions (acceptance evidence.C5.predicate_version, planned denominator): " + coverage,
                  "C5 paired comparisons require a known, equal predicate version per seed pair; unknown, mixed or "
                  "unequal-version pairs are excluded from C5 and counted.",
                  "", "Strict final observations do not prove sustained service; unknown is not failure.",
                  "Laya C6 choice/output and legacy deterministic C6 are separate endpoints.",
                  "failures counts caught exceptions; typed refusals and untyped faults are reported separately."]
        paired = {}
        arm_ids = [a["id"] for a in self.cfg["arms"]]
        for old, new in zip(arm_ids, arm_ids[1:]):
            left = {j.get("seed"): j for j in rows[old] if j["status"] == "completed"}
            right = {j.get("seed"): j for j in rows[new] if j["status"] == "completed"}
            seeds = left.keys() & right.keys()
            comparisons = {}
            for milestone in ("C2", "C5", "C6"):
                up = down = known = excluded = 0
                for seed in seeds:
                    a, b = left[seed].get("accepted_ever", left[seed].get("accepted")), right[seed].get("accepted_ever", right[seed].get("accepted"))
                    if isinstance(a, list) and isinstance(b, list):
                        if milestone == "C5" and not _same_c5_version(left[seed], right[seed]):
                            excluded += 1
                            continue
                        known += 1
                        up += milestone in b and milestone not in a
                        down += milestone in a and milestone not in b
                comparisons[milestone + "_ever"] = {
                    "observed_pairs": known, "new_only": up, "old_only": down,
                    **({"version_excluded_pairs": excluded} if milestone == "C5" else {})}
            def endpoint(job, name):
                if name.endswith("_final"):
                    observed = job.get("accepted_final")
                    return name[:-6] in observed if isinstance(observed, list) else None
                if name == "C6_v2":
                    receipt = (job.get("laya_acceptance") or {}).get("C6", {})
                    return (receipt["status"] == "passed" if receipt.get("version") == "laya-c6-choice-output-v2"
                            and receipt.get("status") in {"passed", "failed"} else None)
                research = job.get("research_acceptance")
                return (research.get("earned") if isinstance(research, dict) and research.get("available") is True
                        and type(research.get("earned")) is bool else None)
            for name in ("C2_final", "C5_final", "C6_final", "C6_v2", "research"):
                observed = [(endpoint(left[s], name), endpoint(right[s], name), s) for s in seeds]
                observed = [(a, b, s) for a, b, s in observed if a is not None and b is not None]
                comparable = observed
                version_excluded = 0
                if name == "C5_final":
                    comparable = [pair for pair in observed if _same_c5_version(left[pair[2]], right[pair[2]])]
                    version_excluded = len(observed) - len(comparable)
                entry = {"observed_pairs": len(comparable)}
                if name == "C5_final":
                    entry["version_excluded_pairs"] = version_excluded
                entry.update(new_only=sum(b and not a for a, b, _ in comparable),
                             old_only=sum(a and not b for a, b, _ in comparable))
                comparisons[name] = entry
            paired[new + "_vs_" + old] = {"complete_pairs": len(seeds),
                "legacy_deterministic": {k: v for k, v in comparisons.items() if k not in {"C6_v2", "research"}},
                "versioned_endpoints": {k: comparisons[k] for k in ("C6_v2", "research")}}
        _atomic(self.root / "report.json", {"version": "laya-batch-report-v3", "planned_jobs": len(self.jobs),
                "ledger_events": len(self.ledger.events), "admission": self.admission,
                "statuses": dict(Counter(j["status"] for j in self.jobs)), "paired": paired, "jobs": self.jobs,
                "strict_c2_reliability": reliability, "c5_predicate_versions": versions})
        exclusions = {pair: {name: entry["version_excluded_pairs"] for name, entry in
                             {**data["legacy_deterministic"], **data["versioned_endpoints"]}.items()
                             if "version_excluded_pairs" in entry}
                      for pair, data in paired.items()}
        lines += ["", "Complete matched pairs: " + str({k: v["complete_pairs"] for k, v in paired.items()}),
                  "C5 version-excluded seed pairs (unknown, mixed or unequal): " + str(exclusions)]
        lines += ["", "Arms:"] + [f"- {a['id']} @ {a.get('revision', '?')[:9]}: {a.get('change', '')}" for a in self.cfg["arms"]]
        (self.root / "report.md").write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("batch", type=Path)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--admission", choices=("run", "drain", "cancel_queued"))
    parser.add_argument("--request-id")
    parser.add_argument("--reason")
    parser.add_argument("--job-id", action="append")
    parser.add_argument("--resume", action="store_true",
                        help="continue this cohort after its supervisor died: keep finished jobs, rerun unfinished ones")
    args = parser.parse_args(argv)
    batch = Batch(args.batch.resolve())
    if args.admission:
        request_admission(batch.root, request_id=args.request_id, mode=args.admission,
                          reason=args.reason, job_ids=args.job_id)
        return 0
    if args.report_only:
        batch.jobs = json.loads((batch.root / "job-status.json").read_text())["jobs"]
        batch.report()
        return 0
    batch.run(resume=args.resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
