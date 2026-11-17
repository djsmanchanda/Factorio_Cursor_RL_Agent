# Path: training/laya_operation_outcome.py | Purpose: Reconcile operation evidence and explicit accounting denominators across runners.
"""Receipt states describe operation postconditions, never overall mission success."""
from collections import Counter
from collections.abc import Mapping
import json
from pathlib import Path
import re

VERSION = 1
STATES = ("unknown", "deferred", "prerequisite_advanced", "submitted", "constructing", "delivered", "producing", "completed", "fault")
EVIDENCE_FIELDS = ("ok", "code", "classification", "state", "status", "construction", "error_type")
_TRANSACTION = re.compile(r"transaction-[0-9a-f]{32}\.json")


def normalize(receipt, operation=None, transaction_id=None):
    r = receipt if isinstance(receipt, Mapping) else {}
    code, state, status = r.get("code"), r.get("state"), r.get("status")
    result = "unknown"
    if r.get("error_type") or code == "tool_error" or r.get("classification") == "bug":
        result = "fault"
    elif code == "prerequisite_advanced":
        result = "prerequisite_advanced"
    elif (code in ("material_shortage", "temporary_mall_batch", "production_prerequisite_deferred")
          or state in ("deferred", "waiting", "supply_wait")
          or r.get("classification") in ("deferred", "supply")):
        result = "deferred"
    elif status == "construction_pending" or state == "constructing":
        result = "constructing"
    elif code in ("submitted", "seed_committed") or status in ("pending", "submitting", "submission_received", "submission_recovered"):
        result = "submitted"
    elif code in ("producing", "seed_producing") or state == "producing":
        result = "producing"
    elif code == "done" and r.get("ok") is True:
        result = "completed"
    elif status == "observed" and r.get("ok") is not False:
        construction = r.get("construction")
        if (isinstance(construction, Mapping)
                and all(type(construction.get(k)) is int and construction[k] == 0 for k in ("pending", "missing"))):
            result = "delivered"
    return {"version": VERSION, "state": result,
            "cause": code or r.get("error_type") or (state if state in ("deferred", "waiting", "supply_wait") else None),
            "operation": operation or r.get("operation"),
            "transaction_id": transaction_id or r.get("transaction_id"), "scope": "operation",
            "evidence": {k: r[k] for k in EVIDENCE_FIELDS if k in r}}


def count(outcomes):
    outcomes = list(outcomes)
    counts = Counter(o.get("state", "unknown") if o.get("state") in STATES else "unknown" for o in outcomes)
    return {"denominator": "observed_decisions", "observed_decisions": len(outcomes),
            "states": {state: counts[state] for state in STATES}}


def _option(row):
    choice = row.get("choice")
    choice = choice if isinstance(choice, Mapping) else {}
    options = row.get("options") or ()
    selected = next((o for o in options if isinstance(o, Mapping)
                     and o.get("option_id") == choice.get("option_id")), {}) if isinstance(options, (list, tuple)) else {}
    return {**selected, **choice}


def decision_outcome(row):
    """Validate an additive envelope against both its raw and enclosed evidence."""
    raw = row.get("outcome")
    raw = raw if isinstance(raw, Mapping) else {}
    operation = raw.get("operation") or _option(row).get("family")
    envelope = normalize(raw, operation)
    candidate = raw.get("typed_outcome") or row.get("typed_outcome")
    if (isinstance(candidate, Mapping) and type(candidate.get("version")) is int
            and candidate["version"] == VERSION and candidate.get("scope") == "operation"
            and isinstance(candidate.get("evidence"), Mapping)):
        checked = normalize(candidate["evidence"], candidate.get("operation"), candidate.get("transaction_id"))
        identity_agrees = all(raw.get(k) in (None, candidate.get(k)) for k in ("operation", "transaction_id"))
        if (identity_agrees and checked["state"] == candidate.get("state")
                and (operation is None or candidate.get("operation") in (None, operation))
                and all(raw[k] == value for k, value in candidate["evidence"].items() if k in raw)
                and envelope["state"] in ("unknown", checked["state"])):
            envelope = checked
            envelope["operation"] = envelope["operation"] or operation
    return envelope


def reconcile_records(records, *, receipt_loader=None, observations=()):
    """One final state per recorded decision; join only explicit recorded identities.

    Operation observation events use operation_id and are never counted as new
    decisions. A construction receipt establishes delivery, not mission success.
    """
    decisions, by_key, duplicate_count, conflicts = [], {}, 0, set()
    updates = list(observations)
    for value in records:
        row = value if isinstance(value, Mapping) else {"outcome": {}}
        if row.get("event") == "operation_observation":
            updates.append(row)
            continue
        run, index = row.get("run_id"), row.get("index")
        key = (run, index) if isinstance(run, str) and type(index) is int and index >= 0 else None
        if key is not None and key in by_key:
            duplicate_count += 1
            if decisions[by_key[key]] != row:
                conflicts.add(by_key[key])
            continue
        if key is not None:
            by_key[key] = len(decisions)
        decisions.append(row)
    latest = {}
    unmatched = 0
    known_ids = set()
    for row in decisions:
        raw = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
        identity = row.get("operation_id") or raw.get("operation_id")
        if isinstance(identity, str):
            known_ids.add(identity)
    for update in updates:
        identity = update.get("operation_id") if isinstance(update, Mapping) else None
        if not isinstance(identity, str) or identity not in known_ids:
            unmatched += 1
        else:
            latest[identity] = update
    envelopes, initial = [], []
    attempted, waiting, unknown_identity, objectives = 0, 0, 0, Counter()
    for i, row in enumerate(decisions):
        envelope = decision_outcome(row)
        initial.append(envelope)
        raw = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
        selected = _option(row)
        operation = envelope.get("operation") or selected.get("family")
        if operation in ("wait", "plan"):
            waiting += 1
        elif isinstance(operation, str) and operation:
            attempted += 1
            phase, need = row.get("phase"), selected.get("need_id") or raw.get("need_id")
            objectives[(str(phase) if phase is not None else None, str(need) if need is not None else None)] += 1
        else:
            unknown_identity += 1
        identity = row.get("operation_id") or raw.get("operation_id")
        update = latest.get(identity) if isinstance(identity, str) else None
        if update is not None:
            measured = decision_outcome({"outcome": update.get("outcome"), "choice": {"family": operation}})
            if measured.get("operation") in (None, operation) and measured["state"] != "unknown":
                envelope = measured
        transaction = envelope.get("transaction_id") or raw.get("transaction_id")
        if receipt_loader is not None and isinstance(transaction, str) and _TRANSACTION.fullmatch(transaction):
            try:
                receipt = receipt_loader(transaction)
            except (OSError, ValueError, TypeError):
                receipt = None
            if isinstance(receipt, Mapping):
                candidate = receipt.get("candidate")
                actual_option = candidate.get("option_id") if isinstance(candidate, Mapping) else None
                selected_id = selected.get("option_id")
                if (receipt.get("transaction_id", transaction) != transaction
                        or actual_option is not None and selected_id is not None and actual_option != selected_id):
                    unmatched += 1
                else:
                    reconciled = normalize(receipt, operation, transaction)
                    if reconciled["state"] != "unknown":
                        envelope = reconciled
        if i in conflicts:
            envelope = normalize({}, operation)
        envelopes.append(envelope)
    return {"outcome_counts": count(envelopes), "initial_outcome_counts": count(initial), "outcomes": envelopes,
            "operation_attempts": {"attempted": attempted, "wait_or_plan": waiting, "identity_unknown": unknown_identity,
                                   "definition": "selected_nonwait_operation"},
            "objective_attempts": [{"phase": phase, "need_id": need, "attempts": amount}
                                   for (phase, need), amount in sorted(objectives.items(), key=lambda x: str(x[0]))],
            "objective_attempted": True if attempted else (None if unknown_identity or not decisions else False),
            "duplicate_records": duplicate_count, "conflicting_records": len(conflicts),
            "unmatched_observations": unmatched}


def read_records(path):
    """Unreadable rows occupy an unknown slot instead of disappearing from totals."""
    try:
        lines = Path(path).read_text().splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            value = None
        rows.append(value if isinstance(value, Mapping) else {"outcome": {}, "evidence_error": "malformed_decision_record"})
    return rows


def summarize_records(path, *, observations=None):
    path = Path(path)
    if observations is None:
        observations = [row for row in read_records(path.with_name("clean-decisions.jsonl"))
                        if row.get("event") == "operation_observation"]
    def load_receipt(name):
        return json.loads((path.parent / name).read_text())
    result = reconcile_records(read_records(path), receipt_loader=load_receipt, observations=observations)
    result.pop("outcomes")
    result["decision_records_available"] = path.is_file()
    return result


def planned_job_accounting(planned, jobs):
    """The immutable plan is the denominator, including rows never dispatched."""
    by_id = {row.get("id"): row for row in jobs if isinstance(row, Mapping)}
    statuses, attempted, unknown, completed, objective_unknown = Counter(), 0, 0, 0, 0
    missing = []
    planned_ids = set()
    for plan in planned:
        identity = plan["id"]
        if identity in planned_ids:
            raise ValueError("Duplicate planned job identity")
        planned_ids.add(identity)
        row = by_id.get(identity)
        if row is None:
            missing.append(identity)
            row = {}
        statuses[str(row.get("status") or "unknown")] += 1
        if row.get("objective_attempted") is True:
            attempted += 1
        elif row.get("objective_attempted") is not False:
            unknown += 1
        if row.get("successor_passed") is True:
            completed += 1
        elif row.get("successor_passed") is not False:
            objective_unknown += 1
    return {"version": 1, "denominator": "all_planned_jobs", "planned_jobs": len(planned),
            "process_states": dict(sorted(statuses.items())), "missing_status_jobs": missing,
            "unplanned_status_jobs": sorted(str(k) for k in by_id if k not in planned_ids),
            "objective_attempted_jobs": attempted, "objective_attempt_unknown_jobs": unknown,
            "objective_completed_jobs": completed, "objective_result_unknown_jobs": objective_unknown}
