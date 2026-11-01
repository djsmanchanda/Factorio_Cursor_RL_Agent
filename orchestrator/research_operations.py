# Path: orchestrator/research_operations.py | Purpose: Observe and explicitly select force-scoped research without running a research cascade.
# Ported from local laya/release 5a5ed28:orchestrator/research_operations.py.
# Adapted: _await_bot_built_infrastructure comes from stage_services, which owns it on this branch.
"""Shared research mechanics. No construction, grants, or strategy live here."""
from __future__ import annotations

import copy
import math
import json
import re
import uuid
from collections.abc import Mapping

from orchestrator.game_bridge import BridgeError, load_json
from orchestrator.science_diagnosis import diagnose_science_status, report_identity
from orchestrator.work_state import WorkStateSignal

MAX_REPORT_AGE = 300
OBSERVATION_VERSION = "research-observation-v1"
LAB_RECORD_VERSION = 2
LAB_PACKET_LIMIT = 6
LAB_OWNERSHIP_EVIDENCE_VERSION = "research-lab-ownership-v1"
LAB_OWNERSHIP_EVIDENCE_SOURCE = "robot_built_entity_tags"
_LAB_PLAN_ENTITIES = frozenset({"lab", "inserter", "requester-chest", "medium-electric-pole"})
_LAB_CONFIGURATION_FIELDS = frozenset({
    "logistic_request", "logistic_requests", "logistic_sections",
    "clear_logistic_groups", "inventory_limit", "clear_inventory_limit",
    "logistic_condition", "clear_logistic_condition", "input_priority",
    "output_priority", "recipe",
})


def _number(value, *, minimum=0):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= minimum)


def _coordinate(value) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _technology(report: Mapping, force: str, requested: str) -> dict:
    if report.get("ok") is not True or report.get("force") != force:
        raise ValueError("research report failed or has wrong force")
    if not _number(report.get("tick")) or not isinstance(report["tick"], int):
        raise ValueError("research tick unavailable")
    tech = report.get("technology") or {}
    canonical = tech.get("canonical_name")
    if (tech.get("requested_name") != requested or not canonical or tech.get("name") != canonical
            or not isinstance(tech.get("target_completed"), bool)
            or not isinstance(tech.get("researched"), bool) or not isinstance(tech.get("enabled"), bool)):
        raise ValueError("requested/canonical technology identity unavailable")
    level = tech.get("requested_level")
    if level is not None:
        stem, _, suffix = requested.rpartition("-")
        if (not suffix.isdigit() or level != int(suffix) or isinstance(level, bool)
                or not isinstance(level, int) or not canonical.startswith(stem + "-")
                or not _number(tech.get("current_level"), minimum=1)
                or not isinstance(tech["current_level"], int)):
            raise ValueError("requested/current level mismatch")
        completed = level < tech["current_level"] or (level == tech["current_level"] and tech["researched"])
        if completed != tech["target_completed"]:
            raise ValueError("inconsistent level completion")
    elif canonical != requested or tech["target_completed"] != tech["researched"]:
        raise ValueError("non-ranged technology mismatch")
    if tech.get("state") not in {"available", "current", "future", "completed"}:
        raise ValueError("technology state unavailable")
    if (tech["state"] == "completed") != tech["target_completed"]:
        raise ValueError("technology completion/state mismatch")
    packs = tech.get("science_packs")
    if (not isinstance(packs, Mapping) or not packs
            or any(not isinstance(k, str) or not k or not _number(v, minimum=0.000001)
                   for k, v in packs.items())):
        raise ValueError("live research ingredients unavailable")
    if not _number(report.get("research_progress")) or report["research_progress"] > 1:
        raise ValueError("research progress unavailable")
    queue = report.get("research_queue")
    if not isinstance(queue, list) or any(not isinstance(t, str) or not t for t in queue):
        raise ValueError("research queue unavailable")
    return dict(tech)


def collect_research_reports(bridge, *, surface: str, force: str, requested: str) -> dict:
    """Read existing GameBridge reports; the caller supplies a later world tick for freshness."""
    try:
        reports = {"research": load_json(bridge.research_status(force=force, technology=requested)),
                "science": load_json(bridge.science_status(surface, force)),
                "options": load_json(bridge.research_options(force=force))}
        canonical = reports["research"]["technology"]["canonical_name"]
        lua = ("local f=game.forces[" + json.dumps(force) + "];local t=f.technologies[" + json.dumps(canonical)
               + "];rcon.print(helpers.table_to_json{tick=game.tick,force=f.name,canonical=t.name,"
               "unit_energy=t.research_unit_energy,lab_speed=prototypes.entity.lab.get_researching_speed('normal'),"
               "speed_bonus=f.laboratory_speed_modifier})")
        reports["capacity"] = json.loads(bridge.command("/sc " + lua).strip())
        return reports
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"[:240]}


def research_observation(reports: Mapping, *, tick: int, surface: str, force: str,
                         requested: str, max_age: int = MAX_REPORT_AGE) -> dict:
    """Fail closed on stale, future, wrong-force, wrong-level or incomplete evidence."""
    result = {"version": OBSERVATION_VERSION, "tick": tick, "surface": surface, "force": force,
              "requested": requested, "available": False}
    try:
        if reports.get("error"):
            raise ValueError(reports["error"])
        research, science = reports["research"], reports["science"]
        tech = _technology(research, force, requested)
        identity = report_identity(science)
        if identity["surface"] != surface or identity["force"] != force:
            raise ValueError("science scope mismatch")
        if any(not 0 <= tick - t <= max_age for t in (research["tick"], science["tick"])):
            raise ValueError("research/science reports stale or from future")
        diagnosis = diagnose_science_status(science)
        result.update(available=True, technology=tech, current=research.get("current_research"),
                      progress=research["research_progress"], queue=research["research_queue"],
                      report_ticks={"research": research["tick"], "science": science["tick"]},
                      labs={k: science["labs"][k] for k in ("total", "working", "status_counts", "input_inventory")},
                      diagnosis=diagnosis["diagnosis"], selection_candidates=[])
        capacity = reports.get("capacity", {})
        if (capacity.get("force") == force and capacity.get("canonical") == tech["canonical_name"]
                and _number(capacity.get("tick")) and 0 <= tick - capacity["tick"] <= max_age
                and _number(capacity.get("unit_energy"), minimum=0.000001)
                and _number(capacity.get("lab_speed"), minimum=0.000001)
                and _number(capacity.get("speed_bonus"))):
            result["capacity"] = dict(capacity)
        options = reports.get("options") or {}
        if (options.get("ok") is True and options.get("force") == force
                and _number(options.get("tick")) and 0 <= tick - options["tick"] <= max_age):
            # Use live requested names, including the currently open earlier repeatable level.
            # Do not invent a prerequisite tree or infer base-game MP4 ingredients.
            for candidate in options.get("options") or []:
                if (candidate.get("canonical_name") == tech["canonical_name"]
                        and candidate.get("state") in {"available", "running"}
                        and isinstance(candidate.get("technology"), str)):
                    result["selection_candidates"].append(dict(candidate))
        return result
    except (KeyError, TypeError, ValueError) as error:
        result.update(available=False, reason=str(error)[:240])
        return result


def select_research(bridge, *, force: str, technology: str, expected_queue: list[str],
                    expected_current: str | None, replace_queue: bool = False) -> dict:
    """One selected operation with an exact queue guard and independent readback.

    Queue replacement is an explicit parameter. Repeating a selection already in
    the live queue never calls the destructive setter. The observation is also
    passed to the setter so the mod rechecks it atomically before replacing the
    queue; a change between the read and the write fails closed. No prerequisite
    is selected.
    """
    def fail(code, message):
        raise WorkStateSignal(message, code=code, classification="intended_difficulty", state="supply_wait")

    valid_current = expected_current is None or (
        isinstance(expected_current, str) and bool(expected_current))
    if (not force or not technology or not isinstance(expected_queue, list)
            or any(not isinstance(name, str) or not name for name in expected_queue)
            or not valid_current or not isinstance(replace_queue, bool)):
        fail("research_selection_invalid", "Explicit force, target, queue and replacement flag required")
    before = load_json(bridge.research_status(force=force, technology=technology))
    try:
        tech = _technology(before, force, technology)
    except (TypeError, ValueError) as error:
        fail("research_observation_unavailable", str(error))
    if before["research_queue"] != expected_queue or before.get("current_research") != expected_current:
        fail("research_queue_changed", "Research queue changed since selection; observe again")
    if tech["target_completed"] or tech["state"] == "future" or not tech["enabled"]:
        fail("research_target_unavailable", "Target is completed, future or disabled")
    canonical = tech["canonical_name"]
    already = canonical in before["research_queue"] or before.get("current_research") == canonical
    replaces = bool(before["research_queue"] or before.get("current_research")) and not already
    if replaces and not replace_queue:
        fail("research_queue_replacement_required", "Replacing existing research requires replace_queue=true")
    if not already:
        try:
            submission = load_json(bridge.set_research(
                technology, force=force, expected_current=expected_current,
                expected_queue=expected_queue, replace_queue=replace_queue))
        except BridgeError as error:
            reason = str(error)
            if "changed since observation" in reason:
                fail("research_queue_changed", "Research queue changed since selection; observe again")
            if "replacement requires" in reason:
                fail("research_queue_replacement_required",
                     "Replacing existing research requires replace_queue=true")
            fail("research_selection_failed", "Research setter did not confirm the requested selection")
        if submission.get("ok") is not True:
            reason = str(submission.get("error") or "")
            if "changed since observation" in reason:
                fail("research_queue_changed", "Research queue changed since selection; observe again")
            if "replacement requires" in reason:
                fail("research_queue_replacement_required",
                     "Replacing existing research requires replace_queue=true")
            fail("research_selection_failed", "Research setter did not confirm the requested selection")
        if (submission.get("force") != force
                or submission.get("technology") != technology or submission.get("canonical_name") != canonical
                or not _number(submission.get("tick")) or submission["tick"] < before["tick"]):
            fail("research_selection_failed", "Research setter did not confirm the requested selection")
    after = load_json(bridge.research_status(force=force, technology=technology))
    try:
        observed = _technology(after, force, technology)
    except (TypeError, ValueError) as error:
        fail("research_readback_failed", str(error))
    if (after["tick"] < before["tick"] or observed["canonical_name"] != canonical
            or not (observed["target_completed"] or after.get("current_research") == canonical
                    or canonical in after["research_queue"])):
        fail("research_readback_failed", "Selected research absent from fresh readback")
    return {"ok": True, "code": "research_selected", "verified": True, "submitted": not already,
            "idempotent": already, "replaced_queue": replaces, "before": before, "observed": after}


def _lab_baseline(client, bridge, *, surface: str, force: str) -> tuple[int, int]:
    """Read fresh, scoped lab ownership evidence before planning or mutation."""
    from orchestrator import live_base
    status_method = getattr(bridge, "science_status", None)
    if not callable(status_method):
        raise ValueError("lab construction requires a science_status baseline")
    try:
        status = load_json(status_method(surface, force))
    except Exception as error:
        raise ValueError("lab baseline report unavailable") from error
    if not isinstance(status, Mapping):
        raise ValueError("lab baseline report is not an object")
    identity = report_identity(status)
    if identity["surface"] != surface or identity["force"] != force:
        raise ValueError("lab baseline scope mismatch")
    now = live_base.game_tick(client)
    if type(now) is not int or not 0 <= now - identity["tick"] <= MAX_REPORT_AGE:
        raise ValueError("lab baseline report stale or from future")
    labs = status.get("labs")
    total = labs.get("total") if isinstance(labs, Mapping) else None
    if type(total) is not int or total < 0:
        raise ValueError("lab baseline total unavailable")
    return total, identity["tick"]


def _lab_plan_actions(plan, *, surface: str, force: str, count: int) -> list:
    if not isinstance(plan, Mapping):
        raise ValueError("lab packet plan is not an object")
    if plan.get("surface") != surface or plan.get("force") != force:
        raise ValueError("lab packet plan scope mismatch")
    phases = plan.get("phases")
    if not isinstance(phases, list) or len(phases) != 1 or not isinstance(phases[0], Mapping):
        raise ValueError("lab packet plan must have exactly one phase")
    phase = phases[0]
    if phase.get("name") != "funded_research_labs":
        raise ValueError("lab packet plan identity mismatch")
    actions = phase.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("lab packet plan has no actions")
    counts = {entity: 0 for entity in _LAB_PLAN_ENTITIES}
    for action in actions:
        if not isinstance(action, Mapping) or action.get("action_type") != "place_ghost":
            raise ValueError("lab packet plan contains a non-ghost action")
        entity = action.get("entity")
        if not isinstance(entity, str) or entity not in counts:
            raise ValueError("lab packet plan contains an unexpected entity")
        position = action.get("position")
        if (not isinstance(position, Mapping) or not _coordinate(position.get("x"))
                or not _coordinate(position.get("y"))):
            raise ValueError("lab packet plan position unavailable")
        counts[entity] += 1
    if any(counts[entity] != count for entity in ("lab", "inserter", "requester-chest")):
        raise ValueError("lab packet plan counts do not match its packet")
    return actions


def _validate_lab_packet(raw, *, surface: str, force: str) -> dict:
    if not isinstance(raw, Mapping):
        raise ValueError("lab packet is not an object")
    count = raw.get("count")
    if type(count) is not int or not 1 <= count <= LAB_PACKET_LIMIT:
        raise ValueError("lab packet count must be 1..6")
    packet_id = raw.get("id")
    if not isinstance(packet_id, str) or not packet_id:
        raise ValueError("lab packet identity unavailable")
    transaction_id = raw.get("transaction_id")
    if not isinstance(transaction_id, str) or not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise ValueError("lab packet transaction identity unavailable")
    complete = raw.get("complete", False)
    if not isinstance(complete, bool):
        raise ValueError("lab packet completion is not boolean")
    _lab_plan_actions(raw.get("plan"), surface=surface, force=force, count=count)
    return {"count": count, "id": packet_id, "transaction_id": transaction_id,
            "complete": complete, "plan": raw["plan"]}


def _load_lab_record(path, *, episode_id: str, surface: str, force: str) -> dict:
    try:
        raw = json.loads(path.read_text())
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("lab recovery record is unreadable") from error
    if not isinstance(raw, Mapping):
        raise ValueError("lab recovery record is not an object")
    if raw.get("version") != LAB_RECORD_VERSION:
        raise ValueError("lab recovery record version is unsupported")
    if (raw.get("episode_id") != episode_id or raw.get("surface") != surface
            or raw.get("force") != force):
        raise ValueError("lab recovery record scope does not match this run")
    baseline_labs = raw.get("baseline_labs")
    if type(baseline_labs) is not int or baseline_labs < 0:
        raise ValueError("lab recovery baseline unavailable")
    baseline_tick = raw.get("baseline_tick")
    if type(baseline_tick) is not int or baseline_tick < 0:
        raise ValueError("lab recovery baseline tick unavailable")
    raw_packets = raw.get("packets")
    if not isinstance(raw_packets, list):
        raise ValueError("lab recovery packets must be a list")
    packets = []
    committed = 0
    positions = set()
    incomplete = 0
    for raw_packet in raw_packets:
        packet = _validate_lab_packet(raw_packet, surface=surface, force=force)
        if packet["id"] != f"research_labs_{committed}":
            raise ValueError("lab recovery packet sequence is inconsistent")
        for action in packet["plan"]["phases"][0]["actions"]:
            position = (action["position"]["x"], action["position"]["y"])
            if position in positions:
                raise ValueError("lab recovery packet positions overlap")
            positions.add(position)
        committed += packet["count"]
        incomplete += 0 if packet["complete"] else 1
        packets.append(packet)
    if incomplete > 1 or (incomplete and packets[-1]["complete"]):
        raise ValueError("lab recovery record has inconsistent unfinished packets")
    return {"version": LAB_RECORD_VERSION, "episode_id": episode_id, "surface": surface,
            "force": force, "baseline_labs": baseline_labs,
            "baseline_tick": baseline_tick, "packets": packets}


def _write_lab_record(path, record: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, sort_keys=True))
    temporary.replace(path)


def _lab_position(action: Mapping) -> tuple[float, float]:
    position = action["position"]
    return float(position["x"]), float(position["y"])


def _lab_occupants(client, surface: str, force: str, actions: list) -> dict:
    """Observe exact planned positions without inferring who placed anything."""
    from orchestrator import live_base
    positions = tuple(_lab_position(action) for action in actions)
    observed = live_base.entity_signatures_at(client, surface, force, positions)
    return {
        position: record
        for position, record in observed.items()
        if record.get("name") not in (None, "NONE")
    }


def _lab_ownership_unknown(packet_id: str, reason: str, targets: list) -> WorkStateSignal:
    return WorkStateSignal(
        f"{packet_id}: refusing to configure or reconcile an existing lab entity "
        f"without runtime transaction ownership ({reason})",
        code="research_lab_ownership_unknown",
        classification="intended_difficulty",
        state="constructing",
        details={
            "packet": packet_id,
            "targets": [
                {"entity": entity, "position": [position[0], position[1]]}
                for entity, position in targets
            ],
            "gap": reason,
        },
    )


def _lab_ownership_evidence(
    session, client, *, packet_id: str, transaction_id: str, episode_id: str, surface: str, force: str,
    targets: list, baseline_tick: int,
) -> tuple[dict | None, str]:
    """Read a runtime transaction-owned observation, never a persisted sidecar.

    The only accepted evidence is a per-entity record produced from the
    engine's robot-built event tags: the packet's own ghost tag, the built
    entity's engine-assigned ``unit_number`` and its exact planned position.
    Positions, a recovery-record packet id, a previous ``complete`` flag and
    same-name presence are not ownership and are never consulted here. This
    Evidence is meaningful only for a separately verified runtime and channel;
    tags and scope alone do not establish ownership or authorize dispatch.
    """
    reader = getattr(session, "research_lab_ownership", None)
    if not callable(reader):
        reader = getattr(getattr(session, "bridge", None), "research_lab_ownership", None)
    if not callable(reader):
        return None, "no runtime transaction-owned entity observation is available"
    try:
        response = reader(
            surface=surface, force=force, packet_id=packet_id, episode_id=episode_id,
            transaction_id=transaction_id,
        )
        raw = response if isinstance(response, Mapping) else load_json(response)
    except Exception as error:  # fail closed on any adapter failure
        return None, f"transaction-owned observation failed: {type(error).__name__}"
    try:
        from orchestrator import live_base
        now = live_base.game_tick(client)
    except Exception:
        return None, "current game tick could not be observed after ownership evidence"
    if type(now) is not int or now < 0:
        return None, "current game tick is invalid"
    if not isinstance(raw, Mapping):
        return None, "transaction-owned observation is not an object"
    if (raw.get("ok") is not True or raw.get("ownership_claimed") is not False
            or raw.get("dispatch_authorized") is not False
            or raw.get("version") != LAB_OWNERSHIP_EVIDENCE_VERSION
            or raw.get("source") != LAB_OWNERSHIP_EVIDENCE_SOURCE):
        return None, "evidence is not a runtime robot-built tag observation"
    if (raw.get("episode_id") != episode_id or raw.get("surface") != surface
            or raw.get("force") != force or raw.get("packet_id") != packet_id
            or raw.get("transaction_id") != transaction_id):
        return None, "transaction-owned observation scope does not match this packet"
    tick = raw.get("tick")
    if type(tick) is not int or not baseline_tick <= tick <= now:
        return None, "transaction-owned observation tick is stale or from the future"
    records = raw.get("entities")
    if not isinstance(records, list):
        return None, "transaction-owned observation has no entity list"
    observed: dict[tuple[float, float], tuple[str, int]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            return None, "transaction-owned entity record is not an object"
        entity = record.get("entity")
        position = record.get("position")
        unit_number = record.get("unit_number")
        tags = record.get("tags")
        if not isinstance(entity, str) or not entity:
            return None, "transaction-owned entity name is unavailable"
        if (not isinstance(position, Mapping) or not _coordinate(position.get("x"))
                or not _coordinate(position.get("y"))):
            return None, "transaction-owned entity position is unavailable"
        if type(unit_number) is not int or unit_number <= 0:
            return None, "transaction-owned entity unit number is unavailable"
        if (not isinstance(tags, Mapping) or tags.get("research_lab_packet") != packet_id
                or tags.get("research_lab_transaction") != transaction_id):
            return None, "entity tags do not carry this packet's transaction ownership"
        event_tick = record.get("tick")
        if type(event_tick) is not int or not baseline_tick <= event_tick <= tick:
            return None, "transaction-owned entity event tick is outside the observation interval"
        key = (float(position["x"]), float(position["y"]))
        if key in observed:
            return None, "transaction-owned observation repeats an entity position"
        observed[key] = (entity, unit_number)
    for entity, position in targets:
        record = observed.get(position)
        if record is None or record[0] != entity:
            return None, "transaction-owned observation does not cover every required entity"
    return observed, ""


def _require_lab_ownership(
    session, client, *, packet_id: str, transaction_id: str, episode_id: str, surface: str, force: str,
    targets: list, baseline_tick: int,
) -> dict:
    """Require current transaction-owned identity before touching existing entities."""
    from orchestrator import live_base
    if not targets:
        return {}
    evidence, reason = _lab_ownership_evidence(
        session, client, packet_id=packet_id, transaction_id=transaction_id, episode_id=episode_id, surface=surface,
        force=force, targets=targets, baseline_tick=baseline_tick,
    )
    if evidence is None:
        raise _lab_ownership_unknown(packet_id, reason, targets)
    try:
        live = live_base.entity_unit_numbers_at(
            client, surface, force, [position for _entity, position in targets],
        )
    except Exception:
        raise _lab_ownership_unknown(
            packet_id, "live entity identity could not be observed", targets,
        ) from None
    for entity, position in targets:
        if live.get(position) != evidence[position][1]:
            raise _lab_ownership_unknown(
                packet_id, "live entity identity does not match ownership evidence",
                targets,
            )
    return evidence


def build_labs(session, *, count: int, packs: dict[str, float]) -> dict:
    """One funded, persisted lab packet; retries reconcile only owned geometry.

    Configuration is fail-closed: the recovery record, planned positions and
    same-name presence never authorize touching an existing entity. A runtime
    transaction-owned observation must identify each built instance first.
    """
    from planners.research_labs import logistic_lab_row
    from orchestrator import autonomous_builder as ab
    from orchestrator import live_base
    from orchestrator.stage_services import _await_bot_built_infrastructure
    from orchestrator.work_state import WorkStateSignal
    from orchestrator.extraction_transport import planned_footprint_tiles
    if type(count) is not int or not 1 <= count <= 256:
        raise ValueError("explicit lab target must be 1..256")
    c, b, emit = session.client, session.bridge, session.emit
    surface, force = "nauvis", "player"
    ledger = ab._MATERIAL_RESERVATION_LEDGER
    if ledger is None:
        raise ValueError("lab construction requires an owned episode ledger")
    if ledger.surface != surface or ledger.force != force:
        raise ValueError("lab construction ledger scope does not match this run")
    path = ledger.path.with_suffix(".research-labs.json")
    if path.exists():
        record = _load_lab_record(
            path, episode_id=ledger.episode_id, surface=surface, force=force)
    else:
        baseline_labs, baseline_tick = _lab_baseline(c, b, surface=surface, force=force)
        record = {"version": LAB_RECORD_VERSION, "episode_id": ledger.episode_id,
                  "surface": surface, "force": force, "baseline_labs": baseline_labs,
                  "baseline_tick": baseline_tick, "packets": []}
    committed = sum(p["count"] for p in record["packets"])
    owned_target = max(0, count - record["baseline_labs"])
    packet = next((p for p in record["packets"] if not p["complete"]), None)
    if packet is None and committed < owned_target:
        amount = min(LAB_PACKET_LIMIT, owned_target - committed)
        area = live_base.find_clear_area(c, surface, session.reference_point, amount * 3 + 2, 7,
                                         avoid_resources=True, resource_clearance=5)
        if area is None:
            raise WorkStateSignal("No legal lab site", code="research_lab_site_wait", state="planned", classification="intended_difficulty")
        origin = (round(area[0]) + 1, round(area[1]) + 1)
        packet = {"count": amount, "id": f"research_labs_{committed}", "transaction_id": uuid.uuid4().hex, "complete": False,
                  "plan": logistic_lab_row(amount, packs, origin=origin)}
        packet["plan"].update(surface=surface, force=force)
        record["packets"].append(packet)
        _write_lab_record(path, record)
    if packet is None:
        # Re-submit owned plans to reconcile damage/configuration, never choose a new site.
        packets = record["packets"]
    else:
        packets = [packet]
    for packet in packets:
        plan = packet["plan"]
        # Current live ingredient requirements refresh existing requester configuration.
        template = logistic_lab_row(packet["count"], packs)
        requests = next(a["logistic_sections"] for a in template["phases"][0]["actions"]
                        if a["entity"] == "requester-chest")
        for a in plan["phases"][0]["actions"]:
            if a["entity"] == "requester-chest":
                a["logistic_sections"] = requests
        actions = plan["phases"][0]["actions"]
        # A retry may meet entities from an earlier/unrelated attempt at these
        # exact positions. Require runtime transaction ownership before the
        # submission can reconcile (and therefore configure) them.
        occupied = _lab_occupants(c, surface, force, actions)
        occupied_targets = [
            (action["entity"], _lab_position(action))
            for action in actions if _lab_position(action) in occupied
        ]
        _require_lab_ownership(
            session, c, packet_id=packet["id"], transaction_id=packet["transaction_id"], episode_id=ledger.episode_id,
            surface=surface, force=force, targets=occupied_targets,
            baseline_tick=record["baseline_tick"],
        )
        # Construction must not also be a configuration path: the executor
        # configures an already-built entity it finds at a ghost's position, so
        # submit a settings-free copy. Configuration happens only below, after
        # ownership evidence proves which instances this packet built.
        build_plan = copy.deepcopy(plan)
        for action in build_plan["phases"][0]["actions"]:
            action["action_type"] = "place_ghost_no_reconcile"
            action["research_lab_ownership"] = {
                "research_lab_episode": ledger.episode_id,
                "research_lab_packet": packet["id"],
                "research_lab_transaction": packet["transaction_id"],
            }
            for field in _LAB_CONFIGURATION_FIELDS:
                action.pop(field, None)
        ab._submit(c, b, surface, build_plan, packet["id"], emit, reservation_priority=50, require_funded=True,
                   stage_coverage=lambda: ab._ensure_plan_construction_coverage(
                       c, b, surface, force, build_plan, emit, reserved_tiles=planned_footprint_tiles(build_plan)))
        points = lambda entity: [(a["position"]["x"], a["position"]["y"]) for a in actions if a["entity"] == entity]
        _await_bot_built_infrastructure(c, surface, force,
            [(a["entity"], (a["position"]["x"], a["position"]["y"])) for a in actions], emit, owner=packet["id"])
        # Name-at-position is only a construction wait. Configuration still
        # requires the packet's own built-entity identity; otherwise fail
        # closed instead of configuring whatever now stands there.
        owned_entities = _require_lab_ownership(
            session, c, packet_id=packet["id"], transaction_id=packet["transaction_id"], episode_id=ledger.episode_id,
            surface=surface, force=force,
            targets=[(action["entity"], _lab_position(action)) for action in actions],
            baseline_tick=record["baseline_tick"],
        )
        configuration = [
            {**a, "action_type": "configure_entity_if_unit_number",
             "expected_unit_number": owned_entities[_lab_position(a)][1]}
            for a in actions if a["entity"] == "requester-chest"
        ]
        ab._submit(c, b, surface, {"surface": surface, "force": force,
                   "phases": [{"name": "configure_owned_labs", "actions": configuration}]},
                   packet["id"] + "_requests", emit)
        for p in points("medium-electric-pole"):
            ab.extend_power(c, b, surface, force, p, emit)
        ab.ensure_logistic_coverage(c, b, surface, force, points("requester-chest"), emit)
        packet["complete"] = True
        _write_lab_record(path, record)
    return {"ok": True, "code": "research_labs_submitted", "target_count": count,
            "committed": sum(p["count"] for p in record["packets"])}


def scale_production(session, *, item: str, machines: int, stock_target: int) -> object:
    """Request explicit capacity from the shared line operation, bypassing its output cache."""
    from orchestrator import autonomous_builder as ab
    if type(machines) is not int or not 1 <= machines <= 256 or type(stock_target) is not int or stock_target < 1:
        raise ValueError("invalid selected capacity")
    if item not in ab.LINE_RECIPES:
        raise ValueError("recipe is not supported by shared production tooling")
    # A cached producing endpoint proves output, not the newly selected machine count.
    ab.MANAGED_INTERMEDIATE_SOURCES.pop(item, None)
    return ab.ensure_produced(session.client, session.bridge, "nauvis", "player", item,
                             session.reference_point, session.emit, upgrade_bootstrap=False,
                             stock_target=stock_target, minimum_machines=machines, allow_promotion=True,
                             storage_limit=stock_target)
