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

from core.disposable_rcon_identity import (DisposableIdentityError, server_process_identity,
                                            verify_connected_server_socket)
from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.guarded_configuration import guard_configuration, require_configuration_baseline
from orchestrator.laya_operations import sha256_hash
from orchestrator.laya_state import validate_state
from planners.plan_validation import actions, validate_build_plan
from planners.sandbox_infrastructure import build_layout_authorization
from planners.laya_quad_cell import generate_quad_slot, quad_centers_from_plan, quad_reservation_reason
from planners.quad_mall_layout import DENSE_MALL_MODULE_OFFSETS
from training.laya_layout_stream import atomic_json, recover as recover_layout_stream, submit as submit_layout_stream

PROFILE = "saved-world-v1"
ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "data" / "laya-saved-world"
BOUNDS = {"x_min": -128, "y_min": -128, "x_max_exclusive": 128, "y_max_exclusive": 128}
SCOPE = {"surface": "nauvis", "force": "player", "bounds": BOUNDS}
# Oil and water lie beyond the build bounds; this read-only fact searches generated terrain this far from the base.
OIL_SEARCH_RADIUS = 512
FACTS_VALIDATOR = Draft7Validator(json.loads((ROOT / "schemas/laya_saved_world.schema.json").read_text()))


def _query_request(candidates=None, inspect=None, extra_recipes=()):
    """One read-only request envelope. Demanded recipes only widen observation."""
    return {"bounds": dict(BOUNDS), "candidates": candidates, "inspect": inspect,
            "extra_recipes": list(extra_recipes), "oil_search_radius": OIL_SEARCH_RADIUS}


class SavedWorldError(RuntimeError):
    pass


OPTIMIZER_REQUIRED_CHECKS = ("fluid_purity", "safe_infrastructure_attachment")
# Only these names can ever be marked verified; anything else stays unimplemented.
IMPLEMENTED_CHECKS = frozenset(OPTIMIZER_REQUIRED_CHECKS)


def _required_checks_structure_reason(candidate, *, external=False):
    required = candidate.get("required_checks", [])
    if (not isinstance(required, list) or any(not isinstance(c, str) or not c for c in required)
            or len(set(required)) != len(required)):
        return "invalid_required_checks"
    optimizer_marked = (candidate.get("family") == "optimizer-block"
                        or str(candidate.get("option_id", "")).startswith("optimizer.")
                        or "optimizer_binding" in candidate or "optimizer_artifact_sha256" in candidate
                        or "funding_context" in candidate)
    if (external or optimizer_marked) and required != list(OPTIMIZER_REQUIRED_CHECKS):
        return "optimizer_required_checks_missing"
    return None


def required_check_reason(candidate, *, external=False, verified=()):
    """Fail closed unless every requested check was verified against the world.

    A response or caller assertion alone cannot establish a check.  ``verified``
    is produced only by ``evaluate_required_checks`` from a verifier that
    independently inspected the current world at option and execution time;
    unknown check names are never verifiable.
    """
    reason = _required_checks_structure_reason(candidate, external=external)
    if reason:
        return reason
    for check in candidate.get("required_checks", []):
        if check not in verified or check not in IMPLEMENTED_CHECKS:
            return "required_check_unimplemented:" + check
    return None


def evaluate_required_checks(candidate, verifier, adapter, entry, response, *, external=False):
    """Run a world verifier; return ``(reason, evidence)``.

    The verifier maps each check to ``{"reason": None | str, "facts": dict}``.
    ``evidence`` (check -> facts) lets a later evaluation detect world change.
    """
    reason = _required_checks_structure_reason(candidate, external=external)
    if reason or not candidate.get("required_checks"):
        return reason, None
    try:
        outcome = verifier(adapter, candidate, entry if isinstance(entry, dict) else {}, response)
    except Exception:  # any verifier failure is a failed check, never a pass
        return "required_check_verifier_error", None
    if not isinstance(outcome, dict):
        return "required_check_verifier_error", None
    verified, evidence = set(), {}
    for check in candidate["required_checks"]:
        result = outcome.get(check)
        if check not in IMPLEMENTED_CHECKS or not isinstance(result, dict):
            return "required_check_unimplemented:" + check, None
        if result.get("reason") is not None:
            return (result["reason"] if isinstance(result["reason"], str) and result["reason"]
                    else "required_check_verifier_error"), None
        if not isinstance(result.get("facts"), dict):
            return "required_check_verifier_error", None
        verified.add(check)
        evidence[check] = result["facts"]
    return required_check_reason(candidate, external=external, verified=verified), evidence


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def owned_root(path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(TEST_ROOT.resolve()) or path == TEST_ROOT.resolve():
        raise SavedWorldError("saved-world roots must be dedicated children of data/laya-saved-world")
    return path


def _verify_server_arguments(argv, expected):
    """Reject absent, duplicate or mismatched scope flags in process arguments."""
    if "--" in argv:
        raise SavedWorldError("test instance arguments contain an option terminator")
    for flag, expected_value in expected.items():
        occurrences = []
        for index, argument in enumerate(argv):
            if argument == flag:
                occurrences.append(argv[index + 1] if index + 1 < len(argv) else None)
            elif argument.startswith(flag + "="):
                occurrences.append(argument[len(flag) + 1:])
        if len(occurrences) != 1 or occurrences[0] != expected_value:
            raise SavedWorldError(f"test instance mismatch or duplicate flag: {flag}")


def verify_instance(root: Path, *, runtime="disposable_saved_world") -> dict:
    """Compare local process arguments and file hashes; this does not authenticate RCON."""
    root = owned_root(root)
    manifest = json.loads((root / "manifest.json").read_text())
    if runtime not in {"disposable_saved_world", "disposable_mall"} or manifest.get("runtime") != runtime or manifest.get("production_authority") is not False:
        raise SavedWorldError("not an explicitly disposable saved-world manifest")
    if file_hash(root / "baseline.zip") != manifest["source_sha256"]:
        raise SavedWorldError("immutable baseline hash changed")
    server = root / "server"
    pid = int((server / "factorio.pid").read_text())
    argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
    expected = {"--config": str(server / "config.ini"), "--start-server": str(server / "saves/mod_playground.zip"),
                "--mod-directory": str(server / "mods"), "--rcon-bind": f"127.0.0.1:{manifest['rcon_port']}",
                "--bind": f"127.0.0.1:{manifest['game_port']}"}
    _verify_server_arguments(argv, expected)
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
                "wait_ticks": candidate.get("wait_ticks", 0)},
            "scope": ({"surface": "nauvis", "force": "player", "bounds": None}
                      if candidate.get("family") == "launch" else SCOPE),
            "risk_tier": "destructive_mutation" if candidate.get("family") == "launch"
                         else "ghost_submit" if placements else "read_only"}


def oil_field_digest(field):
    """Compact oil/water facts for the decision state; None when the survey did not run."""
    if not field:
        return None
    digest = {"found": field["found"], "wells": field["wells"], "search_radius": field["search_radius"],
              "anchor": field["anchor"]}
    for key in ("distance", "yield_percent", "max_crude_per_s"):
        if field.get(key) is not None:
            digest[key] = round(field[key], 1)
    if field.get("center"):
        digest["center"] = {axis: round(field["center"][axis], 1) for axis in ("x", "y")}
    water = field.get("water") or {}
    digest["water_distance"] = round(water["distance"], 1) if water.get("distance") is not None else None
    return digest


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
            "tool_registry": {"id": "saved-world", "version": "1.0.0", "hash": sha256_hash({"families":
                ["mine", "smelt", "assemble", "expand", "wait"] +
                (["launch"] if any(c.get("family") == "launch" for c in candidates) else [])})},
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
    if facts.get("oil_field"):
        state["observation_digest"]["oil_field"] = oil_field_digest(facts["oil_field"])
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
SAVED_WORLD_GHOST_FIELDS = frozenset({"action_type", "entity", "position", "direction", "underground_type", "recipe",
                                      "logistic_requests", "logistic_sections", "clear_inventory_limit",
                                      "input_priority", "output_priority"})


def target_key(action):
    return (action["entity"], action["position"]["x"], action["position"]["y"])


def preexisting_targets(plan, inspected):
    """Configuration targets already standing before submission, keyed to their unit numbers.

    Quad slots deliberately share a chest column and a slot may be retargeted, so a
    later plan legitimately configures an entity an earlier transaction (or the
    starter save) built. Such a target is never tagged as built by this
    transaction; it is authorized by the unit number seen here and the executor
    re-checks that number when it configures. A target that is only an unbuilt
    ghost has no identity to bind and is refused.
    """
    states = {(t["entity"], t["position"]["x"], t["position"]["y"]): t
              for t in (inspected or {}).get("targets", ())}
    existing = {}
    for action in configuration_actions(plan):
        key = target_key(action)
        state = states.get(key, {})
        if state.get("state") == "built" and type(state.get("unit_number")) is int and state["unit_number"] > 0:
            existing[key] = state["unit_number"]
        elif state.get("state") == "ghost":
            raise SavedWorldError("configuration target has an unowned pending ghost: %s at (%s, %s)" % key)
    return existing


def submission_plan(plan, evidence_tags=None, preexisting=()):
    """Ghosts lack inventories; defer every setting until construction is observed.

    Targets in ``preexisting`` are submitted untagged: the executor treats an exact
    existing entity as already present and the optimizer ownership claim is made
    only for entities this transaction creates.
    """
    targets = {(a["entity"], a["position"]["x"], a["position"]["y"]) for a in configuration_actions(plan)}
    targets -= set(preexisting)
    phases = []
    for phase in plan["phases"]:
        projected = []
        for action in phase["actions"]:
            ghost = {k: v for k, v in action.items() if k not in CONFIGURATION_FIELDS}
            ghost["action_type"] = "place_ghost_no_reconcile"
            key = (action["entity"], action["position"]["x"], action["position"]["y"])
            if evidence_tags is not None and key in targets:
                ghost["optimizer_ownership"] = dict(evidence_tags)
            projected.append(ghost)
        phases.append({**phase, "actions": projected})
    # Trees and rocks in a footprint are cleared by robots under the ghost, as a blueprint does.
    return {**plan, "phases": phases, "clear_natural_obstacles": True}


def receipt_preexisting(receipt):
    return {(t["entity"], t["position"]["x"], t["position"]["y"]): t["unit_number"]
            for t in receipt.get("preexisting_targets") or ()}


def configuration_actions(plan):
    result=[]
    for action in actions(plan):
        a=dict(action)
        # Furnaces choose a recipe from their feed; set_recipe is an assembler API.
        if a.get('entity') in {'stone-furnace','steel-furnace','electric-furnace'}:a.pop('recipe',None)
        if CONFIGURATION_FIELDS.intersection(a):result.append({**a,'action_type':'configure_entity'})
    return result


class SavedWorldAdapter:
    runtime = "disposable_saved_world"
    surface = "nauvis"
    force = "player"
    def __init__(self, root: Path):
        self.root = owned_root(root)
        self.manifest = verify_instance(self.root, runtime=self.runtime)
        self.lua_source = Path(__file__).with_suffix(".lua").read_text()
        self.code_hashes = {str(p.relative_to(ROOT)): file_hash(p) for p in (
            Path(__file__), Path(__file__).with_suffix(".lua"), ROOT / "schemas/laya_saved_world.schema.json",
            ROOT / "core/disposable_rcon_identity.py",
            ROOT / "training/laya_launch_rocket.lua", ROOT / "tools/disposable_rocket_launch.py",
            ROOT / "training/laya_layout_stream.py", ROOT / "schemas/layout_submission_receipt.schema.json",
            ROOT / "orchestrator/game_bridge.py", ROOT / "orchestrator/guarded_configuration.py",
            ROOT / "schemas/optimizer_build_evidence.schema.json", ROOT / "schemas/optimizer_build_evidence_v3.schema.json",
            ROOT / "training/laya_input_profile.py", ROOT / "tools/run_laya_saved_world.py",
            ROOT / "planners/laya_quad_cell.py", ROOT / "planners/quad_mall_layout.py",
            ROOT / "planners/mall_layout.py")}
        server = self.root / "server"
        try:
            self._server_identity = server_process_identity(server)
        except DisposableIdentityError as error:
            raise SavedWorldError("saved-world server startup identity is unavailable") from error
        def check_socket(sock):
            try:
                if verify_instance(self.root, runtime=self.runtime) != self.manifest:
                    raise SavedWorldError("saved-world manifest changed before authentication")
                verify_connected_server_socket(sock, server=server, expected_port=self.manifest["rcon_port"],
                                               expected_identity=self._server_identity)
                if verify_instance(self.root, runtime=self.runtime) != self.manifest:
                    raise SavedWorldError("saved-world manifest changed during authentication")
            except DisposableIdentityError as error:
                raise SavedWorldError("saved-world RCON peer is unverified") from error
        self.bridge = GameBridge(server / "script-output", host="127.0.0.1", port=self.manifest["rcon_port"],
                                 password=(server / "rcon-password").read_text().strip(), command_timeout=30,
                                 socket_validator=check_socket)

    def _check_instance(self):
        manifest = verify_instance(self.root, runtime=self.runtime)
        if hasattr(self, "_server_identity"):
            try:
                if manifest != self.manifest or server_process_identity(self.root / "server") != self._server_identity:
                    raise SavedWorldError("saved-world server identity changed during the session")
            except DisposableIdentityError as error:
                raise SavedWorldError("saved-world server identity is unavailable") from error

    def close(self):
        self.bridge.close()

    def validate_plan(self, plan):
        validate_build_plan(plan)

    def _guarded_configuration(self, plan, tags, submission_tick, baseline, preexisting=None):
        configs = configuration_actions(plan)
        if not configs:
            return []
        if not isinstance(tags, dict) or type(submission_tick) is not int:
            raise SavedWorldError("configuration requires correlated robot-build evidence")
        try:
            require_configuration_baseline(baseline, tags=tags, surface=self.surface, force=self.force,
                                           submission_tick=submission_tick)
            report = self.bridge.optimizer_build_evidence(
                surface=self.surface, force=self.force, episode_id=tags["optimizer_episode"],
                project_id=tags["optimizer_project"], transaction_id=tags["optimizer_transaction"],
                plan_hash=tags["optimizer_plan_hash"], timeout=30, include_unidentified=True)
            if report.get("version") != "optimizer-build-evidence-v3":
                raise ValueError("configuration requires complete v3 build-event reporting")
            return guard_configuration(configs, report, tags=tags, surface=self.surface, force=self.force,
                                       submission_tick=submission_tick, preexisting=preexisting)
        except (ValueError, KeyError, RuntimeError, AttributeError, OSError) as error:
            raise SavedWorldError("configuration lacks current correlated robot-build evidence") from error

    def _submit_configuration(self, configs, receipt, receipt_path, *, phase):
        """Journal guarded settings before sending; an ambiguous outcome is never resent."""
        plan = {"surface": self.surface, "force": self.force, "atomic": True,
                "phases": [{"name": phase, "actions": configs}]}
        authorization = build_layout_authorization([plan])
        def prepared(request):
            receipt.update(status="configuration_submitting", configuration_request=request)
            atomic_json(receipt_path, receipt)
        report = submit_layout_stream(self.root, self.bridge, authorization, plan,
                                      transaction=receipt_path.name, prepared=prepared)
        receipt.update(configuration=report,
                       status="configuration_received" if report["ok"] else "configuration_failed")
        atomic_json(receipt_path, receipt)
        if not report["ok"]:
            raise SavedWorldError("configuration failed")

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
        self._check_instance()
        centers = self.quad_centers()
        name = f"laya_saved_world/{uuid.uuid4().hex}.json"
        request = json.dumps(_query_request(candidates, inspect, getattr(self, "extra_recipes", ())),
                             separators=(",", ":"))
        # Strip comments from the Lua source before joining lines; Lua comments
        # otherwise swallow the rest of the RCON command. Only the source is
        # stripped: request data may legitimately contain "--".
        source = " ".join(line.split("--", 1)[0] for line in self.lua_source.splitlines())
        # JSON is a Lua string via a long-bracket delimiter absent from JSON.
        delimiter = "="
        while f"]{delimiter}]" in request:
            delimiter += "="
        body = f"local request=helpers.json_to_table([{delimiter}[{request}]{delimiter}]); " + source
        cmd = f'/sc local ok,value=pcall(function() {body} end); helpers.write_file("{name}",helpers.table_to_json({{ok=ok,value=ok and value or nil,error=not ok and tostring(value) or nil}}),false); rcon.print("saved-world-report")'
        reply = self.bridge.command(cmd)
        report = self.bridge.script_output / name
        if not report.is_file():
            # The protected body always writes a report, so a missing one means the
            # command itself failed (parse error, size limit); surface the server reply.
            raise SavedWorldError(f"saved-world query produced no report ({len(cmd)} bytes); "
                                  f"server replied: {str(reply)[:600]!r}")
        result = load_json(report)
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

    def options(self, facts, seed=0, *, additional_candidates=(), check_verifier=None):
        """Legal alternatives. ``check_verifier`` (see tools.optimizer_saved_world)
        independently inspects the same query's world facts for required checks;
        without it every candidate with required checks stays pruned."""
        external = list(additional_candidates)
        external_ids = {candidate["option_id"] for candidate in external}
        proposed = compile_candidates(facts, seed) + external
        if len({candidate["option_id"] for candidate in proposed}) != len(proposed):
            raise SavedWorldError("candidate option IDs must be unique")
        # Keep all pruned options in evidence. Pruning is legality, not ranking.
        response = {"validations": {}}
        if proposed:
            sent = [check_verifier.prepare(c) if check_verifier is not None and c.get("required_checks") else c for c in proposed]
            response = self.query(sent)
        results = response["validations"] if proposed else {}
        evidence = {}
        for candidate in proposed:
            result = results.get(candidate["option_id"])
            is_external = candidate["option_id"] in external_ids
            if check_verifier is not None and candidate.get("required_checks"):
                reason, checked = evaluate_required_checks(candidate, check_verifier, self, result, response,
                                                           external=is_external)
                if checked:
                    evidence[candidate["option_id"]] = checked
            else:
                reason = required_check_reason(candidate, external=is_external)
            if reason:
                results[candidate["option_id"]] = {**(result or {}), "reason": (result or {}).get("reason") or reason,
                                                   "required_check_reason": reason}
                continue
            if (result is not None and not result.get("reason") and "bill" in candidate
                    and result.get("bill") != candidate["bill"]):
                results[candidate["option_id"]] = {**result, "reason": "construction_bill_changed"}
        legal = [{**c, "bill": results[c["option_id"]].get("bill") or {},
                  **({"check_evidence": evidence[c["option_id"]]} if c["option_id"] in evidence else {})}
                 for c in proposed if not results[c["option_id"]].get("reason")]
        # Leave head-room for instructions and typed option markers in Laya's
        # 256-token decision head. The complete sequence still has its own gate.
        legal = bounded_menu(legal, seed, limit=6)
        legal += [{"option_id": f"wait.{ticks}", "family": "wait", "summary": f"Wait {ticks / 60:g} game seconds", "wait_ticks": ticks}
                  for ticks in (300, 1800)]
        return legal, {"proposed": proposed, "validations": results}

    def advance(self, ticks):
        if not isinstance(ticks, int) or not 1 <= ticks <= 18000:
            raise SavedWorldError("step must be 1..18000 ticks")
        self._check_instance()
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

    def execute(self, selected, presented, measure_ticks=1800, check_verifier=None):
        matches = [c for c in presented if c["option_id"] == selected]
        if len(matches) != 1:
            raise SavedWorldError("selection must resolve to one presented candidate")
        candidate = matches[0]
        verified_later = (check_verifier is not None and bool(candidate.get("required_checks"))
                          and bool(candidate.get("plan")))
        # With a verifier the checks run on fresh facts below; structure is gated now.
        reason = (_required_checks_structure_reason(candidate) if verified_later
                  else required_check_reason(candidate))
        if reason:
            raise SavedWorldError(f"selected plan lacks required validation: {reason}")
        if candidate.get("plan"):
            plan = candidate["plan"]
            if plan.get("surface") != self.surface or plan.get("force") != self.force or plan.get("atomic") is not True:
                raise SavedWorldError("saved-world plans require explicit atomic Nauvis/player scope")
            if any(a.get("action_type") != "place_ghost" or set(a) - SAVED_WORLD_GHOST_FIELDS for a in actions(plan)):
                raise SavedWorldError("saved-world submissions allow funded ghosts only")
        elif candidate.get("family") != "wait" or candidate.get("wait_ticks") not in (300, 1800):
            raise SavedWorldError("unsupported saved-world action")
        before = self.query()
        receipt = {"candidate": candidate, "before": before, "status": "pending",
                   "configuration_protocol": "correlated-v1", "adapter_hashes": getattr(self, "code_hashes", {})}
        receipt_path = self.root / "evidence" / f"transaction-{uuid.uuid4().hex}.json"
        receipt_path.parent.mkdir(exist_ok=True)
        atomic_json(receipt_path, receipt)
        if candidate.get("plan"):
            self.validate_plan(candidate["plan"])
            fresh_response = self.query([check_verifier.prepare(candidate) if verified_later else candidate])
            fresh = fresh_response["validations"][selected]
            if fresh.get("reason"):
                receipt.update(status="rejected", reason=fresh["reason"])
                atomic_json(receipt_path, receipt)
                raise SavedWorldError(f"selected plan became illegal: {fresh['reason']}")
            if "bill" in candidate and fresh.get("bill") != candidate["bill"]:
                receipt.update(status="rejected", reason="construction_bill_changed")
                atomic_json(receipt_path, receipt)
                raise SavedWorldError("selected plan construction bill changed")
            if verified_later:
                reason, checked = evaluate_required_checks(candidate, check_verifier, self, fresh, fresh_response)
                if not reason and candidate.get("check_evidence") is None:
                    reason = "required_check_option_evidence_missing"
                elif not reason:
                    # A fact the checks relied on at option time must be unchanged now.
                    changed = next((c for c in candidate["required_checks"]
                                    if candidate["check_evidence"].get(c) != checked.get(c)), None)
                    if changed:
                        reason = "required_check_facts_changed:" + changed
                if reason:
                    receipt.update(status="rejected", reason=reason, required_check_evidence=checked)
                    atomic_json(receipt_path, receipt)
                    raise SavedWorldError(f"selected plan lacks required validation: {reason}")
                receipt["required_check_evidence"] = checked
            configs = configuration_actions(candidate["plan"])
            tags = None
            if configs:
                canonical = json.dumps(candidate["plan"], sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=True, allow_nan=False).encode("ascii")
                tags = {"optimizer_episode": hashlib.sha256(str(self.root).encode()).hexdigest(),
                        "optimizer_project": "saved-world-configuration", "optimizer_transaction": uuid.uuid4().hex,
                        "optimizer_plan_hash": hashlib.sha256(canonical).hexdigest()}
            receipt["build_evidence_tags"] = tags
            if tags:
                try:
                    baseline = self.bridge.optimizer_build_evidence(
                        surface=self.surface, force=self.force, episode_id=tags["optimizer_episode"],
                        project_id=tags["optimizer_project"], transaction_id=tags["optimizer_transaction"],
                        plan_hash=tags["optimizer_plan_hash"], timeout=30, include_unidentified=True)
                    baseline_tick = require_configuration_baseline(baseline, tags=tags, surface=self.surface, force=self.force)
                    if type(before.get("tick")) is not int or baseline_tick < before["tick"]:
                        raise ValueError("configuration evidence baseline predates the decision snapshot")
                except (ValueError, KeyError, RuntimeError, AttributeError, OSError) as error:
                    receipt.update(status="rejected", reason="configuration_evidence_baseline_unavailable")
                    atomic_json(receipt_path, receipt)
                    raise SavedWorldError("configuration evidence baseline is unavailable") from error
                receipt["build_evidence_baseline"] = baseline
                try:
                    existing = preexisting_targets(candidate["plan"], self.query(inspect=candidate["plan"]))
                except SavedWorldError:
                    receipt.update(status="rejected", reason="configuration_target_unowned_ghost")
                    atomic_json(receipt_path, receipt)
                    raise
                # Journaled before submission so a resumed reconcile reuses the same identities.
                receipt["preexisting_targets"] = [{"entity": e, "position": {"x": x, "y": y}, "unit_number": unit}
                                                  for (e, x, y), unit in existing.items()]
                atomic_json(receipt_path, receipt)
            else:
                existing = {}
            ghosts = submission_plan(candidate["plan"], tags, existing)
            authorization = build_layout_authorization([ghosts])
            def prepared(request):
                receipt.update(status="submitting", layout_request=request)
                atomic_json(receipt_path, receipt)
            report = submit_layout_stream(self.root, self.bridge, authorization, ghosts,
                                          transaction=receipt_path.name, prepared=prepared)
            receipt.update(submission=report, status="submission_received" if report["ok"] else "submission_failed")
            atomic_json(receipt_path, receipt)
            if not report["ok"]:
                raise SavedWorldError(f"ghost submission failed: {report.get('error')}")
        self.advance(candidate.get("wait_ticks", measure_ticks))
        after = self.query()
        # Configuration is applied only to observed bot-built entities. No
        # direct placement, item transfer or production grant is permitted.
        owned = self.query(inspect=candidate["plan"]) if candidate.get("plan") else None
        if owned and owned["pending"] == 0 and owned["missing"] == 0:
            configs = self._guarded_configuration(candidate["plan"], receipt.get("build_evidence_tags"),
                                                  receipt["submission"].get("tick"), receipt.get("build_evidence_baseline"),
                                                  receipt_preexisting(receipt))
            if configs:
                self._submit_configuration(configs, receipt, receipt_path, phase="configure-observed-build")
                self.advance(measure_ticks)
                after = self.query()
        receipt.update(status="observed" if not owned or (owned["pending"] == 0 and owned["missing"] == 0) else "construction_pending",
                       construction=owned, after=after, measurement=measured_outcome(before, after))
        atomic_json(receipt_path, receipt)
        return receipt

    def reconcile(self):
        """Finish only the configuration already selected and durably submitted.

        No new layout or recovery decision is chosen on the director's behalf.
        Interrupted submissions remain ambiguous and require explicit review.
        """
        evidence = self.root / "evidence"
        def store_recovered(request, report):
            path = evidence / request["transaction"]
            receipt = load_json(path)
            status = receipt.get("status")
            request_field = {"submitting": "layout_request", "configuration_submitting": "configuration_request"}.get(status)
            if request_field is None or receipt.get(request_field) != request:
                raise SavedWorldError(f"layout stream transaction mismatch: {path.name}")
            if status == "configuration_submitting":
                receipt.update(configuration=report,
                               status="configuration_recovered" if report["ok"] else "configuration_failed")
            else:
                receipt.update(submission=report, status="submission_recovered" if report["ok"] else "submission_failed")
            atomic_json(path, receipt)
        if (evidence / "layout-stream.json").exists():
            try:
                recover_layout_stream(self.root, self.bridge, resolved=store_recovered)
            except Exception as error:
                raise SavedWorldError("pending layout submission could not be recovered; outcome is unknown") from error
        for path in sorted(evidence.glob("transaction-*.json")):
            receipt = load_json(path)
            if receipt["status"] == "pending":
                raise SavedWorldError(f"interrupted transaction needs review: {path.name}")
            if receipt["status"] == "submitting":
                request = receipt["layout_request"]
                try:
                    report = self.bridge.read_layout_receipt(
                        request["authorization"], request["build_plan"], request_id=request["request_id"],
                        stream_id=request["stream_id"], sequence=request["sequence"])
                except Exception as error:
                    raise SavedWorldError(f"interrupted submission outcome is unknown: {path.name}") from error
                receipt.update(submission=report, status="submission_recovered" if report["ok"] else "submission_failed")
                atomic_json(path, receipt)
            if receipt["status"] == "configuration_submitting":
                request = receipt["configuration_request"]
                try:
                    report = self.bridge.read_layout_receipt(
                        request["authorization"], request["build_plan"], request_id=request["request_id"],
                        stream_id=request["stream_id"], sequence=request["sequence"])
                except Exception as error:
                    raise SavedWorldError(f"interrupted configuration outcome is unknown: {path.name}") from error
                receipt.update(configuration=report,
                               status="configuration_recovered" if report["ok"] else "configuration_failed")
                atomic_json(path, receipt)
            if receipt["status"] in {"submission_received", "submission_recovered", "submission_failed"}:
                raise SavedWorldError(f"submission needs review before configuration: {path.name}")
            if receipt["status"] in {"configuration_received", "configuration_recovered", "configuration_failed"}:
                raise SavedWorldError(f"configuration outcome needs review: {path.name}")
            if receipt["status"] != "construction_pending":
                continue
            candidate = receipt["candidate"]
            owned = self.query(inspect=candidate["plan"])
            receipt["construction"] = owned
            if owned["pending"] == 0 and owned["missing"] == 0:
                if configuration_actions(candidate["plan"]) and receipt.get("configuration_protocol") != "correlated-v1":
                    raise SavedWorldError(f"legacy configuration outcome needs review: {path.name}")
                configs = self._guarded_configuration(candidate["plan"], receipt.get("build_evidence_tags"),
                                                      (receipt.get("submission") or {}).get("tick"),
                                                      receipt.get("build_evidence_baseline"),
                                                      receipt_preexisting(receipt))
                if configs:
                    self._submit_configuration(configs, receipt, path, phase="finish-selected-configuration")
                receipt["status"] = "observed"
                receipt["configured_tick"] = self.query()["tick"]
            atomic_json(path, receipt)
