# Path: tools/optimizer_research_capture.py | Purpose: Capture paused disposable-world catalogs for research supply planning.
"""Opt-in observation; catalog files are written by the server and are not pruned.

The caller must exclude other runners and arbitrary commands. The layout lock
coordinates cooperating writers only. Nothing here selects research, builds,
authenticates a save, or authorizes dispatch.
"""

from collections.abc import Mapping
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.catalog_demand import mod_set_sha256
from core.research_path_demand import plan_research_science_supply
from training.laya_layout_stream import _locked as lock_stream
from training.laya_saved_world import SavedWorldAdapter


QUERY_SOURCE = ROOT / "training/laya_saved_world.lua"
PAUSE_ASSERTION = 'assert(game.tick_paused and game.ticks_to_run == 0, "saved-world clock must be paused")'


class ResearchCaptureError(ValueError):
    pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ResearchCaptureError("duplicate catalog field")
        result[key] = value
    return result


def _parse(raw):
    if not isinstance(raw, bytes) or len(raw) > 32 * 1024 * 1024:
        raise ResearchCaptureError("catalog bytes are unavailable or too large")
    try:
        result = json.loads(raw, object_pairs_hook=_unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(ResearchCaptureError("invalid JSON number")))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ResearchCaptureError("invalid catalog JSON") from error
    if not isinstance(result, dict):
        raise ResearchCaptureError("catalog must be an object")
    return result


def _scope(facts, adapter):
    if (not isinstance(facts, Mapping) or facts.get("surface") != adapter.surface
            or facts.get("force") != adapter.force or type(facts.get("tick")) is not int or facts["tick"] < 0):
        raise ResearchCaptureError("paused world scope or tick unavailable")
    mods = facts.get("mods")
    if (not isinstance(mods, Mapping) or not mods
            or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in mods.items())):
        raise ResearchCaptureError("paused world mod set unavailable")
    return facts["tick"], dict(mods)


def _validate_request(targets, horizon_ticks, game_version):
    if (not isinstance(game_version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", game_version)
            or type(horizon_ticks) is not int or horizon_ticks <= 0
            or not isinstance(targets, list) or not targets
            or any(not isinstance(name, str) or not name for name in targets)
            or len(set(targets)) != len(targets)):
        raise ResearchCaptureError("explicit game version, targets and positive horizon are required")


def capture_paused_research_supply(adapter, *, targets, horizon_ticks, game_version,
                                   boundaries=(), preferences=None):
    """Capture two catalogs and plan gross science at the same paused tick.

    ``game_version`` is an explicit caller assertion: Lua cannot establish the
    engine version. The returned raw bytes permit independent revalidation.
    Same tick and matching mod declarations do not rule out other paused-world
    commands, spoofed reports, or a different save. Failed planning can still
    have written catalog reports to the disposable server's script-output tree.
    """
    if (type(adapter) is not SavedWorldAdapter or adapter.runtime != "disposable_saved_world"
            or adapter.lua_source != QUERY_SOURCE.read_text(encoding="utf-8")
            or PAUSE_ASSERTION not in adapter.lua_source
            or adapter.code_hashes.get(str(QUERY_SOURCE.relative_to(ROOT)))
            != hashlib.sha256(adapter.lua_source.encode("utf-8")).hexdigest()):
        raise ResearchCaptureError("unmodified paused saved-world adapter required")
    _validate_request(targets, horizon_ticks, game_version)
    with lock_stream(adapter.root):
        before = SavedWorldAdapter.query(adapter)
        tick, mods = _scope(before, adapter)
        SavedWorldAdapter._check_instance(adapter)
        runtime = adapter.bridge.export_runtime_catalog(adapter.surface, adapter.force)
        SavedWorldAdapter._check_instance(adapter)
        recipes = adapter.bridge.export_recipe_catalog_capture(adapter.force)
        after = SavedWorldAdapter.query(adapter)
        if _scope(after, adapter) != (tick, mods):
            raise ResearchCaptureError("paused world tick, scope or mod set changed during capture")
        runtime_report, recipe_report = _parse(runtime), _parse(recipes)
        if (runtime_report.get("tick") != tick or type(runtime_report.get("tick")) is not int
                or recipe_report.get("version") != "1.3.0" or recipe_report.get("tick") != tick
                or type(recipe_report.get("tick")) is not int or recipe_report.get("force") != adapter.force
                or recipe_report.get("active_mods") != mods
                or type(recipe_report.get("active_mods_count")) is not int
                or recipe_report["active_mods_count"] != len(mods)):
            raise ResearchCaptureError("captured catalog tick, force or mod set mismatch")
        entries = [{"name": name, "version": version} for name, version in sorted(mods.items())]
        provenance = {"catalog_sha256": hashlib.sha256(recipes).hexdigest(), "game_version": game_version,
                      "surface": adapter.surface, "force": adapter.force, "export_tick": tick,
                      "export_command": "/export_recipe_catalog", "mods": entries,
                      "mod_set_sha256": mod_set_sha256(entries)}
        runtime_hash = hashlib.sha256(runtime).hexdigest()
        path, demand = plan_research_science_supply(runtime, recipes, runtime_sha256=runtime_hash,
                                                    recipe_provenance=provenance, surface=adapter.surface,
                                                    force=adapter.force, active_mods=mods, targets=targets,
                                                    horizon_ticks=horizon_ticks, boundaries=boundaries,
                                                    preferences=preferences)
        return {"version": "paused-research-capture-v1", "tick": tick, "runtime_catalog": runtime,
                "recipe_catalog": recipes, "recipe_provenance": provenance,
                "runtime_catalog_sha256": runtime_hash, "research_path": path, "demand": demand,
                "game_version_basis": "caller assertion", "dispatch_authorized": False}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Existing owned disposable saved-world root")
    parser.add_argument("--target", action="append", required=True, help="Exact technology name; repeat for multiple")
    parser.add_argument("--horizon-ticks", type=int, required=True)
    parser.add_argument("--game-version", required=True, help="Caller-verified engine version, e.g. 2.1.21")
    parser.add_argument("--boundaries", default="[]", help="JSON array of explicit external input rates or caps")
    parser.add_argument("--preferences", default="{}", help="JSON object mapping products to selected recipes")
    args = parser.parse_args(argv)
    try:
        boundaries, preferences = json.loads(args.boundaries), json.loads(args.preferences)
        _validate_request(args.target, args.horizon_ticks, args.game_version)
        if not isinstance(boundaries, list) or not isinstance(preferences, dict):
            raise ResearchCaptureError("boundaries must be an array and preferences must be an object")
        adapter = SavedWorldAdapter(args.root)
        try:
            result = capture_paused_research_supply(adapter, targets=args.target, horizon_ticks=args.horizon_ticks,
                                                     game_version=args.game_version, boundaries=boundaries,
                                                     preferences=preferences)
        finally:
            adapter.close()
        output = {key: value for key, value in result.items() if key not in {"runtime_catalog", "recipe_catalog", "demand"}}
        output["runtime_request_id"] = _parse(result["runtime_catalog"]).get("request_id")
        output["recipe_request_id"] = _parse(result["recipe_catalog"]).get("request_id")
        output["demand"] = asdict(result["demand"])
        print(json.dumps(output, sort_keys=True, indent=2, default=str))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"research capture stopped: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
