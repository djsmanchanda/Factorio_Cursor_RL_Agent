# Path: orchestrator/expansion_daemon.py
# Purpose: Autonomous expansion loop - measure the chain, diagnose bottlenecks, choose a catalog action, execute it, and log the transition for RL training.

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.action_catalog import build_catalog
from core.baseline_policy import choose_action, explain
from core.bottleneck_diagnosis import diagnose_line
from orchestrator.chain_telemetry import measure_chain, rate_tracker
from orchestrator.game_bridge import GameBridge, load_json
from planners.sandbox_infrastructure import (
    build_layout_authorization,
    compose_managed_sandbox,
    require_compatible_topology,
)
from planners.local_layout_planner import (
    BELT_TIERS, FEEDER_RATES, FEED_HEADROOM, LINE_RECIPES, MACHINE_SPEEDS, LocalLayoutPlanner,
)

# Research the agent can actually perform: everything reachable with the
# science pack it produces. Keeping a technology queued is what converts
# production into the terminal reward (docs/22).
ENSURE_RESEARCH = (
    "/sc local pf=game.forces['planner'] "
    "local done,units=0,0 "
    "for _,t in pairs(pf.technologies) do if t.researched then done=done+1 "
    "units=units+(t.research_unit_count or 0) end end "
    "if not pf.current_research then "
    "local best,bestcost=nil,nil "
    "for name,t in pairs(pf.technologies) do if not t.researched and t.enabled then "
    "local only,n=true,0 for _,i in pairs(t.research_unit_ingredients) do n=n+1 "
    "if i.name~='automation-science-pack' then only=false end end "
    "if only and n>0 and (bestcost==nil or t.research_unit_count<bestcost) then "
    "best=name bestcost=t.research_unit_count end end end "
    "if best then pf.add_research(best) end end "
    "rcon.print('techs_done='..done..' units_done='..units"
    "..' current='..tostring(pf.current_research and pf.current_research.name)"
    "..' progress='..string.format('%.4f',pf.research_progress))"
)


def _parse_kv(raw: str) -> dict:
    fields = {}
    for part in raw.split():
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key] = value
    return fields


def ensure_research(bridge: GameBridge) -> dict:
    """Keep a technology queued and report cumulative research progress."""
    return _parse_kv(bridge.command(ENSURE_RESEARCH).strip())


def _limits() -> dict:
    """Tier and rate tables for the catalog (core must not import planners)."""
    return {
        "feed_headroom": FEED_HEADROOM,
        "belt_tiers": dict(BELT_TIERS),
        "inserter_rates": dict(FEEDER_RATES),
        "machine_rates": {
            recipe: MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
            for recipe, spec in LINE_RECIPES.items()
        },
    }


def _diagnosis_spec(line: dict) -> dict:
    spec = LINE_RECIPES[line["recipe"]]
    rate = MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
    return {
        "name": line["name"],
        "machines": line["machines"],
        "theoretical_capacity": line["machines"] * rate,
        "mining_feed": line.get("mining_feed", False),
        "at_max_length": line.get("at_max_length", False),
    }


def relay_chain_link(line: dict, old_machines: int, new_machines: int, belt_type: str) -> dict:
    """Reconnect a grown producer to its EXISTING downstream descent column.

    Growing a chained line paves its output row straight through the old
    connector corner, which would strand every machine east of it. Rather than
    re-pinning the consumer (its x is fixed by the original junction math), the
    connector is relayed: the corner moves to the new east end, drops one row,
    and runs WEST along row 7 back into the old descent column - a west-facing
    belt sideload-merges into that south-facing tile, so the whole existing
    descent below it keeps working untouched.
    """
    ox, oy = line["origin"]
    out_row = oy + 6
    old_turn = ox + old_machines * 3 + 1
    new_turn = ox + new_machines * 3 + 1

    actions = [
        # The old corner is mid-belt now: make it flow east like the rest.
        {"action_type": "remove_entity", "entity": belt_type,
         "position": {"x": old_turn + 0.5, "y": out_row + 0.5}},
        {"action_type": "place_ghost", "entity": belt_type,
         "position": {"x": old_turn + 0.5, "y": out_row + 0.5}, "direction": "east"},
        # New corner at the new east end, dropping to the relay row.
        {"action_type": "place_ghost", "entity": belt_type,
         "position": {"x": new_turn + 0.5, "y": out_row + 0.5}, "direction": "south"},
        {"action_type": "place_ghost", "entity": belt_type,
         "position": {"x": new_turn + 0.5, "y": out_row + 1.5}, "direction": "west"},
    ]
    # Westward run back to the existing descent column (exclusive: that tile
    # already exists and faces south; we sideload into it).
    for x in range(old_turn + 1, new_turn):
        actions.append({"action_type": "place_ghost", "entity": belt_type,
                        "position": {"x": x + 0.5, "y": out_row + 1.5}, "direction": "west"})
    return {"phases": [{"name": f"relay_{line['name']}", "actions": actions}]}


def _prepare_managed_plan(
    bridge: GameBridge,
    plan: dict,
    chain: dict,
    materials: dict,
    ore_patches: list | None = None,
) -> tuple[dict, dict]:
    require_compatible_topology(bridge)
    composition = compose_managed_sandbox(
        [("action", plan)],
        chain["anchors"],
        materials,
        bots_per_roboport=50,
        ore_patches=ore_patches or [],
    )
    authorization = build_layout_authorization(
        composition["infrastructure"] + composition["plans"]
    )
    for name, infrastructure_plan in composition["infrastructure"]:
        report = load_json(bridge.build_layout(authorization, infrastructure_plan))
        if not report.get("ok"):
            raise RuntimeError(f"Managed infrastructure {name} failed: {report.get('error')}")
    bridge.ensure_scaffolding(composition["scaffolding"])
    return dict(composition["plans"])["action"], authorization

def execute_action(bridge: GameBridge, planner: LocalLayoutPlanner, chain: dict, action: dict) -> dict:
    """Execute a catalog action. Unsupported actions are reported, never faked."""
    if action is None:
        return {"ok": True, "detail": "no action chosen (chain healthy)"}

    name = action["action"]
    target = action.get("target_line")
    line = next((l for l in chain["lines"] if l["name"] == target), None)
    if line is None:
        return {"ok": False, "detail": f"unknown target line {target}"}


    if name == "add_collectors" and "lab_row" in line.get("consumers", []):
        # A line drained by labs is relieved by MORE LABS: extra lab capacity
        # consumes the backed-up packs, which is what raises research rate.
        labs = chain["lab_row"]
        new_labs = labs["labs"] + 2
        plan = planner.generate_lab_row(new_labs, labs["origin"][0], labs["origin"][1],
                                        belt_type=labs["belt_type"], inserter_type=labs["inserter_type"])
        materials = {item: count * 2 for item, count in planner.material_requirements(plan).items()}
        plan, authorization = _prepare_managed_plan(bridge, plan, chain, materials)
        report = load_json(bridge.build_layout(authorization, plan))
        if not report.get("ok"):
            return {"ok": False, "detail": report.get("error", "lab build failed")}
        labs["labs"] = new_labs
        return {"ok": True, "detail": f"lab_row: {new_labs - 2} -> {new_labs} labs "
                                      f"({report['placed_ghosts']} ghosts)"}

    if name != "extend_line_x":
        # Honest reporting: the decision is real even when the executor for
        # that action type is not built yet.
        return {"ok": False, "detail": f"executor for '{name}' not implemented yet"}

    new_machines = int(action["params"]["new_machines"])
    plan = planner.generate_line_extension(
        recipe=line["recipe"],
        current_machines=line["machines"],
        new_machines=new_machines,
        origin_x=line["origin"][0],
        origin_y=line["origin"][1],
        belt_type=line["belt_type"],
        inserter_type=line["inserter_type"],
        feed_style=line["feed_style"],
        mining_feed=line.get("mining_feed", False),
        has_terminal_collector=line.get("has_terminal_collector", True),
    )
    materials = {item: count * 2 for item, count in planner.material_requirements(plan).items()}

    ore_patches = []
    if line.get("mining_feed"):
        ox, oy = line["origin"]
        ore = LINE_RECIPES[line["recipe"]]["ingredients"][0]
        ore_patches.append({
            "item": ore, "x1": ox - 1, "y1": oy - 5,
            "x2": ox + new_machines * 3, "y2": oy - 1, "amount": 500000,
        })
    plan, authorization = _prepare_managed_plan(
        bridge, plan, chain, materials, ore_patches
    )
    report = load_json(bridge.build_layout(authorization, plan))
    if not report.get("ok"):
        return {"ok": False, "detail": report.get("error", "build failed")}

    detail = (f"{target}: {line['machines']} -> {new_machines} machines "
              f"({report['placed_ghosts']} ghosts)")

    # A chained-onward line must be reconnected or its new machines strand.
    if not line.get("has_terminal_collector", True) and line.get("consumers"):
        relay = relay_chain_link(line, line["machines"], new_machines, line["belt_type"])
        relay_authorization = build_layout_authorization([relay])
        relay_report = load_json(bridge.build_layout(relay_authorization, relay))
        detail += f"; relayed connector ({relay_report.get('placed_ghosts', 0)} belts)"

    line["machines"] = new_machines  # registry follows reality
    return {"ok": True, "detail": detail}


def _telemetry_spec(line: dict) -> dict:
    """Line spec enriched with what the telemetry needs to measure real rates."""
    spec = LINE_RECIPES[line["recipe"]]
    return {
        **line,
        "output_item": line["recipe"],
        "machine_rate": MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"],
    }


def run_step(bridge: GameBridge, planner: LocalLayoutPlanner, chain: dict, step: int,
             work_dir: Path, previous: dict | None = None, elapsed: float = 0.0) -> dict:
    research = ensure_research(bridge)
    raw = measure_chain(bridge, [_telemetry_spec(line) for line in chain["lines"]])

    # Real output rates come from Factorio's cumulative production statistics
    # compared across two steps; never fabricated on the first step.
    if previous and elapsed > 0:
        for name, rate in rate_tracker(previous, raw, elapsed).items():
            if name in raw and isinstance(rate, dict) and rate.get("output_rate_per_s") is not None:
                raw[name]["measured_output_per_s"] = rate["output_rate_per_s"]

    # Telemetry reports per-line plus "_global"; the catalog consumes
    # {"lines": ..., "global": ...}.
    per_line = {name: dict(value) for name, value in raw.items() if not name.startswith("_")}
    # Normalize telemetry into the keys diagnosis/catalog read: belt fills as
    # fractions, and theoretical capacity from the registry's machine counts.
    for line in chain["lines"]:
        measurement = per_line.setdefault(line["name"], {})
        for side in ("start", "end"):
            count = float(measurement.get(f"input_belt_{side}_count", 0) or 0)
            capacity = float(measurement.get(f"input_belt_{side}_capacity", 0) or 0)
            measurement[f"input_belt_{side}_fill"] = (count / capacity) if capacity > 0 else 0.0
        spec = LINE_RECIPES[line["recipe"]]
        rate = MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
        measurement["theoretical_capacity_per_s"] = line["machines"] * rate
        measurement.setdefault("measured_output_per_s", measurement.get("output_rate_per_s"))
        # A chained line has no terminal chest by design; telemetry reports
        # collectors_full=True when it finds none, which would fake a
        # permanent drain_limited verdict. Its drain is the chain link.
        if not line.get("has_terminal_collector", True):
            measurement["collectors_full"] = False
    measurements = {
        "lines": per_line,
        "global": {
            **raw.get("_global", {}),
            "target_product": chain["target_product"],
            "research_rate_units_per_s": float(research.get("units_done", 0) or 0),
        },
    }

    diagnosis = {}
    for line in chain["lines"]:
        diagnosis[line["name"]] = diagnose_line(_diagnosis_spec(line), per_line.get(line["name"], {}))

    catalog = build_catalog(chain["lines"], measurements, _limits())
    state = {"chain": chain["lines"], "measurements": measurements,
             "target_product": chain["target_product"],
             "diagnosis": diagnosis, "research": research}
    chosen = choose_action(catalog, state)
    sentence = explain(chosen, catalog, state) if chosen else "Chain is healthy; holding."

    result = execute_action(bridge, planner, chain, chosen)

    record = {
        "step": step,
        "timestamp": time.time(),
        "research": research,
        "chain": [dict(line) for line in chain["lines"]],
        "measurements": measurements,
        "diagnosis": diagnosis,
        "catalog": catalog,
        "chosen": chosen,
        "explain": sentence,
        "result": result,
    }
    (work_dir / f"step_{step:03d}.json").write_text(
        json.dumps(record, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous expansion loop driven by bottleneck diagnosis.")
    parser.add_argument("--script-output", required=True)
    parser.add_argument("--rcon-password", required=True)
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--chain", default=str(REPO_ROOT / "tests" / "fixtures" / "science_chain.json"))
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--interval", type=float, default=45.0)
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args()

    chain = load_json(Path(args.chain))
    work_dir = Path(args.work_dir) if args.work_dir else REPO_ROOT / "runs" / f"expansion_{int(time.time())}"
    work_dir.mkdir(parents=True, exist_ok=True)

    bridge = GameBridge(script_output=Path(args.script_output), host=args.rcon_host,
                        port=args.rcon_port, password=args.rcon_password)
    planner = LocalLayoutPlanner()
    previous_raw: dict | None = None
    last_time = 0.0
    try:
        require_compatible_topology(bridge)
        for step in range(1, args.steps + 1):
            now = time.monotonic()
            elapsed = (now - last_time) if last_time else 0.0
            record = run_step(bridge, planner, chain, step, work_dir, previous_raw, elapsed)
            previous_raw = record["measurements"]["lines"]
            last_time = now
            research = record["research"]
            print(f"[step {step:02d}] techs={research.get('techs_done')} "
                  f"units={research.get('units_done')} researching={research.get('current')} "
                  f"| {record['explain']} | {record['result']['detail']}")
            # Persist the registry so a restart resumes from reality.
            (work_dir / "chain_state.json").write_text(
                json.dumps(chain, indent=2, sort_keys=True), encoding="utf-8")
            if step < args.steps:
                time.sleep(args.interval)
        print(f"Run complete: {work_dir}")
        return 0
    finally:
        bridge.close()


if __name__ == "__main__":
    sys.exit(main())
