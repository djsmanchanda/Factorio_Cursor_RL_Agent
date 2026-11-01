# Path: core/rocket_launch_evidence.py
# Purpose: Reduce closed rocket observation sessions without claiming runtime provenance or launch authority.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path

from jsonschema import Draft7Validator


NON_CLAIMS = (
    "Reports and filenames are caller-supplied; server, save, writer and runtime provenance are not authenticated.",
    "A matching on_rocket_launched report means ascent completion only; cargo delivery and victory are not established.",
    "A matching event does not prove that its rocket or silo was earned, funded or transaction-owned.",
    "A closed report set cannot prove absence of launch outside its asserted observation session.",
    "This result grants no dispatch, launch, or production authority.",
)


@dataclass(frozen=True)
class LaunchSessionEvidence:
    session_id: str
    surface: str
    force: str
    start_tick: int
    end_tick: int
    target_unit_number: int
    status: str
    matching_sequences: tuple[int, ...]
    unidentified_sequences: tuple[int, ...]
    other_sequences: tuple[int, ...]
    notes: tuple[str, ...] = NON_CLAIMS


def _finite_position(value: object) -> bool:
    return (isinstance(value, Mapping) and set(value) == {"x", "y"}
            and all(type(value[key]) is int or (type(value[key]) is float and isfinite(value[key]))
                    for key in ("x", "y")))


def evaluate_launch_session(
    start: Mapping, end: Mapping, events: Sequence[tuple[str, Mapping]], *,
    target_silo: Mapping,
) -> LaunchSessionEvidence:
    """Check a fresh arm, its disarm and every asserted event in between.

    ``target_silo`` requires name, unit_number and position. Events lacking a
    silo unit number cannot be attributed and yield an indeterminate result if
    no matching target event exists. A positive status is a matching report,
    not independently authenticated proof that a runtime launched a rocket.
    """
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "rocket_launch_observation.schema.json"
    validator = Draft7Validator(json.loads(schema_path.read_text(encoding="utf-8")))
    if (not isinstance(target_silo, Mapping) or set(target_silo) != {"name", "unit_number", "position"}
            or not isinstance(target_silo["name"], str) or not target_silo["name"]
            or type(target_silo["unit_number"]) is not int or target_silo["unit_number"] < 0
            or not _finite_position(target_silo["position"])):
        raise ValueError("target silo must have a valid name, unit number and finite position")
    if (not isinstance(start, Mapping) or not validator.is_valid(start)
            or start.get("kind") != "rocket_launch_scope" or start.get("ok") is not True
            or start.get("enabled") is not True or start.get("session_started") is not True
            or start.get("recorded_launches") != 0 or start.get("scope_since_tick") != start.get("tick")
            or not start.get("request_id") or not start.get("session_id")
            or start["session_id"] == "unidentified"):
        raise ValueError("start must be a fresh, identified, empty armed scope report")
    if (not isinstance(end, Mapping) or not validator.is_valid(end)
            or end.get("kind") != "rocket_launch_scope" or end.get("ok") is not True
            or end.get("enabled") is not False or end.get("session_started") is not False
            or not end.get("request_id") or end["request_id"] == start["request_id"]
            or any(end.get(key) != start[key] for key in ("session_id", "surface", "force", "scope_since_tick"))
            or end["tick"] < start["tick"]):
        raise ValueError("end must close the same identified observation session")
    if (isinstance(events, (str, bytes)) or not isinstance(events, Sequence)
            or len(events) != end["recorded_launches"]):
        raise ValueError("event count must match the closing scope report")
    checked = []
    filenames = set()
    for entry in events:
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise ValueError("each event requires its filename and report")
        filename, event = entry
        if (not isinstance(filename, str) or not isinstance(event, Mapping)
                or not validator.is_valid(event) or event.get("kind") != "rocket_launch"
                or any(event.get(key) != start[key] for key in ("session_id", "surface", "force"))
                or not start["tick"] <= event["tick"] <= end["tick"]
                or filename != f"rocket_launch_{start['session_id']}_{event['tick']}_{event['sequence']}.json"
                or filename in filenames or not _finite_position(event["rocket"]["position"])):
            raise ValueError("event filename, scope, time or report is invalid")
        silo = event.get("rocket_silo")
        if silo is not None and not _finite_position(silo["position"]):
            raise ValueError("event silo position is not finite")
        filenames.add(filename)
        checked.append(event)
    checked.sort(key=lambda event: event["sequence"])
    for previous, current in zip(checked, checked[1:]):
        if current["sequence"] != previous["sequence"] + 1 or current["tick"] < previous["tick"]:
            raise ValueError("session events are not a contiguous chronological sequence")
    matches, unidentified, others = [], [], []
    for event in checked:
        silo = event.get("rocket_silo")
        if silo is None or "unit_number" not in silo:
            unidentified.append(event["sequence"])
        elif silo["unit_number"] != target_silo["unit_number"]:
            others.append(event["sequence"])
        elif silo["name"] != target_silo["name"] or silo["position"] != target_silo["position"]:
            raise ValueError("target silo unit has inconsistent name or position")
        else:
            matches.append(event["sequence"])
    status = "matching_event_found" if matches else "indeterminate" if unidentified else "no_matching_event"
    return LaunchSessionEvidence(
        session_id=start["session_id"], surface=start["surface"], force=start["force"],
        start_tick=start["tick"], end_tick=end["tick"], target_unit_number=target_silo["unit_number"],
        status=status, matching_sequences=tuple(matches), unidentified_sequences=tuple(unidentified),
        other_sequences=tuple(others),
    )
