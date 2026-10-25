# Path: orchestrator/research_operations.py | Purpose: Observe and explicitly select force-scoped research without running a research cascade.
"""Shared research mechanics. No construction, grants, or strategy live here."""
from __future__ import annotations

import math
import json
from collections.abc import Mapping

from orchestrator.game_bridge import load_json
from orchestrator.science_diagnosis import diagnose_science_status, report_identity
from orchestrator.work_state import WorkStateSignal

MAX_REPORT_AGE = 300
OBSERVATION_VERSION = "research-observation-v1"


def _number(value, *, minimum=0):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= minimum)


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
    the live queue never calls the destructive setter. No prerequisite is selected.
    """
    def fail(code, message):
        raise WorkStateSignal(message, code=code, classification="intended_difficulty", state="supply_wait")

    if not force or not technology or not isinstance(expected_queue, list) or not isinstance(replace_queue, bool):
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
        submission = load_json(bridge.set_research(technology, force=force))
        if (submission.get("ok") is not True or submission.get("force") != force
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


def build_labs(session, *, count: int, packs: dict[str, float]) -> dict:
    """One funded, persisted lab packet; retries reconcile the same geometry."""
    import json
    from pathlib import Path
    from planners.research_labs import logistic_lab_row
    from orchestrator import autonomous_builder as ab
    from orchestrator import live_base
    from orchestrator.construction_coverage import _await_bot_built_infrastructure
    from orchestrator.work_state import WorkStateSignal
    from orchestrator.extraction_transport import planned_footprint_tiles
    if type(count) is not int or not 1 <= count <= 256:
        raise ValueError("explicit lab target must be 1..256")
    c, b, emit = session.client, session.bridge, session.emit
    surface, force = "nauvis", "player"
    ledger = ab._MATERIAL_RESERVATION_LEDGER
    if ledger is None:
        raise ValueError("lab construction requires an owned episode ledger")
    path = ledger.path.with_suffix(".research-labs.json")
    if path.exists():
        record = json.loads(path.read_text())
    else:
        baseline_labs = 0
        if callable(getattr(b, "science_status", None)):
            status = load_json(b.science_status(surface, force))
            identity = report_identity(status)
            if identity["surface"] != surface or identity["force"] != force:
                raise ValueError("lab baseline scope mismatch")
            baseline_labs = status["labs"]["total"]
            if type(baseline_labs) is not int or baseline_labs < 0:
                raise ValueError("lab baseline unavailable")
        record = {"packets": [], "baseline_labs": baseline_labs}
    committed = sum(p["count"] for p in record["packets"])
    owned_target = max(0, count - record.get("baseline_labs", 0))
    packet = next((p for p in record["packets"] if not p.get("complete")), None)
    if packet is None and committed < owned_target:
        amount = min(6, owned_target - committed)
        area = live_base.find_clear_area(c, surface, session.reference_point, amount * 3 + 2, 7,
                                         avoid_resources=True, resource_clearance=5)
        if area is None:
            raise WorkStateSignal("No legal lab site", code="research_lab_site_wait", state="planned", classification="intended_difficulty")
        origin = (round(area[0]) + 1, round(area[1]) + 1)
        packet = {"count": amount, "id": f"research_labs_{committed}", "plan": logistic_lab_row(amount, packs, origin=origin)}
        packet["plan"].update(surface=surface, force=force)
        record["packets"].append(packet)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, sort_keys=True))
        temporary.replace(path)
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
        ab._submit(c, b, surface, plan, packet["id"], emit, reservation_priority=50, require_funded=True,
                   stage_coverage=lambda: ab._ensure_plan_construction_coverage(
                       c, b, surface, force, plan, emit, reserved_tiles=planned_footprint_tiles(plan)))
        actions = plan["phases"][0]["actions"]
        points = lambda entity: [(a["position"]["x"], a["position"]["y"]) for a in actions if a["entity"] == entity]
        _await_bot_built_infrastructure(c, surface, force,
            [(a["entity"], (a["position"]["x"], a["position"]["y"])) for a in actions], emit, owner=packet["id"])
        configuration = [{**a, "action_type": "configure_entity"} for a in actions if a["entity"] == "requester-chest"]
        ab._submit(c, b, surface, {"surface": surface, "force": force,
                   "phases": [{"name": "configure_owned_labs", "actions": configuration}]},
                   packet["id"] + "_requests", emit)
        for p in points("medium-electric-pole"):
            ab.extend_power(c, b, surface, force, p, emit)
        ab.ensure_logistic_coverage(c, b, surface, force, points("requester-chest"), emit)
        packet["complete"] = True
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, sort_keys=True))
        temporary.replace(path)
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
