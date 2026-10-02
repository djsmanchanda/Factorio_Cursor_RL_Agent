# Path: training/laya_saved_world.py | Purpose: Isolated saved-world observations, legal alternatives, bot construction and measured receipts.

from __future__ import annotations

import hashlib
import json
import math
import random
import time
import uuid
import shutil
from pathlib import Path

from jsonschema import Draft7Validator

from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.laya_operations import sha256_hash
from orchestrator.laya_state import validate_state
from planners.plan_validation import actions, validate_build_plan
from planners.sandbox_infrastructure import build_layout_authorization
from planners.laya_quad_cell import generate_quad_slot, quad_centers_from_plan, quad_reservation_reason
from planners.quad_mall_layout import DENSE_MALL_MODULE_OFFSETS

PROFILE = "saved-world-v1"
ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "data" / "laya-saved-world"
BOUNDS = {"x_min": -128, "y_min": -128, "x_max_exclusive": 128, "y_max_exclusive": 128}
SCOPE = {"surface": "nauvis", "force": "player", "bounds": BOUNDS}
FACTS_VALIDATOR = Draft7Validator(json.loads((ROOT / "schemas/laya_saved_world.schema.json").read_text()))


class SavedWorldError(RuntimeError):
    pass


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def owned_root(path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(TEST_ROOT.resolve()) or path == TEST_ROOT.resolve():
        raise SavedWorldError("saved-world roots must be dedicated children of data/laya-saved-world")
    return path


def verify_instance(root: Path) -> dict:
    """Pin the OS process, save, ports, mods and script-output before any RCON."""
    root = owned_root(root)
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("runtime") != "disposable_saved_world" or manifest.get("production_authority") is not False:
        raise SavedWorldError("not an explicitly disposable saved-world manifest")
    if file_hash(root / "baseline.zip") != manifest["source_sha256"]:
        raise SavedWorldError("immutable baseline hash changed")
    server = root / "server"
    pid = int((server / "factorio.pid").read_text())
    argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
    expected = {"--config": str(server / "config.ini"), "--start-server": str(server / "saves/mod_playground.zip"),
                "--mod-directory": str(server / "mods"), "--rcon-bind": f"127.0.0.1:{manifest['rcon_port']}",
                "--bind": f"127.0.0.1:{manifest['game_port']}"}
    for flag, value in expected.items():
        if flag not in argv or argv[argv.index(flag) + 1] != value:
            raise SavedWorldError(f"test instance mismatch: {flag}")
    for rel, digest in manifest["mod_hashes"].items():
        if file_hash(server / "mods" / rel) != digest:
            raise SavedWorldError("test instance mod hash changed")
    return manifest


def ghost(entity, x, y, direction="north"):
    return {"action_type": "place_ghost", "entity": entity,
            "position": {"x": x, "y": y}, "direction": direction}


def power_connection(facts, target):
    """Realize a single substation endpoint and bounded straight wire relay.

    It chooses no mission/site. A supplied endpoint must connect to an observed
    generated grid; the live collision/funding shield may reject this geometry.
    """
    poles = [p for p in facts["poles"] if p["generated"]]
    if not poles:
        return None
    x, y = target
    anchor = min(poles, key=lambda p: math.dist((p["position"]["x"], p["position"]["y"]), target))
    ax, ay = anchor["position"]["x"], anchor["position"]["y"]
    distance = math.hypot(x - ax, y - ay)
    reach = min(anchor["wire"], 18)
    steps = max(1, math.ceil(distance / min(reach - 1, 16)))
    return [ghost("substation", round(ax + (x - ax) * k / steps), round(ay + (y - ay) * k / steps))
            for k in range(1, steps + 1)]


def compile_candidates(facts: dict, seed: int = 0) -> list[dict]:
    """Compile parameterized alternatives without a build-order preference."""
    candidates = []

    def add(family, summary, placements, network, product=None):
        plan = {"surface": "nauvis", "force": "player", "atomic": True,
                "phases": [{"name": f"saved-world-{family}", "actions": placements}]}
        try:
            validate_build_plan(plan)
        except ValueError:
            return
        anchor = min((p["position"] for p in facts["ports"] if p["network"] == network), key=lambda p: (p["x"], p["y"]))
        digest = sha256_hash({"plan": plan, "network_anchor": anchor})
        candidates.append({"option_id": f"{family}.{digest[7:19]}", "family": family,
                           "summary": summary, "plan": plan, "network": network, "network_anchor": anchor, "product": product})

    for port in facts["ports"]:
        if port["energy"] <= 0:
            continue
        px, py = port["position"]["x"], port["position"]["y"]
        # Site and direction are exposed as alternatives, never selected here.
        for dx, dy in [(32, 0), (-32, 0), (0, 32), (0, -32)]:
            x, y = round(px + dx), round(py + dy)
            power = power_connection(facts, (x + 4, y))
            if power:
                add("expand", f"Extend bot coverage to ({x},{y})", power + [ghost("roboport", x, y)], port["network"])
        for site in facts["resources"]:
            x, y = site["position"]["x"], site["position"]["y"]
            if max(abs(x - px), abs(y - py)) > port["construction_radius"] - 6:
                continue
            power = power_connection(facts, (round(x + 4), round(y)))
            if power:
                add("mine", f"Mine {site['item']} at ({x},{y}) into a provider", power + [
                    ghost("electric-mining-drill", x, y), ghost("passive-provider-chest", x, y - 2)],
                    port["network"], site["item"])
        for dx, dy in [(10, 10), (10, -10), (-10, 10), (-10, -10)]:
            x, y = math.floor(px + dx) + .5, math.floor(py + dy) + .5
            power = power_connection(facts, (round(x), round(y + 4)))
            if not power:
                continue
            for recipe, inputs, machine, product in [
                (None, [{"name": "iron-ore", "count": 20}], "electric-furnace", "iron-plate"),
                (None, [{"name": "copper-ore", "count": 20}], "electric-furnace", "copper-plate"),
                (None, [{"name": "stone", "count": 40}], "electric-furnace", "stone-brick"),
                (None, [{"name": "iron-plate", "count": 100}], "electric-furnace", "steel-plate"),
            ]:
                placement = ghost(machine, x, y)
                if recipe:
                    placement["recipe"] = recipe
                input_chest = ghost("requester-chest", x - 3, y)
                input_chest["logistic_requests"] = inputs
                add("smelt", f"Make {product} ({x},{y})", power + [
                    input_chest, ghost("inserter", x - 2, y, "west"), placement,
                    ghost("inserter", x + 2, y, "west"), ghost("passive-provider-chest", x + 3, y)],
                    port["network"], product)
            # One requested slot, with site/side/recipe chosen by Laya. The
            # shared geometry supports horizontal bootstrap without long arms.
            for mx, my in DENSE_MALL_MODULE_OFFSETS:
                center = (x + mx, y + my)
                local_power = power_connection(facts, (center[0] + 3.5, center[1] + 2.5))
                if not local_power:
                    continue
                for product, spec in facts.get("recipes", {}).items():
                    if product in {"iron-plate", "copper-plate", "stone-brick", "steel-plate"}:
                        continue
                    for side in ("west", "east", "north", "south"):
                        placements = generate_quad_slot(product, spec, center, side)
                        target = next(a["position"] for a in placements if a["entity"] == "assembling-machine-1")
                        if any(m["position"] == target and m.get("recipe") == product for m in facts["machines"]):
                            continue
                        add("assemble", f"{product} {side} ({center[0]},{center[1]})", local_power + placements,
                            port["network"], product)
    # Seeded uniform order preserves exploration across families and sites.
    candidates = list({c["option_id"]: c for c in candidates}.values())
    random.Random(seed).shuffle(candidates)
    return candidates


def bounded_menu(legal, seed, limit=10):
    """Preserve available operation families under a small request budget.

    Equal representation is exploration coverage, not a priority ranking;
    seeded rotation chooses which legal sites enter this decision window.
    """
    rng = random.Random(seed)
    groups = {}
    for c in legal:
        groups.setdefault((c["family"], c.get("product") or ""), []).append(c)
    families = sorted(groups)
    rng.shuffle(families)
    for group in groups.values():
        rng.shuffle(group)
    result = []
    while groups and len(result) < limit:
        for family in families:
            if groups.get(family):
                result.append(groups[family].pop())
                if len(result) == limit:
                    break
        groups = {k: v for k, v in groups.items() if v}
    return result


def state_option(candidate):
    placements = list(actions(candidate.get("plan") or {}))
    return {"option_id": candidate["option_id"], "operation_family": candidate["family"],
            "tool_id": f"saved_world.{candidate['family']}", "signature": sha256_hash(candidate),
            "plan_hash": sha256_hash(candidate["plan"]) if candidate.get("plan") else None,
            "summary": candidate["summary"], "features": {"entities": len(placements),
                "new_items": sum(candidate.get("bill", {}).values()) if "bill" in candidate else None,
                "wait_ticks": candidate.get("wait_ticks", 0)}, "scope": SCOPE,
            "risk_tier": "ghost_submit" if placements else "read_only"}


def decision_state(facts, candidates, run_id, model_ref, objective, history=(), measurement=None):
    options = [state_option(c) for c in candidates]
    available = sum(sum(max(0, count - n["reserved"].get(item, 0)) for item, count in n["items"].items())
                    for n in facts["networks"].values())
    state = {
        "version": "1.0.0", "run": {"run_id": run_id, "episode_id": run_id, "tick": facts["tick"],
            "elapsed_ticks": 0, "bootstrap_profile": "exact-save-copy", "surface": "nauvis", "force": "player", "runtime": "disposable_training"},
        "objective": {"family": "saved_world", "description": objective, "target": None, "unit": "items_per_second"},
        "goal_stack": [{"goal_id": "saved-world.goal", "kind": "produce", "objective": objective,
            "status": "active", "gap": None, "age_ticks": 0, "retry_count": 0, "last_failure_kind": "none"}],
        "acceptance_gap": {"metric": "production", "current": None, "target": None, "remaining": None,
            "unit": "items_per_second", "sustained_ticks": 0, "required_sustained_ticks": 0},
        "current_transaction": None,
        "state_digest": {
            "power": {"generation_kw": None, "firm_generation_kw": None, "accumulator_mj": None, "unpowered_consumers": None, "poles": len(facts["poles"])},
            "logistics": {"robots": sum(n["construction_robots"] for n in facts["networks"].values()), "providers_available": None,
                "pending_ghosts": facts["pending_ghosts"], "ghost_blockages_top3": []},
            "stock": {"total": None, "accessible": available, "allocated": None, "top_shortages": []},
            "production": {"rate_now": None, "rate_target": None, "sustained_ticks": None, "bottleneck_hint": None},
            "resource": {"remaining": None, "patch_bounds_hint": BOUNDS},
            "space": {"free_area_tiles": None, "reserved_area_tiles": None, "occupied_footprint_tiles": None, "candidate_site_count": len(options)}},
        "recent_history": list(history)[-2:], "active_failures": [],
        "guidelines": [{"rule_id": "saved-world.funding", "kind": "hard", "statement": "Build funded ghosts on this disposable copy; player inventory and WIP are not provider stock."}],
        "options": options,
        "provenance": {"renderer_version": PROFILE, "state_snapshot_hash": sha256_hash(facts),
            "guideline_pack": {"id": "saved-world", "version": "1.0.0", "hash": sha256_hash({"funded": True})},
            "tool_registry": {"id": "saved-world", "version": "1.0.0", "hash": sha256_hash({"families": ["mine", "smelt", "assemble", "expand", "wait"]})},
            "model": model_ref},
        "budget": {"materials_remaining": available, "construction_budget_remaining": None, "action_count_remaining": None, "elapsed_ticks_remaining": None},
        "observation_digest": {"saved_world": {"version": PROFILE, "provider_stock": {k: n["items"] for k, n in facts["networks"].items()},
            "reserved_stock": {k: n["reserved"] for k, n in facts["networks"].items()},
            "powered_ports": [{"position": p["position"], "network": p["network"], "construction_radius": p["construction_radius"],
                               "logistic_radius": p["logistic_radius"]} for p in facts["ports"] if p["energy"] > 0],
            "machine_status_counts": {status: sum(m["status"] == status for m in facts["machines"])
                                      for status in sorted({m["status"] for m in facts["machines"]})},
            "quad_reserved_centers": facts.get("quad_centers", []),
            "flow_items": sorted(facts["production"]),
            "production_counts": {n: v for n, v in facts["production"].items() if v},
            "consumption_counts": {n: v for n, v in facts["consumption"].items() if v}}},
    }
    if measurement is not None:
        state["observation_digest"]["saved_world"]["measured_flow"] = {
            "window_ticks": measurement["window_ticks"],
            "production_per_second": {n: v["per_second"] for n, v in measurement["production"].items() if v["per_second"]},
            "consumption_per_second": {n: v["per_second"] for n, v in measurement["consumption"].items() if v["per_second"]},
        }
    validate_state(state)
    return state


def measured_outcome(before, after):
    ticks = after["tick"] - before["tick"]
    if ticks <= 0:
        raise SavedWorldError("measurement needs a positive engine-clock window")
    def flow(kind):
        return {name: {"count": after[kind][name] - count, "per_second": (after[kind][name] - count) * 60 / ticks}
                for name, count in before[kind].items() if after[kind][name] >= count}
    if any(after[kind][name] < count for kind in ("production", "consumption") for name, count in before[kind].items()):
        raise SavedWorldError("engine counters reset during the measurement window")
    return {"window_ticks": ticks, "production": flow("production"), "consumption": flow("consumption"),
            "pending_ghosts": after["pending_ghosts"], "machines": after["machines"],
            "note": "Engine production is measured; capacity and sustained delivery are not inferred."}


CONFIGURATION_FIELDS = frozenset({"recipe", "logistic_requests", "logistic_sections", "clear_inventory_limit"})


def submission_plan(plan):
    """Ghosts lack inventories; defer every setting until construction is observed."""
    return {**plan, "phases": [{**phase, "actions": [
        {k: v for k, v in action.items() if k not in CONFIGURATION_FIELDS}
        for action in phase["actions"]]} for phase in plan["phases"]]}


def configuration_actions(plan):
    return [{**a, "action_type": "configure_entity"} for a in actions(plan)
            if CONFIGURATION_FIELDS.intersection(a)]


class SavedWorldAdapter:
    def __init__(self, root: Path):
        self.root = owned_root(root)
        self.manifest = verify_instance(self.root)
        self.lua_source = Path(__file__).with_suffix(".lua").read_text()
        self.code_hashes = {str(p.relative_to(ROOT)): file_hash(p) for p in (
            Path(__file__), Path(__file__).with_suffix(".lua"), ROOT / "schemas/laya_saved_world.schema.json",
            ROOT / "training/laya_input_profile.py", ROOT / "tools/run_laya_saved_world.py",
            ROOT / "planners/laya_quad_cell.py", ROOT / "planners/quad_mall_layout.py",
            ROOT / "planners/mall_layout.py")}
        server = self.root / "server"
        self.bridge = GameBridge(server / "script-output", host="127.0.0.1", port=self.manifest["rcon_port"],
                                 password=(server / "rcon-password").read_text().strip(), command_timeout=30)

    def close(self):
        self.bridge.close()

    def quad_centers(self):
        centers = {(p["x"], p["y"]) for p in self.manifest.get("quad_centers", [])}
        # Reserve as soon as a selected horizontal slot is durably submitted,
        # including while its bots/configuration are still pending.
        for path in (self.root / "evidence").glob("transaction-*.json"):
            receipt = load_json(path)
            if (receipt.get("submission") or {}).get("ok"):
                centers.update((p["x"], p["y"]) for p in quad_centers_from_plan(receipt["candidate"]["plan"]))
        return [{"x": x, "y": y} for x, y in sorted(centers)]

    def checkpoint(self):
        """Save a paused prefix for independent, immutable branch replays."""
        before = self.query()
        name = "laya-prefix-" + uuid.uuid4().hex
        source = self.root / "server/saves" / (name + ".zip")
        response = self.bridge.command(f"/server-save {name}")
        if "error" in response.lower():
            raise SavedWorldError("prefix save failed")
        deadline = time.monotonic() + 30
        while not source.is_file() and time.monotonic() < deadline:
            time.sleep(.05)
        if not source.is_file():
            raise SavedWorldError("prefix save did not appear")
        # The engine's save operation completes before its next console command.
        after = self.query()
        if before != after:
            raise SavedWorldError("paused world changed while capturing a prefix")
        folder = self.root / "prefixes"
        folder.mkdir(exist_ok=True)
        target = folder / source.name
        shutil.copy2(source, target)
        return {"save": str(target), "sha256": file_hash(target), "facts": before,
                "ancestor_source_sha256": self.manifest.get("ancestor_source_sha256", self.manifest["source_sha256"]), "adapter_hashes": self.code_hashes,
                "mod_hashes": self.manifest["mod_hashes"]}

    def query(self, candidates=None, inspect=None):
        verify_instance(self.root)
        centers = self.quad_centers()
        name = f"laya_saved_world/{uuid.uuid4().hex}.json"
        request = json.dumps({"bounds": BOUNDS, "candidates": candidates, "inspect": inspect}, separators=(",", ":"))
        source = self.lua_source
        # JSON is a Lua string via a long-bracket delimiter absent from JSON.
        delimiter = "="
        while f"]{delimiter}]" in request:
            delimiter += "="
        body = f"local request=helpers.json_to_table([{delimiter}[{request}]{delimiter}]); " + source
        # Strip comments before joining lines; Lua comments otherwise swallow
        # the rest of the RCON command, including the protected call's closure.
        body = " ".join(line.split("--", 1)[0] for line in body.splitlines())
        cmd = f'/sc local ok,value=pcall(function() {body} end); helpers.write_file("{name}",helpers.table_to_json({{ok=ok,value=ok and value or nil,error=not ok and tostring(value) or nil}}),false); rcon.print("saved-world-report")'
        self.bridge.command(cmd)
        result = load_json(self.bridge.script_output / name)
        if not result["ok"]:
            raise SavedWorldError(result["error"])
        value = result["value"]
        if candidates is not None:
            for candidate in candidates:
                protected = centers + quad_centers_from_plan(candidate["plan"])
                reason = quad_reservation_reason(candidate["plan"], protected)
                if reason:
                    value["validations"][candidate["option_id"]] = {"reason": reason}
        if candidates is None and inspect is None:
            value["quad_centers"] = centers
            for key in ("ports", "poles", "machines", "resources"):
                if value.get(key) == {}:
                    value[key] = []
            errors = list(FACTS_VALIDATOR.iter_errors(value))
            if errors:
                raise SavedWorldError(f"saved-world facts violate their contract: {errors[0].message}")
        return value

    def options(self, facts, seed=0):
        proposed = compile_candidates(facts, seed)
        # Keep all pruned options in evidence. Pruning is legality, not ranking.
        results = self.query(proposed)["validations"] if proposed else {}
        legal = [{**c, "bill": results[c["option_id"]].get("bill") or {}} for c in proposed if not results[c["option_id"]].get("reason")]
        # Leave head-room for instructions and typed option markers in Laya's
        # 256-token decision head. The complete sequence still has its own gate.
        legal = bounded_menu(legal, seed, limit=6)
        legal += [{"option_id": f"wait.{ticks}", "family": "wait", "summary": f"Wait {ticks / 60:g} game seconds", "wait_ticks": ticks}
                  for ticks in (300, 1800)]
        return legal, {"proposed": proposed, "validations": results}

    def advance(self, ticks):
        if not isinstance(ticks, int) or not 1 <= ticks <= 18000:
            raise SavedWorldError("step must be 1..18000 ticks")
        verify_instance(self.root)
        start = self.query()["tick"]
        self.bridge.command(f"/sc game.tick_paused=true; game.speed=16; game.ticks_to_run={ticks}")
        deadline = time.monotonic() + max(30, ticks / 60)
        while time.monotonic() < deadline:
            response = self.bridge.command('/sc rcon.print(helpers.table_to_json({tick=game.tick,paused=game.tick_paused,remaining=game.ticks_to_run}))')
            state = json.loads(response)
            if state["remaining"] == 0 and state["paused"]:
                if state["tick"] != start + ticks:
                    raise SavedWorldError("stepped world advanced by an unexpected tick count")
                return
            time.sleep(.05)
        raise SavedWorldError("engine clock failed to reach its bounded endpoint")

    def execute(self, selected, presented, measure_ticks=1800):
        matches = [c for c in presented if c["option_id"] == selected]
        if len(matches) != 1:
            raise SavedWorldError("selection must resolve to one presented candidate")
        candidate = matches[0]
        if candidate.get("plan"):
            plan = candidate["plan"]
            if plan.get("surface") != "nauvis" or plan.get("force") != "player" or plan.get("atomic") is not True:
                raise SavedWorldError("saved-world plans require explicit atomic Nauvis/player scope")
            allowed = {"action_type", "entity", "position", "direction", "recipe", "logistic_requests", "logistic_sections", "clear_inventory_limit"}
            if any(a.get("action_type") != "place_ghost" or set(a) - allowed for a in actions(plan)):
                raise SavedWorldError("saved-world submissions allow funded ghosts only")
        elif candidate.get("family") != "wait" or candidate.get("wait_ticks") not in (300, 1800):
            raise SavedWorldError("unsupported saved-world action")
        before = self.query()
        receipt = {"candidate": candidate, "before": before, "status": "pending", "adapter_hashes": getattr(self, "code_hashes", {})}
        receipt_path = self.root / "evidence" / f"transaction-{uuid.uuid4().hex}.json"
        receipt_path.parent.mkdir(exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        if candidate.get("plan"):
            validate_build_plan(candidate["plan"])
            fresh = self.query([candidate])["validations"][selected]
            if fresh.get("reason"):
                receipt.update(status="rejected", reason=fresh["reason"])
                receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
                raise SavedWorldError(f"selected plan became illegal: {fresh['reason']}")
            ghosts = submission_plan(candidate["plan"])
            report = load_json(self.bridge.build_layout(build_layout_authorization([ghosts]), ghosts, timeout=30))
            receipt["submission"] = report
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
            if not report["ok"]:
                raise SavedWorldError(f"ghost submission failed: {report.get('error')}")
        self.advance(candidate.get("wait_ticks", measure_ticks))
        after = self.query()
        # Configuration is applied only to observed bot-built entities. No
        # direct placement, item transfer or production grant is permitted.
        owned = self.query(inspect=candidate["plan"]) if candidate.get("plan") else None
        if owned and owned["pending"] == 0 and owned["missing"] == 0:
            configs = configuration_actions(candidate["plan"])
            if configs:
                plan = {"surface": "nauvis", "force": "player", "atomic": True,
                        "phases": [{"name": "configure-observed-build", "actions": configs}]}
                receipt["configuration"] = load_json(self.bridge.build_layout(build_layout_authorization([plan]), plan, timeout=30))
                if not receipt["configuration"]["ok"]:
                    raise SavedWorldError("configuration failed")
                self.advance(measure_ticks)
                after = self.query()
        receipt.update(status="observed" if not owned or (owned["pending"] == 0 and owned["missing"] == 0) else "construction_pending",
                       construction=owned, after=after, measurement=measured_outcome(before, after))
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        return receipt

    def reconcile(self):
        """Finish only the configuration already selected and durably submitted.

        No new layout or recovery decision is chosen on the director's behalf.
        Interrupted submissions remain ambiguous and require explicit review.
        """
        for path in sorted((self.root / "evidence").glob("transaction-*.json")):
            receipt = load_json(path)
            if receipt["status"] == "pending":
                raise SavedWorldError(f"interrupted transaction needs review: {path.name}")
            if receipt["status"] != "construction_pending":
                continue
            candidate = receipt["candidate"]
            owned = self.query(inspect=candidate["plan"])
            receipt["construction"] = owned
            if owned["pending"] == 0 and owned["missing"] == 0:
                configs = configuration_actions(candidate["plan"])
                if configs:
                    plan = {"surface": "nauvis", "force": "player", "atomic": True,
                            "phases": [{"name": "finish-selected-configuration", "actions": configs}]}
                    receipt["configuration"] = load_json(self.bridge.build_layout(build_layout_authorization([plan]), plan, timeout=30))
                    if not receipt["configuration"]["ok"]:
                        raise SavedWorldError("pending configuration failed")
                receipt["status"] = "observed"
                receipt["configured_tick"] = self.query()["tick"]
            path.write_text(json.dumps(receipt, indent=2) + "\n")
