# Path: data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/final_accounting.py | Purpose: Derive guarded logical120 accounting from explicit immutable receipts at final closure.
"""Standard-library-only report projection. See final-adapter-notes.md for the input contract."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from fractions import Fraction
import hashlib
import itertools
import json
import math
import os
from pathlib import Path


ARMS = ("guided", "kev", "vela", "jiwo")
SEEDS = tuple(range(80300, 80330))
PHASES = tuple(f"C{i}" for i in range(2, 7))
VERSIONS = {p: p.lower() + "-v1" for p in PHASES}
STATES = ("passed", "failed", "unknown", "unobserved", "incompatible")
TERMINAL = {"completed", "runner_error", "harness_error", "prepare_failed", "wall_cap",
            "deadline_cap", "interrupted", "not_started_deadline", "not_started_interrupted",
            "cancelled_queued", "incompatible_checkpoint"}
UNSTARTED = {"not_started_deadline", "not_started_interrupted", "cancelled_queued", "incompatible_checkpoint"}
ORIGINAL = {"guided-s80300", "kev-s80300", "kev-s80301", "vela-s80300", "vela-s80301",
            "jiwo-s80300", "jiwo-s80301"}
BASE_UNIT = "laya-model-base-2123-miniship-20261026.service"
UNITS = {BASE_UNIT} | {f"laya-model-scale15-{a}-2123-miniship-20261027.service" for a in ARMS}
FAULT_CATEGORIES = ("typed_fault", "ordinary_block", "tool_error", "safety_error", "untyped_fault",
                    "backend_error", "runner_failure", "harness_failure", "prepare_failure",
                    "timeout", "oom", "reclaim", "cleanup_failure", "evidence_parse_error")
SUPPLEMENTS = ("raw_join_certification", "pending_records", "latency", "resource_deltas",
               "identity_evidence", "capacity_evidence")


class GuardError(ValueError):
    """Inputs are not a closed, unambiguous cohort; emit no report."""


def require(condition, message):
    if not condition:
        raise GuardError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def is_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def receipt(value, label):
    require(isinstance(value, dict) and isinstance(value.get("path"), str) and bool(value["path"]) and is_hash(value.get("sha256")),
            f"missing pinned receipt: {label}")


def load_json(path, expected=None):
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    require(expected is None or sha == expected, f"input hash mismatch: {path}")
    data = json.loads(raw)
    require(isinstance(data, dict), f"input must be an object: {path}")
    return data, {"path": str(path), "sha256": sha, "bytes": len(raw)}


def index_unique(rows, key, label):
    require(isinstance(rows, list), f"missing list: {label}")
    result = {}
    for row in rows:
        value = key(row)
        require(value not in result, f"duplicate {label}: {value}")
        result[value] = row
    return result


def guarded_union(health, plan, registries):
    expected = {f"{a}-s{s}": (a, s) for a in ARMS for s in SEEDS}
    require(health.get("version") == "laya-model-cohort-host-health-v1", "wrong health snapshot version")
    require(plan.get("version") == "laya-model-capacity-transfer-v1", "wrong transfer plan version")
    require(plan.get("logical_planned_jobs") == 120 and plan.get("transferred_jobs") == 113,
            "wrong transfer denominators")
    original = plan.get("untouched_started_jobs", [])
    require(len(original) == 7 and set(original) == ORIGINAL, "original7 ownership mismatch")
    assignments = index_unique(plan.get("assignments"), lambda r: r["id"], "assignment")
    require(len(assignments) == 113 and set(assignments) == set(expected) - ORIGINAL,
            "transferred113 ownership mismatch")
    source = plan["source_registry"]
    require(Path(source).name + ".service" == BASE_UNIT, "wrong source registry")
    owners = {i: source for i in original}
    for jid, item in assignments.items():
        arm, seed = expected[jid]
        require((item.get("arm"), item.get("seed")) == (arm, seed) and item.get("source") == source,
                f"assignment identity mismatch: {jid}")
        require(Path(item["registry"]).name + ".service" ==
                f"laya-model-scale15-{arm}-2123-miniship-20261027.service", f"wrong owner: {jid}")
        owners[jid] = item["registry"]
    require(set(registries) == set(owners.values()) and len(registries) == 5, "five registry snapshots required")
    indexes = {r: index_unique(d.get("jobs"), lambda j: j["id"], f"registry job {r}")
               for r, d in registries.items()}
    require(set(indexes[source]) == set(expected), "source plan/status IDs mismatch")
    for registry, rows in indexes.items():
        if registry != source:
            require(set(rows) == {i for i, owner in owners.items() if owner == registry},
                    f"segment has missing/extra IDs: {registry}")
    admin = []
    for jid in assignments:
        row = indexes[source][jid]
        require(row.get("status") == "cancelled_queued" and not row.get("admission_reserved") and
                not any(row.get(k) for k in ("started", "preparation_started", "world", "previous_attempts", "runner_pid")),
                f"administrative copy was attempted/reserved: {jid}")
        require((row.get("arm"), row.get("seed")) == expected[jid], f"administrative identity: {jid}")
        admin.append(row)
    logical = index_unique(health.get("logical_jobs"), lambda j: j["id"], "logical job")
    require(health.get("logical_planned") == 120 and set(logical) == set(expected), "logical120 ID set mismatch")
    for jid, job in logical.items():
        require((job.get("arm"), job.get("seed")) == expected[jid], f"logical arm/seed mismatch: {jid}")
        require(job.get("registry") == owners[jid], f"logical owner mismatch: {jid}")
        require({k: v for k, v in job.items() if k != "registry"} == indexes[owners[jid]][jid],
                f"logical row differs from owner snapshot: {jid}")
        require(job.get("status") in TERMINAL, f"nonterminal logical job: {jid}")
        require(not job.get("admission_reserved"), f"reserved logical job: {jid}")
        if jid in ORIGINAL:
            require(bool(job.get("started") or job.get("previous_attempts")), f"original7 has no actual start: {jid}")
    require(health.get("logical_counts") == dict(Counter(j["status"] for j in logical.values())),
            "logical status counts mismatch")
    require(health.get("source_administrative_transfers") == 113, "missing administrative count")
    return list(logical.values()), admin


def attempt_rows(job):
    history = job.get("previous_attempts", [])
    require(isinstance(history, list), f"invalid previous_attempts: {job['id']}")
    rows = history + [{k: v for k, v in job.items() if k not in {"registry", "previous_attempts"}}]
    index_unique(rows, lambda a: a.get("attempt", 0), f"attempt {job['id']}")
    actual = []
    for row in rows:
        require(type(row.get("attempt", 0)) is int and row.get("attempt", 0) >= 0,
                f"invalid attempt number: {job['id']}")
        require(row.get("id") == job["id"] and row.get("arm") == job["arm"] and row.get("seed") == job["seed"],
                f"prior attempt identity: {job['id']}")
        require(not row.get("previous_attempts"), "nested attempt history")
        if any(row.get(k) for k in ("world", "preparation_started", "started", "runner_pid")):
            actual.append(row)
        else:
            require(row.get("status") in UNSTARTED, f"terminal attempt lacks attempt evidence: {job['id']}")
    return rows, actual


def closure_guard(health, proof, jobs):
    require(proof.get("version") == "laya-final-accounting-closure-v1", "wrong closure proof version")
    require(not health.get("parse_errors") and not health.get("running_jobs") and
            not any(health.get("active_by_arm", {}).values()), "snapshot has active/unparseable owned work")
    require(proof.get("owned_reservations") == [], "owned reservation evidence absent/nonempty")
    receipt(proof.get("reservation_receipt"), "owned reservations")
    require(set(proof.get("units", {})) == UNITS, "five exact unit proofs required")
    for name, unit in proof["units"].items():
        require(unit.get("query_ok") is True and unit.get("ActiveState") in {"inactive", "failed"} and
                str(unit.get("MainPID")) == "0" and unit.get("cgroup_empty") is True and
                unit.get("processes_empty") is True and bool(unit.get("Result")) and
                "ExecMainStatus" in unit, f"unit closure evidence absent/active: {name}")
        receipt(unit.get("receipt"), name)
        observed = health.get("units", {}).get(name, {})
        require(all(observed.get(k) == unit.get(k) for k in ("ActiveState", "MainPID", "Result", "ExecMainStatus")),
                f"unit proof/snapshot disagreement: {name}")
    proofs = index_unique(proof.get("attempts"), lambda p: (p["job_id"], p["attempt"]), "attempt proof")
    expected, worlds, runs = set(), set(), set()
    for job in jobs:
        rows, actual = attempt_rows(job)
        if len(rows) > 1:
            resolution = proof.get("attempt_resolutions", {}).get(job["id"], {})
            require(resolution.get("basis") == "current_attempt_only" and bool(resolution.get("reason")) and
                    resolution.get("selected_attempt") == job.get("attempt", 0),
                    f"ambiguous retained attempts: {job['id']}")
        for row in actual:
            key = (job["id"], row.get("attempt", 0)); expected.add(key)
            att = proofs.get(key, {})
            require(att.get("registry") == job["registry"] and att.get("job_sha256") == digest(row),
                    f"attempt proof missing/snapshot binding: {key}")
            require(row.get("status") in TERMINAL and row.get("admission_reserved") is False and
                    not row.get("cleanup_error"), f"attempt not terminal/released: {key}")
            times = [row.get(k) for k in ("preparation_started", "cleanup_started", "cleanup_completed")]
            require(all(type(t) in (float, int) and math.isfinite(t) for t in times) and times == sorted(times),
                    f"cleanup chronology absent/invalid: {key}")
            if row.get("started"):
                require("runner_returncode" in row and type(row.get("runner_exited")) in (float, int) and
                        times[0] <= row["started"] <= row["runner_exited"] <= times[1],
                        f"runner exit evidence absent: {key}")
            for kind, event in (("start", "attempt_started"), ("terminal", "attempt_terminal")):
                ref = att.get(kind + "_receipt")
                receipt(ref, f"{key} {kind}")
                require(ref.get("event_id") == f"{kind}:{key[0]}:{key[1]}" and ref.get("event") == event,
                        f"ledger receipt identity mismatch: {key}")
            release = att.get("release", {})
            require(all(release.get(k) is True for k in ("runner_released", "factorio_released", "ports_released")),
                    f"scoped cleanup evidence absent: {key}")
            receipt(release.get("receipt"), f"{key} scoped release")
            if row.get("world"):
                require(row["world"] not in worlds, f"duplicate world attempt ambiguity: {key}")
                worlds.add(row["world"])
            if att.get("run_id"):
                require(att["run_id"] not in runs, f"duplicate run attempt ambiguity: {key}")
                runs.add(att["run_id"])
            require(not att.get("journal") or att.get("run_id"), f"journal requires explicit run_id: {key}")
    require(set(proofs) == expected, "missing/extra actual attempt proofs")
    return proofs


def project_journal(ref, run_id):
    """Stream once, retaining only acceptance/summary fields and exact byte receipts; no model journals."""
    receipt(ref, "clean journal")
    path = Path(ref["path"]).resolve(strict=True)
    accepted, summaries, offset, sha = [], [], 0, hashlib.sha256()
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        for number, raw in enumerate(handle, 1):
            sha.update(raw)
            require(raw.endswith(b"\n"), f"torn journal line: {path}:{number}")
            row = json.loads(raw)
            require(isinstance(row, dict), f"non-object journal line: {path}:{number}")
            if row.get("event") in {"acceptance", "summary"}:
                require(row.get("run_id") == run_id, f"journal run identity ambiguity: {path}:{number}")
                keys = ("event", "run_id", "at", "fresh", "statuses", "checks") if row["event"] == "acceptance" else (
                    "event", "run_id", "accepted", "accepted_ever", "accepted_final", "acceptance", "stop",
                    "failures", "codes", "classifications", "phases_reached", "laya_acceptance", "research_acceptance")
                projected = {k: row[k] for k in keys if k in row}
                projected["receipt"] = {"path": str(path), "line": number, "offset": offset,
                                        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                (accepted if row["event"] == "acceptance" else summaries).append(projected)
            offset += len(raw)
        after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), f"journal changed while reading: {path}")
    require(sha.hexdigest() == ref["sha256"], f"journal hash mismatch: {path}")
    require(len(summaries) <= 1 and sum(r.get("at") == "end" for r in accepted) <= 1,
            f"duplicate terminal journal evidence: {path}")
    require(not any(r.get("at") == "end" for r in accepted[:-1]), f"acceptance after end: {path}")
    return accepted, summaries[0] if summaries else None


def strict_projection(acceptances, summary, provenance, contract):
    """Fresh ever and explicit end only. Parent source bindings supply versions omitted by the runner."""
    result, issues = {}, []
    context_fields = ("source_save_sha256", "mods_sha256", "freeze_sha256", "runtime_revision", "window")
    context_mismatch = [k for k in context_fields if provenance.get(k) is not None and
                        contract.get(k) is not None and provenance[k] != contract[k]]
    context_known = all(bool(provenance.get(k)) and bool(contract.get(k)) for k in context_fields)
    context_known &= all(is_hash(provenance.get(k)) and is_hash(contract.get(k))
                         for k in ("source_save_sha256", "mods_sha256", "freeze_sha256"))
    context_known &= is_hash(provenance.get("baseline_sha256"))
    issues.extend("incompatible_context:" + k for k in context_mismatch)
    if not context_known:
        issues.append("source_baseline_window_context_unverified")
    end = next((r for r in acceptances if r.get("at") == "end"), None)
    if end is None:
        issues.append("final_missing")
    if summary is None:
        issues.append("summary_missing")
    for phase in PHASES:
        mismatches, fresh_values, seen_pass = [], [], False
        for row in acceptances:
            statuses = row.get("statuses")
            error = not isinstance(statuses, dict) or "error" in statuses
            value = (row.get("fresh") or {}).get(phase) if isinstance(row.get("fresh"), dict) and not error else None
            fresh_values.append(value)
            seen_pass |= value == "passed"
            if not error and (statuses.get(phase) == "passed") != seen_pass:
                mismatches.append("latched_status_disagrees_with_fresh_ever")
        passed = any(v == "passed" for v in fresh_values)
        final_value = None if end is None or not isinstance(end.get("statuses"), dict) or "error" in end["statuses"] else (
            end.get("fresh", {}).get(phase) if isinstance(end.get("fresh"), dict) else None)
        if summary:
            for key in ("accepted", "accepted_ever"):
                if not isinstance(summary.get(key), list) or ((phase in summary[key]) != passed):
                    mismatches.append("summary_" + key + "_disagrees_with_fresh_ever")
            sf = summary.get("accepted_final")
            if end and ((final_value in {"passed", "failed"} and
                         (not isinstance(sf, list) or (phase in sf) != (final_value == "passed"))) or
                        (final_value not in {"passed", "failed"} and isinstance(sf, list) and phase in sf)):
                mismatches.append("summary_final_disagrees_with_end")
        binding = provenance.get("predicate_source_sha256")
        version = provenance.get("milestone_versions", {}).get(phase)
        expected_hash = contract.get("predicate_source_sha256")
        explicit = {r.get("checks", {}).get(phase, {}).get("predicate_version") for r in acceptances
                    if isinstance(r.get("checks"), dict) and isinstance(r["checks"].get(phase), dict)} - {None}
        compatible = (is_hash(binding) and is_hash(expected_hash) and binding == expected_hash and
                      version == VERSIONS[phase] and contract.get("milestone_versions", {}).get(phase) == VERSIONS[phase])
        incompatible = ((is_hash(binding) and is_hash(expected_hash) and binding != expected_hash) or
                        (version is not None and version != VERSIONS[phase]) or bool(explicit - {VERSIONS[phase]}) or
                        (contract.get("milestone_versions", {}).get(phase) is not None and
                         contract["milestone_versions"][phase] != VERSIONS[phase]) or
                        bool(context_mismatch) or contract.get("_transfer_freeze_mismatch", False))
        if not acceptances:
            ever, final = "unobserved", "unknown"
        elif incompatible:
            ever = final = "incompatible"
        elif not compatible or not context_known or mismatches:
            ever = final = "unknown"
        else:
            ever = ("passed" if passed else "failed" if end is not None and fresh_values and
                    all(v == "failed" for v in fresh_values) else "unknown")
            final = final_value if final_value in {"passed", "failed"} else "unknown"
        result[phase] = {"ever": ever, "final": final, "predicate_version": version,
                         "version_basis": "incompatible" if incompatible else "parent_source_binding" if compatible else "unknown",
                         "explicit_check_versions": sorted(explicit), "crosscheck_errors": sorted(set(mismatches))}
        issues.extend(f"{phase}:{e}" for e in set(mismatches))
        if acceptances and (incompatible or not compatible):
            issues.append(phase + (":incompatible_predicate" if incompatible else ":predicate_binding_missing"))
    return {"strict": result, "final_missing": end is None, "issues": sorted(set(issues)),
            "acceptance_projection": acceptances, "summary_projection": summary}


def endpoint_counts(values):
    counts = {s: values.count(s) for s in STATES}
    known = counts["passed"] + counts["failed"]
    return dict(counts, planned=len(values), known=known,
                passed_planned={"numerator": counts["passed"], "denominator": len(values)},
                passed_known={"numerator": counts["passed"], "denominator": known})


def paired_cells(left, right):
    require(len(left) == len(right), "pair length mismatch")
    cells = {a + "/" + b: 0 for a in STATES for b in STATES}
    for a, b in zip(left, right):
        cells[a + "/" + b] += 1
    up, down = cells["failed/passed"], cells["passed/failed"]
    n = up + down
    p = min(Fraction(1), Fraction(2 * sum(math.comb(n, i) for i in range(min(up, down) + 1)), 2 ** n))
    known = sum(cells[a + "/" + b] for a in ("passed", "failed") for b in ("passed", "failed"))
    return {"planned_pairs": len(left), "observed_pairs": known, "unknown_pairs": len(left) - known,
            "cells_left_right": cells, "right_only": up, "left_only": down,
            "exact_mcnemar_two_sided": {"discordant_pairs": n, "numerator": p.numerator,
                                         "denominator": p.denominator, "p": float(p)}}


def epoch(value):
    if type(value) in (int, float):
        require(math.isfinite(value), "nonfinite timestamp")
        return value
    time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(time.tzinfo is not None, "capacity timestamps need an explicit timezone")
    return time.timestamp()


def capacity_period(row, periods):
    if not row.get("preparation_started"):
        return "unstarted"
    start, end = row["preparation_started"], row["cleanup_completed"]
    overlaps = [(name, lo, hi) for name, lo, hi in periods if start < hi and end > lo]
    if len(overlaps) == 1 and overlaps[0][1] <= start <= end <= overlaps[0][2]:
        return overlaps[0][0]
    return "mixed" if len(overlaps) > 1 else "period-unknown"


def aggregate(rows):
    return {"planned_jobs": len(rows), "statuses": dict(Counter(r["status"] for r in rows)),
            "strict": {p + "_" + endpoint: endpoint_counts([r["strict"][p][endpoint] for r in rows])
                       for p in PHASES for endpoint in ("ever", "final")}}


def fault_projection(supplement, attempts, jobs):
    """Only parent-certified, already joined events; never add source and clean stream counts."""
    if supplement.get("status") != "verified":
        return {"status": "unverified", "categories": {c: {"events": None, "affected_jobs": None}
                                                         for c in FAULT_CATEGORIES}, "parent_supplement": supplement}
    receipt(supplement.get("receipt"), "fault certification")
    require(set(supplement.get("covered_attempts", [])) == {f"{j}:{a}" for j, a in attempts},
            "fault coverage omits retained attempts")
    events = supplement.get("events")
    index_unique(events, lambda e: e["event_id"], "fault event")
    identities, groups = set(), {}
    for event in events:
        key = (event["job_id"], event["attempt"])
        require(key in attempts and event.get("category") in FAULT_CATEGORIES, "unknown fault attempt/category")
        require(event.get("run_id") == attempts[key].get("run_id"), "fault run identity mismatch")
        identity = (key, event.get("run_id"), event.get("index"), event["category"], event.get("code"))
        require(identity not in identities, "duplicate joined fault ambiguity")
        identities.add(identity)
        receipt(event.get("receipt"), "joined fault")
        job = jobs[event["job_id"]]
        for group in ("all", "arm:" + job["arm"], "ownership:" + job["ownership_stratum"],
                      "arm_ownership:" + job["arm"] + ":" + job["ownership_stratum"],
                      "capacity:" + attempts[key]["capacity_period"]):
            groups.setdefault(group, []).append(event)
    def counts(rows):
        return {c: {"events": sum(e["category"] == c for e in rows),
                    "affected_jobs": len({e["job_id"] for e in rows if e["category"] == c}),
                    "codes": dict(Counter(e.get("code", "unspecified") for e in rows if e["category"] == c)),
                    "classifications": dict(Counter(e.get("classification", "unclassified") for e in rows if e["category"] == c))}
                for c in FAULT_CATEGORIES}
    return {"status": "parent_verified_not_reaudited", "categories": counts(events),
            "groups": {g: counts(e) for g, e in groups.items()}, "parent_supplement": supplement}


def build_report(snapshot_path, plan_path, proof_path):
    """All checks and reads finish before any derived output directory is created."""
    proof, proof_ref = load_json(proof_path)
    health, health_ref = load_json(snapshot_path, proof.get("snapshot_sha256"))
    plan, plan_ref = load_json(plan_path, proof.get("transfer_plan_sha256"))
    require(is_hash(proof.get("snapshot_sha256")) and is_hash(proof.get("transfer_plan_sha256")),
            "closure must bind both immutable inputs")
    registries, registry_refs = {}, {}
    for registry, ref in proof.get("registry_snapshots", {}).items():
        receipt(ref, registry)
        registries[registry], registry_refs[registry] = load_json(ref["path"], ref["sha256"])
        require(health.get("registry_metadata", {}).get(registry, {}).get("sha256") == ref["sha256"],
                f"registry hash differs from host-health snapshot: {registry}")
    jobs, admin = guarded_union(health, plan, registries)
    attempts = closure_guard(health, proof, jobs)
    contract = proof.get("predicate_contract", {})
    if contract.get("freeze_sha256") and contract["freeze_sha256"] != plan.get("source_freeze_sha256"):
        contract = dict(contract, _transfer_freeze_mismatch=True)
    periods = []
    for period in proof.get("capacity_periods", []):
        receipt(period.get("receipt"), "capacity interval")
        lo, hi = epoch(period["start_utc"]), epoch(period["end_utc"])
        require(lo < hi and period["name"] not in {"mixed", "period-unknown", "unstarted"}, "invalid capacity interval")
        periods.append((period["name"], lo, hi))
    periods.sort(key=lambda p: p[1])
    require(len({p[0] for p in periods}) == len(periods) and
            all(a[2] <= b[1] for a, b in zip(periods, periods[1:])), "overlapping/duplicate capacity intervals")
    projected, gaps, used_journals = [], [], set()
    for name, unit in proof["units"].items():
        if unit["ActiveState"] == "failed" or unit["Result"] != "success" or str(unit["ExecMainStatus"]) != "0":
            gaps.append("unit_non_success:" + name)
    for job in jobs:
        projections = []
        for row in attempt_rows(job)[1]:
            key = (job["id"], row.get("attempt", 0)); att = attempts[key]
            acceptances, summary = [], None
            if att.get("journal"):
                path = str(Path(att["journal"]["path"]).resolve())
                require(path not in used_journals, "journal reused across attempts")
                used_journals.add(path)
                acceptances, summary = project_journal(att["journal"], att["run_id"])
            projection = strict_projection(acceptances, summary, att.get("provenance", {}), contract)
            cached_mismatches = [k for k in ("accepted", "accepted_ever", "accepted_final")
                                 if summary and k in row and row[k] != summary.get(k)]
            if cached_mismatches:
                projection["issues"].extend("snapshot_summary_disagrees:" + k for k in cached_mismatches)
                for endpoint in projection["strict"].values():
                    for name in ("ever", "final"):
                        if endpoint[name] != "incompatible":
                            endpoint[name] = "unknown"
            period = capacity_period(row, periods)
            attempts[key] = dict(att, capacity_period=period)
            projection.update(attempt=row.get("attempt", 0), capacity_period=period,
                              parent_proof=att, snapshot=row)
            projections.append(projection)
            gaps.extend(f"{job['id']}:{row.get('attempt', 0)}:{i}" for i in projection["issues"])
            if period == "period-unknown":
                gaps.append(f"{job['id']}:capacity_period_unverified")
            prov = att.get("provenance", {})
            for field in ("source_save_sha256", "baseline_sha256", "freeze_sha256", "mods_sha256"):
                if not is_hash(prov.get(field)):
                    gaps.append(f"{job['id']}:{field}_missing")
            if not prov.get("window") or not prov.get("runtime_revision"):
                gaps.append(f"{job['id']}:window_or_runtime_revision_missing")
        selected = next((p for p in projections if p["attempt"] == job.get("attempt", 0)), None)
        strict = selected["strict"] if selected else {p: {"ever": "unobserved", "final": "unobserved"} for p in PHASES}
        projected.append(dict(job, strict=strict, ownership_stratum="original7" if job["id"] in ORIGINAL else "transferred113",
                              capacity_period=selected["capacity_period"] if selected else "unstarted",
                              attempt_projections=projections,
                              separate_endpoints={k: job.get(k) for k in ("phases", "phase_times", "laya_acceptance", "research_acceptance")}))
    comparisons = proof.get("comparisons")
    require(isinstance(comparisons, list) and comparisons, "explicit planned comparisons required")
    pairs, seen = {}, set()
    by_arm = {a: {j["seed"]: j for j in projected if j["arm"] == a} for a in ARMS}
    for pair in comparisons:
        require(isinstance(pair, list) and len(pair) == 2 and all(a in ARMS for a in pair) and pair[0] != pair[1],
                "invalid declared comparison")
        left, right = pair
        require(frozenset(pair) not in seen, "duplicate declared comparison")
        seen.add(frozenset(pair))
        pairs[right + "_vs_" + left] = {p + "_" + end: paired_cells(
            [by_arm[left][s]["strict"][p][end] for s in SEEDS],
            [by_arm[right][s]["strict"][p][end] for s in SEEDS]) for p in PHASES for end in ("ever", "final")}
    supplements = proof.get("supplements", {})
    for name in SUPPLEMENTS:
        item = supplements.get(name, {})
        if item.get("status") != "verified":
            gaps.append("required_supplement:" + name)
        else:
            receipt(item.get("receipt"), name)
    faults = fault_projection(supplements.get("faults", {}), attempts, {j["id"]: j for j in projected})
    if faults["status"] == "unverified":
        gaps.append("required_supplement:faults")
    if not is_hash(contract.get("predicate_source_sha256")) or contract.get("milestone_versions") != VERSIONS:
        gaps.append("predicate_contract_missing_or_incompatible")
    return {"version": "laya-final-accounting-derived-v1", "status": "blocked" if gaps else "projection_complete_parent_evidence_required",
            "adoption_verdict": None, "closure_basis": "parent-provided pinned attestations; no lifecycle queries or raw join recertification",
            "endpoint_basis": "current recorded attempt; retained attempts projected separately; no selection by outcome",
            "provenance": {"snapshot": health_ref, "transfer_plan": plan_ref, "closure_proof": proof_ref,
                           "registry_snapshots": registry_refs, "predicate_contract": contract},
            "closure_proof": proof, "health_context": {k: health.get(k) for k in (
                "checked_utc", "host_occupied", "other_reserved_jobs", "host_gate", "issues", "units")},
            "overall": aggregate(projected), "arms": {a: aggregate([j for j in projected if j["arm"] == a]) for a in ARMS},
            "ownership": {s: aggregate([j for j in projected if j["ownership_stratum"] == s]) for s in ("original7", "transferred113")},
            "ownership_by_arm": {a: {s: aggregate([j for j in projected if j["arm"] == a and j["ownership_stratum"] == s])
                                      for s in ("original7", "transferred113")} for a in ARMS},
            "capacity": {s: aggregate([j for j in projected if j["capacity_period"] == s])
                         for s in sorted({j["capacity_period"] for j in projected})},
            "capacity_note": "Attempt preparation-to-cleanup UTC intervals only; crossing attempts mixed. No per-choice UTC, mtime/tick conversion, or causal quiet-period attribution.",
            "comparisons": comparisons, "undeclared_comparisons": [list(p) for p in itertools.combinations(ARMS, 2) if frozenset(p) not in seen],
            "paired": pairs, "faults": faults,
            "required_supplements": {name: supplements.get(name, {"status": "unverified"}) for name in SUPPLEMENTS},
            "material_gaps": sorted(set(gaps)), "jobs": projected,
            "administrative_transfer_copies": {"additional_trials": 0, "count": 113, "jobs": admin}}


def write_report(report, output):
    output = Path(output).resolve()
    inputs = [report["provenance"][key]["path"] for key in ("snapshot", "transfer_plan", "closure_proof")]
    inputs += [r["path"] for r in report["provenance"]["registry_snapshots"].values()]
    protected = list(report["provenance"]["registry_snapshots"])
    for job in report["jobs"]:
        for att in job["attempt_projections"]:
            if att["snapshot"].get("world"):
                protected.append(att["snapshot"]["world"])
            ref = att["parent_proof"].get("journal")
            if ref:
                inputs.append(ref["path"])
                protected.append(str(Path(ref["path"]).resolve()))
    require(not any(output.is_relative_to(Path(p).resolve()) for p in protected), "output is inside authoritative registry/world/evidence")
    require(not any(Path(p).resolve().is_relative_to(output) for p in inputs), "output contains an input")
    require(not output.exists(), "derived output directory must be new")
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    lines = ["# Derived logical120 final projection", "", f"Status: {report['status']}. No adoption verdict.", "",
             "Closure and raw-join evidence remain parent attestations. All planned jobs and retained attempts are preserved.", "",
             "| Arm | Planned | C6 ever / planned | C6 final / planned |", "|---|---:|---:|---:|"]
    for arm, data in report["arms"].items():
        strict = data["strict"]
        lines.append(f"| {arm} | {data['planned_jobs']} | {strict['C6_ever']['passed']}/30 | {strict['C6_final']['passed']}/30 |")
    lines += ["", "Unknown, unobserved and incompatible cells, exact pairs, receipt hashes and required supplements are in report.json.",
              "", "Material gaps:", ""] + ["- " + gap for gap in report["material_gaps"]]
    output.mkdir()
    (output / "report.json").write_text(payload)
    (output / "report.md").write_text("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--transfer-plan", required=True, type=Path)
    parser.add_argument("--closure-proof", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = build_report(args.snapshot, args.transfer_plan, args.closure_proof)
        write_report(report, args.output_dir)
    except (GuardError, OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"refused: {error}\n")
    print(json.dumps({"status": report["status"], "planned": 120, "material_gaps": len(report["material_gaps"]),
                      "output": str(args.output_dir)}))
    return 3 if report["material_gaps"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
