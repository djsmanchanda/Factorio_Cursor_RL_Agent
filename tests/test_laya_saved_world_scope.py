# Path: tests/test_laya_saved_world_scope.py | Purpose: A per-option declared scope reaches only the surveyed oil outpost; others keep BOUNDS.
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import training.laya_saved_world as saved_world
from tests.test_laya_clear_obstacles import _WORLD
from tests.test_laya_saved_world import _SURVEY_LUA_FIXTURE
from training.laya_saved_world import (BOUNDS, SCOPE, SavedWorldAdapter, declared_scope_reason, ghost,
                                       state_option)

# The current save: crude about 297 tiles east of the lowest roboport at the origin.
OIL = {"oil_field": {"found": True, "anchor": {"x": 0, "y": 0}, "center": {"x": 297.0, "y": 10.0},
                     "search_radius": 512, "wells": 8}}


def candidate(points, scope=None, option_id="oil.1"):
    plan = {"surface": "nauvis", "force": "player", "atomic": True,
            "phases": [{"name": "oil", "actions": [ghost("pumpjack", x, y) for x, y in points]}]}
    return {"option_id": option_id, "family": "oil", "summary": "s", "plan": plan,
            **({"scope_bounds": scope} if scope is not None else {})}


def tight(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return {"x_min": int(min(xs)) - 3, "y_min": int(min(ys)) - 3,
            "x_max_exclusive": int(max(xs)) + 4, "y_max_exclusive": int(max(ys)) + 4}


@pytest.mark.parametrize("points", [[(297.5, 10.5), (301.5, 12.5)],      # at the outpost
                                    [(120.5, 4.5), (140.5, 4.5)],         # a reach hop leaving BOUNDS
                                    [(200.5, 40.5), (210.5, 44.5)],       # a mid-route hop, off both areas
                                    [(252.5, 113.5), (254.5, 117.5)]])    # the cell's coal mine, ~113 tiles past the wells
def test_tight_scope_at_the_outpost_or_leaving_bounds_is_accepted(points):
    assert declared_scope_reason(candidate(points, tight(points)), OIL) is None


def test_candidate_without_declared_scope_is_unchanged():
    plain = candidate([(10.5, 10.5)])
    assert declared_scope_reason(plain, {}) is None
    assert state_option(plain)["scope"] == SCOPE and SCOPE["bounds"] == BOUNDS
    scoped = candidate([(297.5, 10.5)], tight([(297.5, 10.5)]))
    assert state_option(scoped)["scope"] == {**SCOPE, "bounds": scoped["scope_bounds"]}


@pytest.mark.parametrize("points, scope, facts, reason", [
    ([(297.5, 10.5)], {"x_min": 290, "y_min": 0}, OIL, "scope_malformed"),
    ([(297.5, 10.5)], {"x_min": 290.5, "y_min": 0, "x_max_exclusive": 300, "y_max_exclusive": 20}, OIL, "scope_malformed"),
    ([(297.5, 10.5)], tight([(297.5, 10.5)]), {}, "scope_unsurveyed"),
    ([(297.5, 10.5)], tight([(297.5, 10.5)]), {"oil_field": {**OIL["oil_field"], "found": False}}, "scope_unsurveyed"),
    ([(297.5, 10.5), (310.5, 10.5)], tight([(297.5, 10.5)]), OIL, "scope_excludes_actions"),
    ([(297.5, 10.5)], {"x_min": 200, "y_min": 0, "x_max_exclusive": 302, "y_max_exclusive": 15}, OIL, "scope_too_wide"),
    ([(297.5, 10.5)], {"x_min": -500, "y_min": -500, "x_max_exclusive": 500, "y_max_exclusive": 500}, OIL, "scope_too_wide"),
    ([(510.5, 10.5)], tight([(510.5, 10.5)]), OIL, "scope_out_of_reach"),
    ([(200.5, 200.5)], tight([(200.5, 200.5)]), OIL, "scope_off_route"),
])
def test_declared_scope_refusals_are_typed(points, scope, facts, reason):
    assert declared_scope_reason(candidate(points, scope), facts) == reason


class _Bridge:
    """Answers the adapter's Lua query: facts, or every sent candidate as legal."""

    def __init__(self, root: Path):
        self.script_output = root
        self.requests = []

    def command(self, cmd):
        name = re.search(r'write_file\("([^"]+)"', cmd).group(1)
        request = json.loads(re.search(r"\[(=+)\[(.*?)\]\1\]", cmd).group(2))
        self.requests.append(request)
        value = ({"tick": 7, "validations": {c["option_id"]: {"bill": {}} for c in request["candidates"]}}
                 if request["candidates"] is not None else {"tick": 7, **OIL})
        (self.script_output / name).parent.mkdir(parents=True, exist_ok=True)
        (self.script_output / name).write_text(json.dumps({"ok": True, "value": value}))
        return "saved-world-report"


def _adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(saved_world, "FACTS_VALIDATOR", type("V", (), {"iter_errors": lambda self, v: []})())
    adapter = object.__new__(SavedWorldAdapter)
    adapter._check_instance = lambda: None
    adapter.quad_centers = lambda: []
    adapter.lua_source = "return 1"
    adapter.bridge = _Bridge(tmp_path)
    return adapter


def test_existing_candidates_send_one_unchanged_query(tmp_path, monkeypatch):
    adapter = _adapter(tmp_path, monkeypatch)
    plain = candidate([(10.5, 10.5)], option_id="assemble.1")
    result = adapter.query([plain])
    assert result["validations"] == {"assemble.1": {"bill": {}}}
    assert adapter.bridge.requests == [saved_world._query_request([plain])]


def test_refused_scope_never_reaches_lua_and_accepted_scope_does(tmp_path, monkeypatch):
    adapter = _adapter(tmp_path, monkeypatch)
    good = candidate([(297.5, 10.5)], tight([(297.5, 10.5)]), option_id="oil.good")
    wide = candidate([(297.5, 10.5)], dict(BOUNDS, x_max_exclusive=400), option_id="oil.wide")
    plain = candidate([(10.5, 10.5)], option_id="assemble.1")
    result = adapter.query([good, wide, plain])
    assert result["validations"] == {"oil.good": {"bill": {}}, "assemble.1": {"bill": {}},
                                     "oil.wide": {"reason": "scope_too_wide"}}
    facts_request, validation_request = adapter.bridge.requests
    assert facts_request["candidates"] is None
    assert [c["option_id"] for c in validation_request["candidates"]] == ["oil.good", "assemble.1"]
    assert validation_request["candidates"][0]["scope_bounds"] == good["scope_bounds"]
    assert validation_request["bounds"] == BOUNDS


def test_all_refused_skips_the_validation_query(tmp_path, monkeypatch):
    adapter = _adapter(tmp_path, monkeypatch)
    result = adapter.query([candidate([(200.5, 200.5)], tight([(200.5, 200.5)]))])
    assert result == {"tick": 7, "validations": {"oil.1": {"reason": "scope_off_route"}}}
    assert len(adapter.bridge.requests) == 1


def _run_lua(candidates: str, assertions: str):
    lua = shutil.which("lua")
    if lua is None:
        pytest.skip("Lua interpreter required")
    fixture = _SURVEY_LUA_FIXTURE.replace("__EXTRA_RECIPES__", "{}").replace("__MALFORMED_RECIPES__", "")
    # The clear-obstacles world (bounds +-50, one funded network); its roboport reaches the far site.
    world = (_WORLD.replace("__OBSTACLES__", "").replace("__WATER__", "false").replace("__DIRECTION__", "north")
             .replace("construction_radius=40", "construction_radius=400"))
    body = (fixture + world + "\nrequest.candidates=" + candidates + "\nlocal facts=(function()\n"
            + Path("training/laya_saved_world.lua").read_text() + "\nend)()\n" + assertions)
    result = subprocess.run([lua, "-"], input=body, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def _lua_candidate(option_id, x, scope=""):
    return (f"{{option_id='{option_id}',network='1',{scope}plan={{phases={{{{actions={{"
            f"{{action_type='place_ghost',entity='wide',position={{x={x},y=10.5}},direction='north'}}}}}}}}}}}}")


@pytest.mark.lua
def test_lua_declared_scope_replaces_bounds_for_that_candidate_only():
    scope = "scope_bounds={x_min=60,y_min=0,x_max_exclusive=80,y_max_exclusive=20},"
    candidates = "{" + ",".join([
        _lua_candidate("far", 70.5),                  # outside +-50 without a scope
        _lua_candidate("far_scoped", 70.5, scope),     # inside its declared scope
        _lua_candidate("near", 10.5),                  # existing in-bounds candidate
        _lua_candidate("near_scoped", 10.5, scope),    # a declared scope replaces, never widens
    ]) + "}"
    _run_lua(candidates, """
local v=facts.validations
assert(v.far.reason=='scope', tostring(v.far.reason))
assert(v.far_scoped.reason==nil, tostring(v.far_scoped.reason))
assert(v.near.reason==nil, tostring(v.near.reason))
assert(v.near_scoped.reason=='scope', tostring(v.near_scoped.reason))
""")
