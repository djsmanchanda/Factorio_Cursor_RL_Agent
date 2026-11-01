# Path: orchestrator/optimizer_build_reconciliation.py | Purpose: Compare correlated build-event facts to a bound optimizer plan.
"""Read-only diagnostics; event tags are not authenticated ownership or a complete scan."""

from collections import Counter, defaultdict
from fractions import Fraction
import json
from pathlib import Path

from jsonschema import Draft7Validator

from core.optimizer_artifact_binding import BoundArtifactPlan
from orchestrator.material_reservations import MaterialReservationError, MaterialReservationLedger
from orchestrator.optimizer_reservations import _verify_bound_ledger


def compare_optimizer_build_evidence(bound: BoundArtifactPlan, ledger: MaterialReservationLedger, *,
                                     episode_id: str, report: dict, submission_tick: int | None = None) -> dict:
    """Compare current facts for recorded events; never credit construction or release stock."""
    _verify_bound_ledger(bound, ledger, episode_id)
    version = report.get("version") if isinstance(report, dict) else None
    name = {"optimizer-build-evidence-v2": "optimizer_build_evidence.schema.json",
            "optimizer-build-evidence-v3": "optimizer_build_evidence_v3.schema.json"}.get(version)
    if name is None:
        raise ValueError("optimizer build evidence report has an unsupported version")
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if not Draft7Validator(schema).is_valid(report) or report.get("ok") is not True:
        raise ValueError("optimizer build evidence report is invalid")
    if submission_tick is not None and (type(submission_tick) is not int or submission_tick < 0
                                        or submission_tick > report["tick"]):
        raise ValueError("submission tick is invalid or later than the report")
    binding = bound.plan.binding
    tags = {"optimizer_episode": episode_id, "optimizer_project": binding.project_id,
            "optimizer_transaction": binding.transaction_id, "optimizer_plan_hash": binding.plan_hash}
    if (report["surface"] != binding.scope.surface or report["force"] != binding.scope.force
            or report["tags"] != tags or any(row["tick"] > report["tick"] for row in report["entities"])):
        raise ValueError("optimizer build evidence does not match the bound plan")
    by_key = defaultdict(list)
    unit_counts = Counter(row["unit_number"] for row in report["entities"] if "unit_number" in row)
    for row in report["entities"]:
        # JSON floats and plan coordinates both originate in the same numeric action.
        position = row["position"]
        by_key[(row["entity"], Fraction(position["x"]), Fraction(position["y"]))].append(row)
    keys = {(ref.entity, ref.x, ref.y) for ref in bound.plan.required_entities}
    counts = Counter()
    entities = []
    for ref in bound.plan.required_entities:
        rows = by_key[(ref.entity, ref.x, ref.y)]
        present = [row for row in rows if row["current"]["state"] == "present"]
        unknown = [row for row in rows if row["current"]["state"] in ("unavailable", "mismatch")]
        if not rows:
            status = "no_event"
        elif unknown or len(rows) > 1 or any(unit_counts[row["unit_number"]] > 1
                                               for row in rows if "unit_number" in row):
            status = "unknown"
        elif "unit_number" not in rows[0]:
            status = "identity_unavailable"
        elif not present:
            status = "no_current_record"
        elif submission_tick is not None and present[0]["tick"] < submission_tick:
            status = "before_submission"
        else:
            current = present[0]["current"]
            expected = [("direction", ref.direction), ("quality", ref.quality)]
            if ref.recipe is not None:
                expected.append(("recipe", ref.recipe))
            if any(current[key]["state"] == "unavailable" for key, _ in expected):
                status = "unknown"
            elif any(current[key].get("value") != value for key, value in expected):
                status = "configuration_mismatch"
            else:
                status = "matching_event"
        counts[status] += 1
        entities.append({"planned": ref.to_dict(), "status": status,
                         "event_unit_numbers": [row.get("unit_number") for row in rows]})
    return {"binding": binding.to_dict(), "report_request_id": report["request_id"],
            "report_tick": report["tick"], "submission_tick": submission_tick,
            "counts": dict(counts), "entities": entities,
            "unexpected_event_count": sum(len(rows) for key, rows in by_key.items() if key not in keys),
            "ownership_claimed": False, "construction_complete": False, "dispatch_authorized": False,
            "non_claims": ["event tags and output writer are not authenticated",
                           "no event or failed lookup proves an entity absent or its site empty",
                           "an event without a unit number cannot establish current entity continuity",
                           "matching facts do not prove ownership, settings beyond those compared, or delivery",
                           "submission tick is a caller assertion and does not authenticate runtime continuity"]}
