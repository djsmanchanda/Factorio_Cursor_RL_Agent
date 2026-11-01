# Path: orchestrator/guarded_configuration.py | Purpose: Bind configuration actions to correlated robot-built entity identities.
"""Compare observed events without claiming authenticated runtime ownership."""

from fractions import Fraction
from collections import Counter
import json
from pathlib import Path

from jsonschema import Draft7Validator


def guard_configuration(actions: list[dict], report: dict, *, tags: dict,
                        surface: str, force: str, submission_tick: int,
                        preexisting: dict | None = None) -> list[dict]:
    """Require one current, post-submission robot-build record for each target.

    ``preexisting`` maps (entity, x, y) to the unit number observed before
    submission for a target this transaction did not create (a shared chest or a
    retargeted machine). It needs no build record, because the submission left it
    untagged; configuration still binds to that unit number, and the executor
    refuses on a mismatch. Targets absent from it keep the full build-record rule.
    """
    version = report.get("version") if isinstance(report, dict) else None
    schema_name = {"optimizer-build-evidence-v2": "optimizer_build_evidence.schema.json",
                   "optimizer-build-evidence-v3": "optimizer_build_evidence_v3.schema.json"}.get(version)
    if schema_name is None:
        raise ValueError("configuration evidence has invalid scope, tags, or time")
    schema = json.loads((Path(__file__).resolve().parent.parent / "schemas" /
                         schema_name).read_text(encoding="utf-8"))
    if (not Draft7Validator(schema).is_valid(report) or report.get("ok") is not True
            or report.get("tags") != tags or report.get("surface") != surface or report.get("force") != force
            or type(submission_tick) is not int or submission_tick < 0 or report["tick"] < submission_tick):
        raise ValueError("configuration evidence has invalid scope, tags, or time")
    guarded = []
    used = set()
    unit_counts = Counter(row["unit_number"] for row in report["entities"] if "unit_number" in row)
    for action in actions:
        position = action["position"]
        known = (preexisting or {}).get((action["entity"], position["x"], position["y"]))
        if known is not None:
            if type(known) is not int or known < 1 or known in used:
                raise ValueError("pre-existing configuration target lacks a unique unit number")
            used.add(known)
            guarded.append({**action, "action_type": "configure_entity_if_unit_number", "expected_unit_number": known})
            continue
        target = (action["entity"], Fraction(position["x"]), Fraction(position["y"]))
        matches = [row for row in report["entities"]
                   if (row["entity"], Fraction(row["position"]["x"]), Fraction(row["position"]["y"])) == target]
        if (len(matches) != 1 or not submission_tick <= matches[0]["tick"] <= report["tick"]
                or matches[0]["current"]["state"] != "present" or "unit_number" not in matches[0]
                or unit_counts[matches[0]["unit_number"]] != 1 or matches[0]["unit_number"] in used):
            raise ValueError("configuration target lacks a unique current robot-build record")
        unit = matches[0]["unit_number"]
        used.add(unit)
        guarded.append({**action, "action_type": "configure_entity_if_unit_number", "expected_unit_number": unit})
    return guarded


def require_configuration_baseline(report, *, tags, surface, force, submission_tick=None):
    """Require an empty v3 event stream sampled before the correlated submission.

    This is a local assertion, not authentication of the server or continuity.
    """
    path = Path(__file__).resolve().parent.parent / "schemas/optimizer_build_evidence_v3.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(report, dict) or not Draft7Validator(schema).is_valid(report)
            or report.get("version") != "optimizer-build-evidence-v3" or report.get("ok") is not True
            or report.get("surface") != surface or report.get("force") != force or report.get("tags") != tags
            or report.get("entities") != []
            or (submission_tick is not None and (type(submission_tick) is not int
                                                  or submission_tick < report["tick"]))):
        raise ValueError("configuration requires an empty pre-submission v3 event baseline")
    return report["tick"]
