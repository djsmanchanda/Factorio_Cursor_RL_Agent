# Path: orchestrator/work_diagnosis.py
# Purpose: Explain recorded deterministic work dependencies without executing recovery.

"""Pure, bounded diagnosis of explicitly supplied episode evidence.

No ledger instances, clients, clocks, scheduler, or runtime globals are used.
An undated journal describes recorded intent, never current factory readiness.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque

from orchestrator.bootstrap_district import BootstrapDistrictState, BootstrapLifecycleError
from orchestrator.material_reservations import MaterialProject, MaterialReservationError

MAX_ARTIFACTS = 24
MAX_RECORDS = 256
MAX_EDGES = 96
MAX_REPORT_CHARS = 12000
KINDS = {"work", "materials", "district", "loans", "measurements"}
METRICS = {"crafts_completed", "items_delivered", "entities_built", "pending_ghosts"}


def _name(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise ValueError(f"{label} must be a nonempty string of at most 160 characters")
    return value


def _count(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _records(value, label):
    if not isinstance(value, list) or len(value) > MAX_RECORDS:
        raise ValueError(f"{label} must be a list of at most {MAX_RECORDS} records")
    return value


def _work_id(value):
    value = _name(value, "work ID")
    if not value.startswith(("item:", "project:", "district:", "loan:")) or not value.split(":", 1)[1]:
        raise ValueError("work ID must start with item:, project:, district:, or loan:")
    return value


def _pointer(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def _project(kind, payload):
    """Validate one artifact completely before contributing any graph facts."""
    edges, findings, samples = [], [], []

    def edge(parent, child, reason, pointer):
        edges.append({"parent": _work_id(parent), "child": _work_id(child),
                      "reason": reason[:240], "pointer": pointer})

    def finding(node, code, message, pointer, next_observation, candidate=None, preconditions=None):
        findings.append({"work_id": _work_id(node), "code": code, "message": message[:500],
                         "pointer": pointer, "next_observation": next_observation,
                         "candidate": candidate, "preconditions": preconditions or []})

    if kind == "work":
        raw = _object(payload["edges"], "work edges")
        if sum(len(_object(children, "work children")) for children in raw.values()) > MAX_EDGES:
            raise ValueError("work edge limit exceeded")
        for parent, children in sorted(raw.items()):
            for child, reason in sorted(children.items()):
                edge(f"item:{parent}", f"item:{child}", _name(reason, "dependency reason"),
                     f"/edges/{_pointer(parent)}/{_pointer(child)}")
        waits = _object(payload.get("waits", {}), "stock waits")
        if len(waits) > MAX_RECORDS:
            raise ValueError("stock wait limit exceeded")
        for item, record in sorted(waits.items()):
            target = _count(record["target"], "stock target")
            best = _count(record["best"], "best stock")
            finding(f"item:{item}", "stock_wait", f"Recorded stock wait: best {best}, target {target}.",
                    f"/waits/{_pointer(item)}", "Measure transferable stock for this obligation and its delivery path.")
    elif kind == "materials":
        projects = [MaterialProject.from_dict(p) for p in _records(payload["projects"], "projects")]
        if len({p.project_id for p in projects}) != len(projects):
            raise ValueError("duplicate material project IDs")
        for index, p in enumerate(projects):
            node, pointer = f"project:{p.project_id}", f"/projects/{index}"
            if p.state in {"completed", "failed"}:
                continue
            if p.target_item:
                edge(f"item:{p.target_item}", node, "recorded construction project", pointer + "/target_item")
            # Nonholding construction bills have been consumed, not lost supply.
            if p.state != "constructing" or p.hold_until_producing:
                for item, required in sorted(p.required.items()):
                    missing = required - p.reserved.get(item, 0)
                    if missing <= 0:
                        continue
                    edge(node, f"item:{item}", f"bill short by {missing}", pointer + "/required/" + _pointer(item))
                    holders = sorted(q.project_id for q in projects if q.project_id != p.project_id
                                     and q.state not in {"completed", "failed"} and q.reserved.get(item, 0))
                    source = p.source_producers.get(item)
                    finding(node, "material_wait",
                            f"{item}: required {required}, reserved {p.reserved.get(item, 0)}, short {missing}; "
                            f"recorded source {source or 'unknown'}, other reservation owners {holders[:4]}. "
                            "Reservations are accounting evidence, not proven delivery.", pointer,
                            f"Verify {missing} {item} become allocatable to {p.project_id} in its network.",
                            "supply_existing_project",
                            ["Fresh exact bill and reservation ownership", "Reachable actual stock; no WIP double counting"])
            if p.state == "constructing":
                finding(node, "construction_wait", "Project is recorded as constructing; completion is not observed here.",
                        pointer, "Compare owned remaining ghosts and exact executor receipts at two ticks.",
                        "reconcile_owned_project", ["Persisted plan ownership and supported layout adapter", "Fresh bill, power and bot coverage"])
    elif kind == "district":
        state = BootstrapDistrictState.from_json(json.dumps(payload))
        node = f"district:{state.district_id}"
        if state.lifecycle_state != "released":
            edge(f"item:{state.recipe}", node, "recorded replacement lifecycle", "/lifecycle_state")
            expected = {"pioneer": "owned replacement submission", "provisioning": "owned replacement submission",
                        "validating": "first delivered replacement output", "retiring": "owned pioneer absence"}[state.lifecycle_state]
            finding(node, "lifecycle_wait",
                    f"{state.recipe}: {state.lifecycle_state}; submitted={state.replacement_submitted}; "
                    f"historical measured output={state.measured_output_count}. Expected: {expected}.",
                    "/lifecycle_state", f"Verify {expected} against the persisted district geometry.",
                    "reconcile_owned_district", ["Exact persisted geometry and ownership", "Supported recovery; preserve starter until delivered output"])
    elif kind == "loans":
        seen = set()
        for index, loan in enumerate(_records(payload["loans"], "loans")):
            node = "loan:" + _name(loan["loan_id"], "loan ID")
            if node in seen:
                raise ValueError("duplicate loan IDs")
            seen.add(node)
            original = _name(loan["original_recipe"], "original recipe")
            current = _name(loan["current_recipe"], "current recipe")
            target = _name(loan["target_item"], "loan target")
            if current == original:
                continue
            pointer = f"/loans/{index}"
            edge(f"item:{original}", node, "producer recipe displaced by recorded loan", pointer)
            finding(node, "producer_displaced",
                    f"Cell {loan['loan_id']} originally made {original}, currently {current}, borrowed for {target}. "
                    "Other deliverable producers and the borrower's progress are unknown.", pointer,
                    f"Measure {original} delivery from other producers and this cell's borrower output.",
                    "restore_owned_loan",
                    ["Verified cell and lender ownership", "Borrower debt and restoration obligation reconciled", "Controller chooses restoration"])
    else:
        tick = _count(payload["tick"], "measurement tick")
        for index, sample in enumerate(_records(payload["measurements"], "measurements")):
            metric = sample["metric"]
            if metric not in METRICS:
                raise ValueError("unsupported progress metric; stock totals and queue revisions are not progress")
            samples.append({"work_id": _work_id(sample["work_id"]), "metric": metric,
                            "counter_id": _name(sample["counter_id"], "counter ID"),
                            "value": _count(sample["value"], "counter value"), "tick": tick,
                            "pointer": f"/measurements/{index}"})
        keys = [(s["work_id"], s["metric"], s["counter_id"]) for s in samples]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate measurement keys at one tick")
    if len(edges) > MAX_EDGES:
        raise ValueError("artifact graph limit exceeded")
    return edges, findings, samples


def diagnose(bundle: dict) -> dict:
    """Return a deterministic advisory packet; malformed artifacts stay unknown.

    Envelope errors raise ValueError. Artifact rejection is local and explicit.
    Missing facts, stale ticks, cycles and unchanged counters never grant action
    authority or imply that a deadline proves a deadlock.
    """
    if bundle.get("version") != 1:
        raise ValueError("unsupported diagnostic bundle version")
    identity = _object(bundle.get("identity"), "identity")
    identity = {k: _name(identity.get(k), k) for k in ("episode_id", "surface", "force", "source_id")}
    objective = _name(bundle.get("objective"), "objective")
    focus = _work_id(bundle.get("focus"))
    provenance = _object(bundle.get("provenance", {}), "provenance")
    provenance = {k: _name(v, k) for k, v in provenance.items()
                  if k in {"run_id", "code_revision", "mod_revision", "save_sha256"}}
    now = bundle.get("as_of_tick")
    if now is not None:
        _count(now, "as_of_tick")
    max_age = _count(bundle.get("max_age_ticks", 600), "max_age_ticks")
    artifacts = _records(bundle.get("artifacts"), "artifacts")
    if len(artifacts) > MAX_ARTIFACTS:
        raise ValueError("artifact limit exceeded")

    inputs, edges, findings, samples, unknowns = [], [], [], [], []
    seen, singleton = set(), {}
    for artifact in artifacts:
        _object(artifact, "artifact")
        ref = _name(artifact.get("ref"), "artifact reference")
        if ref in seen:
            raise ValueError("duplicate artifact references")
        seen.add(ref)
        kind = artifact.get("kind")
        row = {"ref": ref, "kind": str(kind)[:40], "status": "rejected", "tick": None}
        if "sha256" in artifact:
            row["sha256"] = str(artifact["sha256"])[:64]
        inputs.append(row)
        try:
            if artifact.get("load_error"):
                raise ValueError(artifact["load_error"])
            if kind not in KINDS:
                raise ValueError("unsupported artifact kind")
            if artifact.get("source_id") != identity["source_id"]:
                raise ValueError("source identity mismatch")
            payload = _object(artifact.get("payload"), "artifact payload")
            # District state version 2 adds retained starter providers while
            # keeping the same diagnosis fields; other artifacts stay version 1.
            supported_versions = {1, 2} if kind == "district" else {1}
            if payload.get("version") not in supported_versions:
                raise ValueError("unsupported artifact version")
            for key in ("episode_id", "surface", "force", "source_id"):
                if key not in payload and key in {"surface", "force", "source_id"} and kind == "work":
                    continue  # Native work journals only record episode identity.
                if key == "source_id" and key not in payload and kind in {"materials", "district"}:
                    continue  # Collector must declare source attribution in wrapper.
                if payload.get(key) != identity[key]:
                    raise ValueError(f"{key} mismatch or missing")
            tick = artifact.get("observation_tick")
            if tick is not None:
                _count(tick, "observation tick")
            if "tick" in payload and payload["tick"] != tick:
                raise ValueError("payload and wrapper ticks differ")
            if now is not None and tick is not None and tick > now:
                raise ValueError("observation is later than as_of_tick")
            freshness = "undated" if now is None or tick is None else ("fresh" if now - tick <= max_age else "stale")
            projected = _project(kind, payload)
            key = (kind, payload.get("district_id"))
            if kind != "measurements" and key in singleton:
                prior = singleton[key]
                if prior is not None:
                    for previous in inputs:
                        if previous["ref"] == prior:
                            previous.update(status="rejected", reason="ambiguous duplicate journal kind/identity")
                    for facts in (edges, findings, samples):
                        facts[:] = [f for f in facts if not f["evidence"].startswith(prior + "#")]
                    singleton[key] = None
                raise ValueError("ambiguous duplicate journal kind/identity; supply one snapshot")
            if len(edges) + len(projected[0]) > MAX_EDGES:
                raise ValueError("combined graph limit exceeded; narrow the bundle")
            singleton[key] = ref
            row.update(status="accepted", tick=tick, freshness=freshness)
            if freshness != "fresh":
                unknowns.append(f"{ref}: {freshness} evidence; current readiness is unknown.")
            if kind == "work":
                unknowns.append(f"{ref}: surface/force/source are collector attribution, absent from native journal.")
            for group, destination in zip(projected, (edges, findings, samples)):
                for fact in group:
                    fact["evidence"] = ref + "#" + fact.pop("pointer")
                    fact["freshness"] = freshness
                    destination.append(fact)
        except (KeyError, TypeError, ValueError, AttributeError, BootstrapLifecycleError, MaterialReservationError) as error:
            row["reason"] = str(error)[:320]
            unknowns.append(f"{ref}: rejected ({row['reason']}).")

    graph = defaultdict(list)
    for edge in sorted(edges, key=lambda e: (e["parent"], e["child"], e["evidence"])):
        graph[edge["parent"]].append(edge)
    reachable, todo = {focus}, deque([focus])
    while todo:
        for edge in graph[todo.popleft()]:
            if edge["child"] not in reachable:
                reachable.add(edge["child"])
                todo.append(edge["child"])
    relevant_edges = [e for e in edges if e["parent"] in reachable]
    relevant = [f for f in findings if f["work_id"] in reachable]

    cycles, visited = [], set()

    def walk(node, path):
        if node in path:
            cycles.append(path[path.index(node):] + [node])
            return
        if node in visited:
            return
        visited.add(node)
        for edge in graph[node]:
            walk(edge["child"], path + [node])

    walk(focus, [])
    groups = defaultdict(list)
    for sample in samples:
        if sample["work_id"] in reachable:
            groups[(sample["work_id"], sample["metric"], sample["counter_id"])].append(sample)
    progress = []
    for key, values in sorted(groups.items()):
        ordered = sorted(values, key=lambda s: (s["tick"], s["evidence"]))
        if len(ordered) < 2:
            continue
        before, after = ordered[-2:]
        if before["tick"] >= after["tick"] or any(s["freshness"] != "fresh" for s in (before, after)):
            unknowns.append(f"{key[0]}: progress needs increasing ticks and two fresh samples.")
            continue
        delta = after["value"] - before["value"]
        improving = delta < 0 if key[1] == "pending_ghosts" else delta > 0
        reset = delta < 0 and key[1] != "pending_ghosts"
        state = "advancing" if improving else ("counter_reset" if reset else "unchanged")
        progress.append({"work_id": key[0], "metric": key[1], "counter_id": key[2], "state": state,
                         "from_tick": before["tick"], "to_tick": after["tick"], "delta": delta,
                         "evidence": [before["evidence"], after["evidence"]]})
        if reset:
            unknowns.append(f"{key[0]}: counter reset; provide a new counter_id for the replacement generation.")
    direct = any(p["state"] == "advancing" and p["work_id"] == focus for p in progress)
    codes = {f["code"] for f in relevant}
    status = ("progressing" if direct else "recorded_dependency_cycle" if cycles else
              "producer_displaced" if "producer_displaced" in codes else
              "material_wait" if "material_wait" in codes else
              "recorded_wait" if relevant or relevant_edges else "unknown")
    if not progress:
        unknowns.append("No comparable relevant counters; neither elapsed time nor stock growth proves stagnation or throughput.")
    unknowns.append("Graph covers supplied recorded obligations only; missing edges and alternative producers remain unknown.")
    lead = next((f for f in relevant if f["code"] == status), relevant[0] if relevant else None)
    next_observation = ("Resample the advancing obligation; preserve hard run budgets." if direct else
                        "Verify each recorded cycle edge against current reservations, delivery and alternative producers." if cycles else
                        lead["next_observation"] if lead else
                        f"Collect scoped ownership, bill, machine/input/output, power and delivery evidence for {focus}.")
    report = {"version": 1, "identity": identity, "objective": objective, "focus": focus,
              "as_of_tick": now, "provenance": provenance, "status": status, "authority": "advisory_only",
              "next_observation": next_observation, "inputs": inputs,
              "edges": relevant_edges, "cycles": cycles, "findings": relevant,
              "progress": progress, "unknowns": unknowns, "omitted": {}}
    # Remove complete records, never slice JSON or conceal omitted evidence.
    for field in ("edges", "findings", "progress", "unknowns", "inputs", "cycles"):
        while len(json.dumps(report, sort_keys=True)) > MAX_REPORT_CHARS - 1 and report[field]:
            report[field].pop()
            report["omitted"][field] = report["omitted"].get(field, 0) + 1
    return report


def render_markdown(report: dict) -> str:
    """Bounded human view; the JSON packet retains machine-readable structure."""
    rows = ["# Deterministic work diagnosis", "", f"Status: {report['status']} (advisory only)",
            f"Episode: {report['identity']['episode_id']}; source: {report['identity']['source_id']}",
            f"Scope: {report['identity']['surface']}/{report['identity']['force']}; as-of tick: {report['as_of_tick']}",
            f"Objective: {report['objective']}; focus: {report['focus']}", "", "Next observation: " + report["next_observation"]]
    rows.extend(["", "Declared provenance: " + json.dumps(report["provenance"], sort_keys=True)])
    for finding in report["findings"]:
        rows.extend(["", f"- {finding['code']}: {finding['message']}",
                     f"  Evidence: {finding['evidence']} ({finding['freshness']})"])
        if finding["candidate"]:
            rows.append(f"  Candidate for controller review: {finding['candidate']}; requires " + "; ".join(finding["preconditions"]))
    for cycle in report["cycles"]:
        rows.extend(["", "Recorded cycle (verify current edges): " + " -> ".join(cycle)])
    for p in report["progress"]:
        rows.extend(["", f"{p['work_id']}: {p['metric']} {p['state']}, delta={p['delta']}, ticks {p['from_tick']}->{p['to_tick']}",
                     "Evidence: " + ", ".join(p["evidence"])])
    rows.extend(["", "Unknowns:", ""] + ["- " + u for u in report["unknowns"]])
    rows.extend(["", "Inputs:", ""] + ["- " + json.dumps(i, sort_keys=True) for i in report["inputs"]])
    rows.extend(["", "Omitted records: " + json.dumps(report["omitted"], sort_keys=True)])
    text = "\n".join(rows) + "\n"
    if len(text) > MAX_REPORT_CHARS:
        text = text[:MAX_REPORT_CHARS - 80] + "\n[Markdown truncated; use JSON and cited raw evidence.]\n"
    return text
