# Path: tools/verify_factory_invariants.py
# Purpose: Independent, measurement-based acceptance harness for milestone M6 -
#          measures the six live-server invariants from the planning/execution contract
#          directly over RCON. Never infers; reports "unknown" for anything it cannot measure.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tools.rcon_client import RconClient, RconError

UNKNOWN = "unknown"
PASS = "pass"
FAIL = "fail"

CRITERIA = (
    "energy_interface_count",
    "electric_networks",
    "logistic_networks",
    "pending_ghosts",
    "fluid_machines",
    "idempotency",
)

# Entity types sampled for criterion 2 (electric_network_id). Chosen to cover
# "machines, drills, inserters and roboports, poles" per the M6 brief plus a
# few more powered categories so a stray unsampled network is less likely.
_ELECTRIC_SAMPLE_TYPES = (
    "assembling-machine",
    "furnace",
    "lab",
    "mining-drill",
    "inserter",
    "roboport",
    "electric-pole",
    "pump",
    "offshore-pump",
    "radar",
    "beacon",
)

_FLUID_MACHINE_TYPES = ("oil-refinery", "chemical-plant", "assembling-machine")


class MeasurementError(RuntimeError):
    """Raised when a query could not be executed or its response could not be parsed."""


def _run_query(client: RconClient, lua_body: str) -> str:
    """Send a Lua chunk via /sc and return the raw response body (may be empty)."""
    command = "/sc " + lua_body
    if len(command) > 1500:
        raise MeasurementError(f"query exceeds 1500 chars ({len(command)}); split it")
    try:
        response = client.command(command)
    except (RconError, OSError) as exc:
        raise MeasurementError(f"RCON command failed: {exc}") from exc
    return response.strip()


def _parse_json_response(raw: str) -> Any:
    """Parse a helpers.table_to_json response. Empty Lua tables serialize as '{}'
    regardless of whether the caller meant an array or an object; callers that
    expect a list must tolerate a bare '{}' meaning 'empty list'."""
    if not raw:
        raise MeasurementError("empty RCON response")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MeasurementError(f"unparseable RCON response: {raw!r} ({exc})") from exc


def _as_list(value: Any) -> list:
    if value == {}:
        return []
    if isinstance(value, list):
        return value
    raise MeasurementError(f"expected a JSON list, got {value!r}")


# ---------------------------------------------------------------------------
# Criterion 1: exactly one electric-energy-interface
# ---------------------------------------------------------------------------

def measure_energy_interface_count(client: RconClient, surface: str) -> dict:
    lua = (
        f"local s = game.surfaces['{surface}']\n"
        "if not s then rcon.print('ERROR:no-surface') return end\n"
        "rcon.print(s.count_entities_filtered{name='electric-energy-interface'})"
    )
    raw = _run_query(client, lua)
    if raw == "ERROR:no-surface":
        return {
            "status": UNKNOWN,
            "detail": f"surface '{surface}' does not exist",
            "count": None,
        }
    try:
        count = int(raw)
    except ValueError as exc:
        raise MeasurementError(f"expected an integer count, got {raw!r}") from exc
    status = PASS if count == 1 else FAIL
    return {"status": status, "count": count, "expected": 1}


# ---------------------------------------------------------------------------
# Criterion 2: exactly one distinct electric_network_id across a broad sample
# ---------------------------------------------------------------------------

def measure_electric_networks(client: RconClient, surface: str) -> dict:
    types_literal = ",".join(f"'{t}'" for t in _ELECTRIC_SAMPLE_TYPES)
    lua = (
        f"local s = game.surfaces['{surface}']\n"
        "if not s then rcon.print('ERROR:no-surface') return end\n"
        f"local types = {{{types_literal}}}\n"
        "local nets = {}\n"
        "local order = {}\n"
        "local sampled = 0\n"
        "for _, t in ipairs(types) do\n"
        "  for _, e in pairs(s.find_entities_filtered{type = t}) do\n"
        "    sampled = sampled + 1\n"
        "    local ok, id = pcall(function() return e.electric_network_id end)\n"
        "    if ok and id then\n"
        "      if not nets[id] then nets[id] = {}; order[#order + 1] = id end\n"
        "      if #nets[id] < 3 then\n"
        "        nets[id][#nets[id] + 1] = {name = e.name, position = e.position}\n"
        "      end\n"
        "    end\n"
        "  end\n"
        "end\n"
        "local out = {}\n"
        "for _, id in ipairs(order) do out[#out + 1] = {id = id, examples = nets[id]} end\n"
        "rcon.print(helpers.table_to_json({sampled = sampled, networks = out}))"
    )
    raw = _run_query(client, lua)
    if raw == "ERROR:no-surface":
        return {"status": UNKNOWN, "detail": f"surface '{surface}' does not exist"}
    parsed = _parse_json_response(raw)
    if not isinstance(parsed, dict) or "networks" not in parsed:
        raise MeasurementError(f"malformed electric_networks response: {raw!r}")
    networks = _as_list(parsed["networks"])
    ids = sorted({n["id"] for n in networks})
    sampled = parsed.get("sampled", 0)
    if sampled == 0:
        return {
            "status": UNKNOWN,
            "detail": "no powered entities found to sample; cannot measure",
            "sampled": 0,
            "network_ids": [],
            "networks": [],
        }
    status = PASS if len(ids) == 1 else FAIL
    return {
        "status": status,
        "sampled": sampled,
        "network_ids": ids,
        "networks": networks,
        "expected_distinct_networks": 1,
    }


# ---------------------------------------------------------------------------
# Criterion 3: every roboport reports the same logistic_network.network_id
# ---------------------------------------------------------------------------

def measure_logistic_networks(client: RconClient, surface: str) -> dict:
    lua = (
        f"local s = game.surfaces['{surface}']\n"
        "if not s then rcon.print('ERROR:no-surface') return end\n"
        "local counts = {}\n"
        "local order = {}\n"
        "local total = 0\n"
        "local without_network = 0\n"
        "for _, e in pairs(s.find_entities_filtered{type = 'roboport'}) do\n"
        "  total = total + 1\n"
        "  local ln = e.logistic_network\n"
        "  local id = ln and ln.network_id or nil\n"
        "  if id then\n"
        "    if not counts[id] then counts[id] = 0; order[#order + 1] = id end\n"
        "    counts[id] = counts[id] + 1\n"
        "  else\n"
        "    without_network = without_network + 1\n"
        "  end\n"
        "end\n"
        "local out = {}\n"
        "for _, id in ipairs(order) do out[#out + 1] = {id = id, count = counts[id]} end\n"
        "rcon.print(helpers.table_to_json({total_roboports = total, without_network = without_network, networks = out}))"
    )
    raw = _run_query(client, lua)
    if raw == "ERROR:no-surface":
        return {"status": UNKNOWN, "detail": f"surface '{surface}' does not exist"}
    parsed = _parse_json_response(raw)
    if not isinstance(parsed, dict) or "networks" not in parsed:
        raise MeasurementError(f"malformed logistic_networks response: {raw!r}")
    networks = _as_list(parsed["networks"])
    total = parsed.get("total_roboports", 0)
    without_network = parsed.get("without_network", 0)
    ids = sorted({n["id"] for n in networks})
    if total == 0:
        return {
            "status": UNKNOWN,
            "detail": "no roboports found; cannot measure logistic networks",
            "total_roboports": 0,
            "network_ids": [],
        }
    status = PASS if (len(ids) == 1 and without_network == 0) else FAIL
    return {
        "status": status,
        "total_roboports": total,
        "without_network": without_network,
        "network_ids": ids,
        "networks": networks,
        "expected_distinct_networks": 1,
    }


# ---------------------------------------------------------------------------
# Criterion 4: zero entity-ghost, or a diagnosis of each stuck one
# ---------------------------------------------------------------------------

def measure_pending_ghosts(client: RconClient, surface: str, limit: int = 10) -> dict:
    lua = (
        f"local s=game.surfaces['{surface}']\n"
        "if not s then rcon.print('ERROR:no-surface') return end\n"
        "local rp=s.find_entities_filtered{type='roboport'}\n"
        "local gh=s.find_entities_filtered{name='entity-ghost'}\n"
        "local out={}\n"
        f"local lim={int(limit)}\n"
        "for i,g in pairs(gh) do\n"
        " if i>lim then break end\n"
        " local ok1,can=pcall(function() return s.can_place_entity{name=g.ghost_name,"
        "position=g.position,direction=g.direction,force=g.force,"
        "build_check_type=defines.build_check_type.ghost_revive} end)\n"
        " local ln=s.find_logistic_network_by_position(g.position,g.force)\n"
        " local iname=nil\n"
        " local ok2,it=pcall(function() return g.ghost_prototype.items_to_place_this end)\n"
        " if ok2 and it and it[1] then iname=it[1].name end\n"
        " local icnt=nil\n"
        " if ln and iname then\n"
        "  local ok3,c=pcall(function() return ln.get_item_count(iname) end)\n"
        "  if ok3 then icnt=c end\n"
        " end\n"
        " local rng=false\n"
        " for _,r in pairs(rp) do\n"
        "  local cell=r.logistic_cell\n"
        "  if cell then\n"
        "   local rad=cell.construction_radius\n"
        "   local dx=g.position.x-r.position.x\n"
        "   local dy=g.position.y-r.position.y\n"
        "   if (dx*dx+dy*dy)<=(rad*rad) then rng=true end\n"
        "  end\n"
        " end\n"
        " out[#out+1]={ghost_name=g.ghost_name,position=g.position,"
        "can_place=(ok1 and can) or false,in_logistic_network=(ln~=nil),"
        "item_name=iname,item_count=icnt,in_construction_range=rng}\n"
        "end\n"
        "rcon.print(helpers.table_to_json({total=#gh,sample=out}))"
    )
    raw = _run_query(client, lua)
    if raw == "ERROR:no-surface":
        return {"status": UNKNOWN, "detail": f"surface '{surface}' does not exist"}
    parsed = _parse_json_response(raw)
    if not isinstance(parsed, dict) or "total" not in parsed:
        raise MeasurementError(f"malformed pending_ghosts response: {raw!r}")
    total = parsed["total"]
    sample = _as_list(parsed.get("sample", []))
    reasons = []
    for ghost in sample:
        problems = []
        if not ghost.get("can_place", False):
            problems.append("tile blocked")
        if not ghost.get("in_construction_range", False):
            problems.append("outside any roboport construction range")
        item_name = ghost.get("item_name")
        item_count = ghost.get("item_count")
        if item_name is None:
            problems.append("could not determine required item")
        elif not ghost.get("in_logistic_network", False):
            problems.append(f"not covered by any logistic network (needs {item_name})")
        elif not item_count:
            problems.append(f"item '{item_name}' unavailable in covering logistic network")
        if not problems:
            problems.append("no obvious blocker found (may just be waiting for a bot)")
        reasons.append({**ghost, "reasons": problems})
    status = PASS if total == 0 else FAIL
    return {
        "status": status,
        "count": total,
        "expected": 0,
        "sample": reasons,
        "sample_truncated": total > len(sample),
    }


# ---------------------------------------------------------------------------
# Criterion 5: every fluid machine's input box has amount > 0; refineries 'working'
# ---------------------------------------------------------------------------

def measure_fluid_machines(client: RconClient, surface: str) -> dict:
    types_literal = ",".join(f"'{t}'" for t in _FLUID_MACHINE_TYPES)
    lua = (
        f"local s = game.surfaces['{surface}']\n"
        "if not s then rcon.print('ERROR:no-surface') return end\n"
        "local inv = {}\n"
        "for k, v in pairs(defines.entity_status) do inv[v] = k end\n"
        f"local types = {{{types_literal}}}\n"
        "local out = {}\n"
        "for _, t in ipairs(types) do\n"
        "  for _, e in pairs(s.find_entities_filtered{type = t}) do\n"
        "    local fb = e.fluidbox\n"
        "    if fb and #fb > 0 then\n"
        "      local boxes = {}\n"
        "      for i = 1, #fb do\n"
        "        local fluid = fb[i]\n"
        "        local proto = fb.get_prototype(i)\n"
        "        local ptype = proto and proto.production_type or 'unknown'\n"
        "        boxes[#boxes + 1] = {index = i, production_type = ptype, "
        "fluid_name = fluid and fluid.name or nil, amount = fluid and fluid.amount or 0}\n"
        "      end\n"
        "      out[#out + 1] = {name = e.name, position = e.position, "
        "status = inv[e.status] or tostring(e.status), boxes = boxes}\n"
        "    end\n"
        "  end\n"
        "end\n"
        "rcon.print(helpers.table_to_json(out))"
    )
    raw = _run_query(client, lua)
    if raw == "ERROR:no-surface":
        return {"status": UNKNOWN, "detail": f"surface '{surface}' does not exist"}
    parsed = _parse_json_response(raw)
    machines = _as_list(parsed)
    if not machines:
        return {
            "status": UNKNOWN,
            "detail": "no fluid-handling machines found; cannot measure",
            "machines": [],
        }
    violations = []
    for machine in machines:
        for box in machine.get("boxes", []):
            production_type = box.get("production_type")
            if production_type in ("input", "input-output"):
                amount = box.get("amount", 0)
                if not amount or amount <= 0:
                    violations.append(
                        f"{machine['name']} at {machine['position']}: input box "
                        f"{box['index']} ({box.get('fluid_name')}) amount={amount}"
                    )
        if machine.get("name") == "oil-refinery" and machine.get("status") != "working":
            violations.append(
                f"refinery at {machine['position']}: status={machine.get('status')} (expected working)"
            )
    status = PASS if not violations else FAIL
    return {"status": status, "machines": machines, "violations": violations}


# ---------------------------------------------------------------------------
# Criterion 6: idempotency via entity-name counts, baseline/compare
# ---------------------------------------------------------------------------

def measure_entity_counts(client: RconClient, surface: str) -> dict:
    lua = (
        f"local s = game.surfaces['{surface}']\n"
        "if not s then rcon.print('ERROR:no-surface') return end\n"
        "local counts = {}\n"
        "for _, e in pairs(s.find_entities_filtered{}) do\n"
        "  counts[e.name] = (counts[e.name] or 0) + 1\n"
        "end\n"
        "rcon.print(helpers.table_to_json(counts))"
    )
    raw = _run_query(client, lua)
    if raw == "ERROR:no-surface":
        raise MeasurementError(f"surface '{surface}' does not exist")
    parsed = _parse_json_response(raw)
    if parsed == {}:
        return {}
    if not isinstance(parsed, dict):
        raise MeasurementError(f"malformed entity-count response: {raw!r}")
    return {str(k): int(v) for k, v in parsed.items()}


def diff_entity_counts(baseline: dict, current: dict) -> dict:
    added: dict[str, int] = {}
    removed: dict[str, int] = {}
    for name in set(baseline) | set(current):
        delta = current.get(name, 0) - baseline.get(name, 0)
        if delta > 0:
            added[name] = delta
        elif delta < 0:
            removed[name] = -delta
    return {"added": added, "removed": removed}


def measure_idempotency(
    client: RconClient,
    surface: str,
    baseline_path: str | None,
    compare_path: str | None,
) -> dict:
    try:
        current = measure_entity_counts(client, surface)
    except MeasurementError as exc:
        return {"status": UNKNOWN, "detail": str(exc)}

    result: dict[str, Any] = {"counts": current}

    if baseline_path:
        Path(baseline_path).write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
        result["baseline_written"] = baseline_path

    if compare_path:
        try:
            baseline_raw = Path(compare_path).read_text(encoding="utf-8")
        except OSError as exc:
            result["status"] = UNKNOWN
            result["detail"] = f"could not read baseline '{compare_path}': {exc}"
            return result
        try:
            baseline = json.loads(baseline_raw)
        except json.JSONDecodeError as exc:
            result["status"] = UNKNOWN
            result["detail"] = f"baseline '{compare_path}' is not valid JSON: {exc}"
            return result
        if not isinstance(baseline, dict):
            result["status"] = UNKNOWN
            result["detail"] = f"baseline '{compare_path}' is not an object"
            return result
        diff = diff_entity_counts(baseline, current)
        result["diff"] = diff
        result["baseline_source"] = compare_path
        result["status"] = PASS if not diff["added"] and not diff["removed"] else FAIL
        return result

    if baseline_path and not compare_path:
        result["status"] = UNKNOWN
        result["detail"] = "baseline recorded; rerun with --compare against it to measure idempotency"
        return result

    result["status"] = UNKNOWN
    result["detail"] = "idempotency needs two runs; pass --baseline on the first and --compare on the second"
    return result


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_all(
    client: RconClient,
    surface: str,
    *,
    baseline_path: str | None = None,
    compare_path: str | None = None,
    ghost_limit: int = 10,
) -> dict:
    report: dict[str, Any] = {}

    def measured(name: str, func) -> None:
        try:
            report[name] = func()
        except MeasurementError as exc:
            report[name] = {"status": UNKNOWN, "detail": str(exc)}

    measured("energy_interface_count", lambda: measure_energy_interface_count(client, surface))
    measured("electric_networks", lambda: measure_electric_networks(client, surface))
    measured("logistic_networks", lambda: measure_logistic_networks(client, surface))
    measured("pending_ghosts", lambda: measure_pending_ghosts(client, surface, ghost_limit))
    measured("fluid_machines", lambda: measure_fluid_machines(client, surface))
    measured(
        "idempotency",
        lambda: measure_idempotency(client, surface, baseline_path, compare_path),
    )

    statuses = {name: report[name]["status"] for name in CRITERIA}
    if FAIL in statuses.values():
        verdict = FAIL
    elif UNKNOWN in statuses.values():
        verdict = UNKNOWN
    else:
        verdict = PASS

    report["verdict"] = verdict
    report["criteria_status"] = statuses
    report["surface"] = surface
    return report


def format_report(report: dict) -> str:
    lines = [f"Surface: {report['surface']}", f"Verdict: {report['verdict'].upper()}", ""]

    eic = report["energy_interface_count"]
    lines.append(f"1. energy_interface_count: {eic['status'].upper()}")
    if "count" in eic:
        lines.append(f"   count={eic.get('count')} expected=1")
    if eic.get("detail"):
        lines.append(f"   {eic['detail']}")

    en = report["electric_networks"]
    lines.append(f"2. electric_networks: {en['status'].upper()}")
    if "network_ids" in en:
        lines.append(f"   sampled={en.get('sampled')} distinct_ids={en['network_ids']}")
        for net in en.get("networks", []):
            examples = ", ".join(
                f"{ex['name']}@({ex['position'].get('x')},{ex['position'].get('y')})"
                for ex in net.get("examples", [])
            )
            lines.append(f"     network {net['id']}: {examples}")
    if en.get("detail"):
        lines.append(f"   {en['detail']}")

    ln = report["logistic_networks"]
    lines.append(f"3. logistic_networks: {ln['status'].upper()}")
    if "network_ids" in ln:
        lines.append(
            f"   total_roboports={ln.get('total_roboports')} "
            f"without_network={ln.get('without_network')} distinct_ids={ln['network_ids']}"
        )
    if ln.get("detail"):
        lines.append(f"   {ln['detail']}")

    pg = report["pending_ghosts"]
    lines.append(f"4. pending_ghosts: {pg['status'].upper()}")
    if "count" in pg:
        lines.append(f"   count={pg.get('count')} expected=0")
        for ghost in pg.get("sample", []):
            lines.append(
                f"     {ghost['ghost_name']} @ {ghost['position']}: {'; '.join(ghost['reasons'])}"
            )
    if pg.get("detail"):
        lines.append(f"   {pg['detail']}")

    fm = report["fluid_machines"]
    lines.append(f"5. fluid_machines: {fm['status'].upper()}")
    if "machines" in fm:
        lines.append(f"   machines_measured={len(fm['machines'])}")
        for violation in fm.get("violations", []):
            lines.append(f"     VIOLATION: {violation}")
    if fm.get("detail"):
        lines.append(f"   {fm['detail']}")

    idem = report["idempotency"]
    lines.append(f"6. idempotency: {idem['status'].upper()}")
    if "diff" in idem:
        lines.append(f"   added={idem['diff']['added']} removed={idem['diff']['removed']}")
    if idem.get("detail"):
        lines.append(f"   {idem['detail']}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Independent measurement-based acceptance harness for milestone M6 "
        "factory invariants, checked live over RCON."
    )
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--rcon-password", required=True)
    parser.add_argument("--surface", default="planner-sandbox")
    parser.add_argument("--ghost-limit", type=int, default=10)
    parser.add_argument("--baseline", help="Write current entity-name counts to this path")
    parser.add_argument("--compare", help="Diff current entity-name counts against this baseline path")
    parser.add_argument("--json", action="store_true", help="Emit the machine-readable report as JSON")
    args = parser.parse_args(argv)

    try:
        client = RconClient(args.rcon_host, args.rcon_port, args.rcon_password)
    except (RconError, OSError) as exc:
        print(f"Could not connect to RCON at {args.rcon_host}:{args.rcon_port}: {exc}")
        return 3

    try:
        report = run_all(
            client,
            args.surface,
            baseline_path=args.baseline,
            compare_path=args.compare,
            ghost_limit=args.ghost_limit,
        )
    finally:
        client.close()

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))

    if report["verdict"] == PASS:
        return 0
    if report["verdict"] == FAIL:
        return 2
    return 3


if __name__ == "__main__":
    sys.exit(main())
