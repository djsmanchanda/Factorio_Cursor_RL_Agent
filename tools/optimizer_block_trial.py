# Path: tools/optimizer_block_trial.py | Purpose: Drive the requester-fed red-science optimizer block from survey to measured pack output on a prepared disposable saved world.
"""Phase 3 trial driver: survey -> site -> power -> bind -> reserve -> offer -> construct -> measure.

The driver runs against an already prepared, process-verified disposable saved
world whose clock is paused. It never starts or stops a server, never edits the
mod, and builds only through the existing optimizer path (ghosts funded by real
network stock and built by construction bots). The only item grant is the
explicit, receipted ``--prestock`` test arm. A successful report is a single
disposable-world observation, not a cohort result and not production authority.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.optimizer_construction_state import ConstructionScope
from planners.plan_validation import actions
from tools.optimizer_block_proposals import (  # noqa: F401  (re-exported: the trial's tests and callers use these names)
    ANCHOR_ENTITY, BOUNDARIES, FORCE, LINE_SPEC, MACHINES, PACK, POLE_ENTITY, PREFERENCES, SURFACE, TARGET_RATE,
    Context, LuaError, Template, TrialError, UnfundedError, _BLOCK_STATE_LUA, _ENTITIES_LUA, _PRELUDE, _as_list, _short,
    attempt_site, block_state, block_template, build_candidate_artifact, capture_catalogs, choose_port, confirm_requests,
    known_tiles, line_spec, logistic_window, lua_json, origin_candidates, placement_capture, plan_demand, pole_reach,
    power_anchors, requested_items, screen_origin, search_sites, state_specs, survey_entities)
from tools.optimizer_saved_world import optimizer_candidate_config, execute_optimizer_candidate
from training.laya_layout_stream import atomic_json
from training.laya_saved_world import SavedWorldAdapter, SavedWorldError, file_hash, measured_outcome, owned_root, verify_instance


REPORT_VERSION = "optimizer-block-trial-v1"
DEFAULT_PRESTOCK = {"transport-belt": 50, "copper-plate": 600, "iron-gear-wheel": 600}
_EPISODE = re.compile(r"[A-Za-z0-9_-]{1,58}")
_ITEM_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


# --------------------------------------------------------------------------- Lua access


_PRESTOCK_LUA = (
    'local matches={}; '
    'for _,e in pairs(surface.find_entities_filtered{name="storage-chest",force=force}) do '
    'local net=surface.find_logistic_network_by_position(e.position,force); '
    'if net and tostring(net.network_id)==request.network then matches[#matches+1]=e end end; '
    'if #matches~=1 then error("expected exactly one storage-chest in network "..request.network.." found "..#matches) end; '
    'local chest=matches[1]; local inv=chest.get_inventory(defines.inventory.chest); local before=contents(inv); '
    'local reported={}; for _,item in ipairs(request.items) do reported[item.name]=inv.insert{name=item.name,count=item.count} end; '
    'return {tick=game.tick,unit_number=chest.unit_number,position=chest.position,before=before,'
    'inserted_reported=reported,after=contents(inv)}')



def verify_world(root):
    """Refuse unless the root is an owned, process-verified, non-production disposable saved world."""
    try:
        root = owned_root(Path(root))
        manifest = verify_instance(root)
    except (SavedWorldError, OSError, ValueError, KeyError) as error:
        raise TrialError(f"refusing: not a verified disposable saved world: {error}") from error
    if manifest.get("production_authority") is not False or manifest.get("runtime") != "disposable_saved_world":
        raise TrialError("refusing: manifest does not declare a non-production disposable saved world")
    return root, manifest


def git_commit():
    def run(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
    try:
        head, status = run("rev-parse", "HEAD"), run("status", "--porcelain")
        return {"head": head.stdout.strip() or None, "dirty": bool(status.stdout.strip())
                if status.returncode == 0 else None}
    except (OSError, subprocess.SubprocessError):
        return {"head": None, "dirty": None}


# --------------------------------------------------------------------------- prestock (test-only grant)

def prestock_receipts(root):
    """Every prestock receipt with its status; unreadable receipts count as blocking."""
    result = []
    for path in sorted((Path(root) / "evidence").glob("prestock-*.json")):
        try:
            result.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            result.append((path, {"status": "unreadable"}))
    return result


def trial_kind(root):
    """``block_test_prestocked`` whenever a granted receipt exists, even if granted by an earlier run."""
    done = [path for path, receipt in prestock_receipts(root) if receipt.get("status") in {"completed", "partial"}]
    return ("block_test_prestocked", str(done[-1])) if done else ("fresh_start", None)


def parse_items(values):
    items = {}
    for value in values:
        name, _, count = value.partition("=")
        if not _ITEM_NAME.fullmatch(name) or not count.isdigit() or int(count) <= 0 or name in items:
            raise TrialError(f"invalid --prestock-item {value!r}; expected unique NAME=POSITIVE_COUNT")
        items[name] = int(count)
    return items


def prestock_items(adapter, root, *, items, network, allow_repeat=False):
    """Insert a fixed item list into the network's one storage chest, journaled before and after."""
    root = Path(root)
    if (not isinstance(items, dict) or not items
            or any(not _ITEM_NAME.fullmatch(k) or type(v) is not int or v <= 0 for k, v in items.items())):
        raise TrialError("prestock item list must map item names to positive integer counts")
    blocking = [path for path, receipt in prestock_receipts(root) if receipt.get("status") != "rejected_no_insert"]
    if blocking and not allow_repeat:
        raise TrialError(f"prestock receipt already exists ({blocking[0]}); pass --allow-repeat-prestock to grant again")
    (root / "evidence").mkdir(exist_ok=True)
    path = root / "evidence" / f"prestock-{uuid.uuid4().hex}.json"
    receipt = {"version": "optimizer-block-prestock-v1", "status": "pending", "root": str(root),
               "surface": SURFACE, "force": FORCE, "network": network, "items_requested": dict(items),
               "authority": "test-only grant approved for the requester-fed red block test arm on a disposable world",
               "created_unix": time.time(), "repeat_allowed": bool(blocking)}
    atomic_json(path, receipt)  # durable before the command: a crash leaves a pending receipt that blocks repeats
    try:
        result = lua_json(adapter, _PRESTOCK_LUA, {"network": network, "items": [
            {"name": name, "count": count} for name, count in sorted(items.items())]})
    except LuaError as error:
        no_insert = "expected exactly one storage-chest" in str(error)
        receipt.update(status="rejected_no_insert" if no_insert else "uncertain", error=str(error))
        atomic_json(path, receipt)
        raise TrialError(f"prestock failed ({receipt['status']}); receipt {path}: {error}") from error
    except Exception as error:
        receipt.update(status="uncertain", error=repr(error))
        atomic_json(path, receipt)
        raise TrialError(f"prestock outcome is uncertain and was not retried; receipt {path}") from error
    before, after = result.get("before") or {}, result.get("after") or {}
    inserted = {name: after.get(name, 0) - before.get(name, 0) for name in items}
    complete = inserted == items
    receipt.update(status="completed" if complete else "partial", tick=result.get("tick"),
                   chest={"unit_number": result.get("unit_number"), "position": result.get("position")},
                   contents_before=before, contents_after=after, inserted_reported=result.get("inserted_reported"),
                   items_inserted_readback=inserted)
    atomic_json(path, receipt)
    if not complete:
        raise TrialError(f"prestock inserted {inserted}, requested {dict(items)}; receipt {path}")
    return {**receipt, "path": str(path)}



# --------------------------------------------------------------------------- site and catalogs
# Catalog capture, site screening, binding and reservation live in tools/optimizer_block_proposals.py.


# --------------------------------------------------------------------------- construction and measurement

def find_receipt(root, option_id):
    """Newest transaction receipt for this selection; the adapter does not return its path."""
    matches = []
    for path in sorted((Path(root) / "evidence").glob("transaction-*.json"), key=lambda p: p.stat().st_mtime):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("candidate", {}).get("option_id") == option_id:
                matches.append(path)
        except (OSError, json.JSONDecodeError):
            continue
    return matches[-1] if matches else None


def drive_construction(adapter, plan, receipt, *, chunk, max_build_ticks, start_tick):
    """Step the paused world until every planned entity is built or the build budget is spent."""
    total = len(list(actions(plan)))
    owned = receipt.get("construction")
    tick = (receipt.get("after") or {}).get("tick", start_tick)
    timeline = []
    def done():
        return bool(owned) and owned["pending"] == 0 and owned["missing"] == 0 and owned["built"] == total
    if owned is None:
        owned = adapter.query(inspect=plan)
    timeline.append({"tick": tick, "elapsed_ticks": tick - start_tick, **owned})
    while not done() and tick - start_tick < max_build_ticks:
        step = min(chunk, max_build_ticks - (tick - start_tick))
        adapter.advance(step)
        tick += step
        owned = adapter.query(inspect=plan)
        timeline.append({"tick": tick, "elapsed_ticks": tick - start_tick, **owned})
    return {"complete": done(), "timed_out": not done(), "planned_entities": total, "timeline": timeline,
            "end_tick": tick, "build_ticks": tick - start_tick}


def classify_limit(samples, ratio, min_ratio):
    """Heuristic reading of sampled statuses; instantaneous samples can miss short stalls."""
    assemblers = [r for s in samples for r in s["rows"] if r["name"].startswith("assembling-machine")]
    chests = [r for s in samples for r in s["rows"] if r["name"] == "requester-chest"]
    if not assemblers or any(not r.get("found") for r in assemblers):
        return {"limited_by": "entities_missing", "detail": "assemblers were not found at planned positions"}
    statuses = sorted({r.get("status") for r in assemblers})
    if {"no_power", "low_power"} & set(statuses):
        return {"limited_by": "power", "detail": statuses}
    starved_items = {}
    for row in chests:
        for request in _as_list(row.get("requests")):
            if (row.get("contents") or {}).get(request["name"], 0) == 0:
                starved_items[request["name"]] = starved_items.get(request["name"], 0) + 1
    if ratio is not None and ratio >= min_ratio:
        return {"limited_by": "none", "detail": statuses}
    if "no_ingredients" in statuses or starved_items:
        return {"limited_by": "starvation", "detail": {"statuses": statuses, "empty_requester_samples": starved_items}}
    if "full_output" in statuses:
        return {"limited_by": "output_blocked", "detail": statuses}
    return {"limited_by": "unexplained", "detail": statuses}


def measure_block(adapter, plan, *, warmup_ticks, measure_ticks, min_ratio, samples=6):
    """Warm up, then record pack production over the window with sampled block state."""
    if warmup_ticks:
        adapter.advance(warmup_ticks)
    specs = state_specs(plan)
    before = adapter.query()
    states = [block_state(adapter, specs)]
    base, extra = divmod(measure_ticks, samples)
    for index in range(samples):
        adapter.advance(base + (1 if index < extra else 0))
        states.append(block_state(adapter, specs))
    after = adapter.query()
    outcome = measured_outcome(before, after)
    flow = outcome["production"].get(PACK)
    expected = float(TARGET_RATE)
    measured = flow["per_second"] if flow else None
    ratio = measured / expected if measured is not None else None
    planned = {(s["x"], s["y"]) for s in specs if s["name"] == "assembling-machine-1"}
    foreign = [m for m in _as_list(before.get("machines"))
               if m.get("recipe") == PACK and (m["position"]["x"], m["position"]["y"]) not in planned]
    return {"status": "measured" if measured is not None else "missing",
            "warmup_ticks": warmup_ticks, "window_ticks": outcome["window_ticks"],
            "start_tick": before["tick"], "end_tick": after["tick"],
            "packs_produced": flow["count"] if flow else None, "measured_per_second": measured,
            "expected_per_second": expected, "ratio": ratio,
            "quantization_note": "one pack in the window moves the rate by 60/window_ticks per second",
            "consumption_per_second": {item: outcome["consumption"].get(item, {}).get("per_second")
                                       for item in ("copper-plate", "iron-gear-wheel")},
            "other_pack_assemblers_in_world": len(foreign),
            "samples": states, "limit": classify_limit(states, ratio, min_ratio),
            "pending_ghosts_at_end": outcome["pending_ghosts"]}


# --------------------------------------------------------------------------- report

def evidence_level(report):
    construction, measurement = report.get("construction") or {}, report.get("measurement") or {}
    if report.get("submitted") is not True:
        return "integration only (survey and planning; the world was not mutated by a submission)"
    if measurement.get("status") == "measured":
        return "deployed runtime / observed live result (single disposable-world run, n=1; not a matched cohort)"
    if construction.get("complete"):
        return "deployed runtime (construction observed; throughput not measured)"
    return "deployed runtime (ghosts submitted; construction not completed)"


def build_report(*, args, root, trial, **parts):
    report = {"version": REPORT_VERSION, "trial_kind": trial[0], "commit": git_commit(), "root": str(root),
              "episode_id": args.episode_id, "surface": SURFACE, "force": FORCE, "block": "requester-red-science",
              "target": {"output": PACK, "per_second": float(TARGET_RATE)},
              "prestock_receipt": trial[1], "submitted": False, **parts}
    report["evidence_level"] = evidence_level(report)
    measurement = report.get("measurement") or {}
    report["outcome"] = {
        "status": ("measured" if measurement.get("status") == "measured" else
                   "failed" if report.get("failure") else "incomplete"),
        "meets_expected": (measurement.get("ratio") is not None and measurement["ratio"] >= args.min_ratio)
        if measurement.get("status") == "measured" else None,
        "min_ratio": args.min_ratio,
        "note": "no success is claimed without a measurement"}
    return report


_SUBMITTED = {"submission_received", "submission_recovered", "construction_pending", "observed",
              "configuration_submitting", "configuration_received", "configuration_recovered", "configuration_failed"}


def submitted_in(root):
    """True only when a receipt records a successful ghost submission; an ambiguous one is not claimed."""
    return any(row["status"] in _SUBMITTED for row in receipts_summary(root))


def receipts_summary(root):
    rows = []
    for path in sorted((Path(root) / "evidence").glob("transaction-*.json")):
        try:
            rows.append({"path": str(path), "status": json.loads(path.read_text(encoding="utf-8")).get("status")})
        except (OSError, json.JSONDecodeError):
            rows.append({"path": str(path), "status": "unreadable"})
    return rows


# --------------------------------------------------------------------------- orchestration

def run_trial(args, adapter=None):
    """Run every stage in order; always leaves a report, returns ``(exit_code, report)``."""
    root, _manifest = verify_world(args.root)
    if not _EPISODE.fullmatch(args.episode_id):
        raise TrialError("episode id must be 1-58 characters of [A-Za-z0-9_-] (attempt suffixes are appended)")
    own = adapter is None
    adapter = adapter or SavedWorldAdapter(root)
    report_path = Path(args.report) if args.report else root / "evidence" / f"block-trial-{uuid.uuid4().hex}.json"
    parts = {"stage": "start", "report_path": str(report_path)}
    trial = trial_kind(root)
    def flush(**more):
        parts.update(more)
        atomic_json(report_path, build_report(args=args, root=root, trial=trial, **parts))
        return parts
    try:
        facts = adapter.query()
        port = choose_port(facts)
        network_id = port["network"]
        flush(stage="survey", tick_start=facts["tick"], roboport={"position": port["position"], "network": network_id,
              "logistic_radius": port["logistic_radius"], "construction_radius": port["construction_radius"]})
        if args.prestock:
            items = parse_items(args.prestock_item) or dict(DEFAULT_PRESTOCK)
            receipt = prestock_items(adapter, root, items=items, network=network_id,
                                     allow_repeat=args.allow_repeat_prestock)
            trial = ("block_test_prestocked", receipt["path"])
            flush(stage="prestock", prestock={"this_run": True, "items_requested": items,
                  "items_inserted_readback": receipt["items_inserted_readback"], "tick": receipt["tick"],
                  "chest": receipt["chest"]})
            facts = adapter.query()
        else:
            flush(prestock={"this_run": False, "items_requested": {}, "note": "no items were granted by this run"})
        catalogs = capture_catalogs(adapter, game_version=args.game_version)
        if catalogs["tick"] != facts["tick"]:
            facts = adapter.query()
        recipe_catalog, demand = plan_demand(catalogs)
        scope = ConstructionScope(str(root), file_hash(root / "manifest.json"), SURFACE, FORCE)
        ctx = Context(root=root, scope=scope, catalogs=catalogs, placement=placement_capture(catalogs),
                      recipe_catalog=recipe_catalog, demand=demand, pole_wire=pole_reach(catalogs["runtime_bytes"]),
                      template=block_template(), port=port, network_anchor=dict(port["position"]),
                      network_id=network_id, base_episode=args.episode_id)
        flush(stage="catalogs", catalogs={"source": catalogs["source"], "tick": catalogs["tick"],
              "runtime_sha256": catalogs["runtime_sha256"], "recipe_sha256": catalogs["recipe_provenance"]["catalog_sha256"],
              "game_version": catalogs["game_version"], "game_version_basis": catalogs["game_version_basis"]})
        survey_tick, entities = survey_entities(adapter, port, tick_expected=facts["tick"])
        blocked = known_tiles(entities)
        origins = origin_candidates(ctx.template, port, args.margin)
        poles = _as_list(facts.get("poles"))
        flush(stage="site", site={"survey_tick": survey_tick, "known_entities": len(entities),
              "origin_count": len(origins), "margin": args.margin})
        origin, outcome, rejected, stop = search_sites(
            origins, lambda o: screen_origin(o, ctx.template, blocked, poles, pole_wire=ctx.pole_wire),
            lambda o, n: attempt_site(adapter, ctx, facts, o, n), max_attempts=args.max_site_attempts)
        parts["site"].update(rejected_origins=rejected, stop_reason=stop)
        if origin is None:
            raise TrialError(f"no origin passed the verifier ({stop}); see site.rejected_origins")
        bound, ledger, chosen = outcome["bound"], outcome["ledger"], outcome["candidate"]
        config_path = root / "evidence" / f"optimizer-candidate-config-{outcome['episode']}.json"
        try:
            atomic_json(config_path, optimizer_candidate_config(
                bound, episode_id=outcome["episode"], network_anchor=ctx.network_anchor,
                fluid_catalog_bytes=catalogs["runtime_bytes"]))
            config_note = str(config_path)
        except ValueError as error:
            config_note = f"not written: {error}"
        flush(stage="offered", site={**parts["site"], "chosen_origin": list(origin)},
              power={"anchor": outcome["anchor"], **outcome["power"]},
              bill=dict(bound.plan.required_materials), check_evidence=outcome["check_evidence"],
              ledger={"episode_id": outcome["episode"], "path": str(ledger.path), "revision": ledger.revision},
              binding={"project_id": outcome["project_id"], "transaction_id": outcome["transaction_id"],
                       "artifact_sha256": bound.artifact_sha256, "plan_sha256": bound.binding_sha256,
                       "candidate_config": config_note})
        plan = chosen["plan"]
        try:
            flush(stage="submitting")
            receipt = execute_optimizer_candidate(
                adapter, bound, ledger, episode_id=outcome["episode"], network_anchor=ctx.network_anchor,
                candidate=chosen, selected=chosen["option_id"], measure_ticks=args.build_chunk_ticks,
                fluid_capture={"sha256": catalogs["runtime_sha256"], "json": catalogs["runtime_bytes"].decode("utf-8")})
        except Exception as error:
            flush(failure={"stage": "execute", "error": f"{type(error).__name__}: {_short(error)}",
                           "uncertain": True, "receipts": receipts_summary(root),
                           "action": "do not resubmit; inspect evidence/transaction-*.json and reconcile"},
                  submitted=submitted_in(root))
            return 2, parts
        receipt_path = find_receipt(root, chosen["option_id"])
        submission_tick = receipt["before"]["tick"]
        flush(stage="constructing", submitted=bool((receipt.get("submission") or {}).get("ok")),
              transaction_receipt=str(receipt_path), ticks={"decision": submission_tick})
        build = drive_construction(adapter, plan, receipt, chunk=args.build_chunk_ticks,
                                   max_build_ticks=args.max_build_ticks, start_tick=submission_tick)
        construction = {k: v for k, v in build.items()}
        if build["complete"] and receipt.get("status") != "observed":
            adapter.reconcile()  # applies the deferred requester configuration to the bot-built chests
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        construction["receipt_status"] = receipt.get("status")
        construction["configuration"] = {"report": receipt.get("configuration"),
                                         "configured_tick": receipt.get("configured_tick")}
        flush(stage="constructed", construction=construction)
        if not build["complete"]:
            raise TrialError(f"construction incomplete after {build['build_ticks']} ticks "
                             f"(budget {args.max_build_ticks}); no measurement taken")
        specs = state_specs(plan)
        construction["requests"] = confirm_requests(plan, block_state(adapter, specs))
        configured = (receipt.get("configuration") or {}).get("ok") is True
        if receipt.get("status") != "observed" or not configured or construction["requests"]["mismatch"]:
            flush(construction=construction)
            raise TrialError("requester configuration was not confirmed (receipt status "
                             f"{receipt.get('status')!r}, configuration ok={configured}, read-back mismatch="
                             f"{construction['requests']['mismatch']}); no measurement taken")
        flush(stage="measuring", construction=construction)
        measurement = measure_block(adapter, plan, warmup_ticks=args.warmup_ticks, measure_ticks=args.measure_ticks,
                                    min_ratio=args.min_ratio)
        flush(stage="done", measurement=measurement, tick_end=measurement["end_tick"])
        if measurement["status"] != "measured":
            return 2, parts
        return (0 if measurement["ratio"] >= args.min_ratio else 1), parts
    except Exception as error:
        flush(failure={"stage": parts.get("stage"), "error": f"{type(error).__name__}: {_short(error)}",
                       "receipts": receipts_summary(root)})
        return 2, parts
    finally:
        if own:
            adapter.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Prepared disposable saved-world root")
    parser.add_argument("--episode-id", default=None, help="Ledger episode base id (attempts append -s<N>)")
    parser.add_argument("--prestock", action="store_true", help="Test-only: grant the fixed item list to the storage chest")
    parser.add_argument("--prestock-item", action="append", default=[], metavar="NAME=COUNT",
                        help="Replace the default prestock list (repeatable)")
    parser.add_argument("--allow-repeat-prestock", action="store_true")
    parser.add_argument("--measure-ticks", type=int, default=3600)
    parser.add_argument("--warmup-ticks", type=int, default=1800)
    parser.add_argument("--max-build-ticks", type=int, default=7200)
    parser.add_argument("--build-chunk-ticks", type=int, default=600)
    parser.add_argument("--margin", type=int, default=2, help="Tiles kept inside the logistic radius")
    parser.add_argument("--max-site-attempts", type=int, default=8)
    parser.add_argument("--min-ratio", type=float, default=0.9)
    parser.add_argument("--game-version", default=None, help="Default: the base mod version of the paused world")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)
    args.episode_id = args.episode_id or "blocktrial-" + uuid.uuid4().hex[:8]
    if not 600 <= args.measure_ticks <= 108000 or not 0 <= args.warmup_ticks <= 18000:
        parser.error("--measure-ticks must be 600..108000 and --warmup-ticks 0..18000")
    if not 60 <= args.build_chunk_ticks <= 18000 or args.max_build_ticks < args.build_chunk_ticks:
        parser.error("--build-chunk-ticks must be 60..18000 and no larger than --max-build-ticks")
    if args.margin < 0 or args.max_site_attempts < 1 or not 0 < args.min_ratio <= 1:
        parser.error("--margin >= 0, --max-site-attempts >= 1 and 0 < --min-ratio <= 1 are required")
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        code, parts = run_trial(args)
    except (TrialError, SavedWorldError, OSError, ValueError) as error:
        print(f"block trial refused: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"report": parts.get("report_path"), "stage": parts.get("stage"),
                      "measurement": (parts.get("measurement") or {}).get("status"),
                      "failure": parts.get("failure")}, indent=2, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
