# Path: tests/test_optimizer_block_trial.py | Purpose: Offline proof of the red-block trial driver's gating, receipts, site search, build loop and report.
"""Fake adapter and bridge only: nothing here contacts Factorio, RCON or a real saved world."""

from __future__ import annotations

import hashlib
import json
import re
from argparse import Namespace
from fractions import Fraction
from pathlib import Path

import pytest

from core.optimizer_construction_state import ConstructionScope
from tests.test_fluid_topology import MODS, catalog_bytes, entity
from tests.test_optimizer_demand_to_ghost import _FIXTURE, _provenance
from tools import optimizer_block_trial as bt
from tools import optimizer_saved_world as integration
from training.laya_saved_world import SavedWorldAdapter, SavedWorldError

PORT = {"position": {"x": 3, "y": -1}, "energy": 100000000, "network": "1", "construction_radius": 55,
        "logistic_radius": 25}
SUBSTATIONS = [(8, -8, 1234), (8, 10, 1235), (-10, 10, 1236), (-10, -8, 1237)]
STOCK = {"transport-belt": 50, "inserter": 20, "medium-electric-pole": 50, "passive-provider-chest": 16,
         "requester-chest": 6, "assembling-machine-1": 6, "substation": 20}


def pole(x, y, unit, **extra):
    return {"name": "substation", "position": {"x": x, "y": y}, "wire": 18, "supply": 9, "grid": "1",
            "generated": True, "quality": "normal", "unit_number": unit, **extra}


def world_facts(tick=100, items=None):
    return {"version": "saved-world-v1", "tick": tick, "surface": "nauvis", "force": "player", "mods": dict(MODS),
            "ports": [dict(PORT)], "poles": [pole(*s) for s in SUBSTATIONS],
            "networks": {"1": {"items": dict(STOCK if items is None else items), "reserved": {}}},
            "production": {bt.PACK: 0, "copper-plate": 0, "iron-gear-wheel": 0},
            "consumption": {bt.PACK: 0, "copper-plate": 0, "iron-gear-wheel": 0},
            "machines": [], "pending_ghosts": 0}


class FakeBridge:
    """Answers each Lua command by writing the JSON file the real server would write."""

    def __init__(self, script_output, handler):
        self.script_output, self.handler, self.commands = Path(script_output), handler, []

    def command(self, text):
        self.commands.append(text)
        name = re.search(r'helpers\.write_file\("([^"]+)"', text).group(1)
        try:
            payload = {"ok": True, "value": self.handler(text)}
        except bt.LuaError as error:
            payload = {"ok": False, "error": str(error).split(": ", 1)[-1]}
        path = self.script_output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
        return "optimizer-block-trial"


class FakeAdapter:
    surface, force = "nauvis", "player"

    def __init__(self, root, handler=None, facts=None):
        self.root = Path(root)
        self.bridge = FakeBridge(self.root / "server/script-output", handler or (lambda text: {}))
        self.facts = facts or world_facts()
        self.queries, self.advanced, self.closed, self.reconciled = [], [], False, 0

    def _check_instance(self):
        pass

    def query(self, candidates=None, inspect=None):
        self.queries.append(inspect)
        return self.facts

    def advance(self, ticks):
        self.advanced.append(ticks)

    def reconcile(self):
        self.reconciled += 1

    def close(self):
        self.closed = True


def args(**overrides):
    values = dict(root=Path("."), episode_id="trial", prestock=False, prestock_item=[], allow_repeat_prestock=False,
                  measure_ticks=3600, warmup_ticks=0, max_build_ticks=1200, build_chunk_ticks=600, margin=2,
                  max_site_attempts=4, min_ratio=0.9, game_version=None, report=None)
    values.update(overrides)
    return Namespace(**values)


@pytest.fixture
def root(tmp_path, monkeypatch):
    path = tmp_path.resolve()
    monkeypatch.setattr(bt, "verify_world", lambda _root: (path, {"production_authority": False}))
    return path


# --------------------------------------------------------------------------- world gate

def test_refuses_unverified_root_or_production_manifest(tmp_path, monkeypatch):
    # A path outside data/laya-saved-world is refused by the real ownership check.
    with pytest.raises(bt.TrialError, match="refusing"):
        bt.verify_world(tmp_path)
    monkeypatch.setattr(bt, "owned_root", lambda path: Path(path))
    def stale(_root):
        raise SavedWorldError("immutable baseline hash changed")
    monkeypatch.setattr(bt, "verify_instance", stale)
    with pytest.raises(bt.TrialError, match="baseline hash"):
        bt.verify_world(tmp_path)
    monkeypatch.setattr(bt, "verify_instance", lambda _root: {"runtime": "disposable_saved_world",
                                                              "production_authority": True})
    with pytest.raises(bt.TrialError, match="non-production"):
        bt.verify_world(tmp_path)
    monkeypatch.setattr(bt, "verify_instance", lambda _root: {"runtime": "disposable_saved_world",
                                                              "production_authority": False})
    assert bt.verify_world(tmp_path)[1]["production_authority"] is False


def test_unverified_root_never_reaches_the_adapter(tmp_path):
    adapter = FakeAdapter(tmp_path)
    with pytest.raises(bt.TrialError, match="refusing"):
        bt.run_trial(args(root=tmp_path), adapter=adapter)
    assert adapter.bridge.commands == [] and adapter.queries == []


# --------------------------------------------------------------------------- prestock

def prestock_handler(before=None, accepted=None, error=None, seen=None):
    def handler(text):
        if seen is not None:
            seen.append(text)
        if error:
            raise bt.LuaError(error)
        assert "storage-chest" in text
        request = json.loads(re.search(r"\[=*\[(.*?)\]=*\]", text).group(1))
        start = dict(before or {"transport-belt": 3})
        got = {i["name"]: (accepted or {}).get(i["name"], i["count"]) for i in request["items"]}
        return {"tick": 100, "unit_number": 77, "position": {"x": 1.5, "y": 2.5}, "before": start,
                "inserted_reported": got, "after": {k: start.get(k, 0) + got.get(k, 0) for k in {*start, *got}}}
    return handler


def test_prestock_writes_pending_receipt_before_command_then_finalizes(root):
    seen_receipts = []
    def handler(text):
        seen_receipts.extend(json.loads(p.read_text())["status"] for p in (root / "evidence").glob("prestock-*.json"))
        return prestock_handler()(text)
    adapter = FakeAdapter(root, handler)
    receipt = bt.prestock_items(adapter, root, items=bt.DEFAULT_PRESTOCK, network="1")
    assert seen_receipts == ["pending"]  # durable before the command was sent
    saved = json.loads(Path(receipt["path"]).read_text())
    assert saved["status"] == "completed" and saved["tick"] == 100
    assert saved["items_requested"] == bt.DEFAULT_PRESTOCK == saved["items_inserted_readback"]
    assert saved["chest"] == {"unit_number": 77, "position": {"x": 1.5, "y": 2.5}}
    assert saved["contents_before"] == {"transport-belt": 3}
    assert bt.trial_kind(root) == ("block_test_prestocked", receipt["path"])


def test_prestock_is_not_repeated_without_the_explicit_flag(root):
    seen = []
    adapter = FakeAdapter(root, prestock_handler(seen=seen))
    bt.prestock_items(adapter, root, items=bt.DEFAULT_PRESTOCK, network="1")
    with pytest.raises(bt.TrialError, match="already exists"):
        bt.prestock_items(adapter, root, items=bt.DEFAULT_PRESTOCK, network="1")
    assert len(seen) == 1
    bt.prestock_items(adapter, root, items=bt.DEFAULT_PRESTOCK, network="1", allow_repeat=True)
    assert len(seen) == 2 and len(list((root / "evidence").glob("prestock-*.json"))) == 2


def test_partial_or_uncertain_prestock_keeps_a_blocking_receipt(root):
    partial = FakeAdapter(root, prestock_handler(accepted={"copper-plate": 100}))
    with pytest.raises(bt.TrialError, match="inserted"):
        bt.prestock_items(partial, root, items=bt.DEFAULT_PRESTOCK, network="1")
    [(path, receipt)] = bt.prestock_receipts(root)
    assert receipt["status"] == "partial" and receipt["items_inserted_readback"]["copper-plate"] == 100
    path.unlink()
    broken = FakeAdapter(root, prestock_handler(error="boom"))
    with pytest.raises(bt.TrialError, match="uncertain"):
        bt.prestock_items(broken, root, items=bt.DEFAULT_PRESTOCK, network="1")
    assert [r["status"] for _, r in bt.prestock_receipts(root)] == ["uncertain"]
    with pytest.raises(bt.TrialError, match="already exists"):
        bt.prestock_items(broken, root, items=bt.DEFAULT_PRESTOCK, network="1")


def test_missing_storage_chest_is_a_clean_no_insert_rejection(root):
    adapter = FakeAdapter(root, prestock_handler(error="expected exactly one storage-chest in network 1 found 0"))
    with pytest.raises(bt.TrialError, match="rejected_no_insert"):
        bt.prestock_items(adapter, root, items=bt.DEFAULT_PRESTOCK, network="1")
    assert bt.trial_kind(root)[0] == "fresh_start"
    adapter.bridge.handler = prestock_handler()
    bt.prestock_items(adapter, root, items=bt.DEFAULT_PRESTOCK, network="1")  # nothing was granted, so no flag needed


def test_item_list_validation():
    assert bt.parse_items(["transport-belt=5", "copper-plate=9"]) == {"transport-belt": 5, "copper-plate": 9}
    for bad in (["x"], ["a=0"], ["a=b"], ["A=1"], ["a=1", "a=2"]):
        with pytest.raises(bt.TrialError):
            bt.parse_items(bad)


def stop_after_survey(monkeypatch):
    def stop(*_args, **_kwargs):
        raise bt.TrialError("stop after prestock decision")
    monkeypatch.setattr(bt, "capture_catalogs", stop)


def test_without_prestock_nothing_is_granted_and_the_trial_is_fresh_start(root, monkeypatch):
    stop_after_survey(monkeypatch)
    adapter = FakeAdapter(root, prestock_handler())
    code, parts = bt.run_trial(args(), adapter=adapter)
    report = json.loads(Path(parts["report_path"]).read_text())
    assert code == 2 and report["trial_kind"] == "fresh_start" and report["prestock_receipt"] is None
    assert report["prestock"] == {"this_run": False, "items_requested": {},
                                  "note": "no items were granted by this run"}
    assert not any("storage-chest" in c for c in adapter.bridge.commands)
    assert not list((root / "evidence").glob("prestock-*.json"))
    assert report["submitted"] is False and report["outcome"]["status"] == "failed"
    assert report["evidence_level"].startswith("integration only")


def test_prestock_flag_grants_once_and_labels_the_report(root, monkeypatch):
    stop_after_survey(monkeypatch)
    adapter = FakeAdapter(root, prestock_handler())
    code, parts = bt.run_trial(args(prestock=True), adapter=adapter)
    report = json.loads(Path(parts["report_path"]).read_text())
    assert code == 2 and report["trial_kind"] == "block_test_prestocked"
    assert report["prestock"]["this_run"] is True
    assert report["prestock"]["items_inserted_readback"] == bt.DEFAULT_PRESTOCK
    assert Path(report["prestock_receipt"]).name.startswith("prestock-")
    assert sum("storage-chest" in c for c in adapter.bridge.commands) == 1
    # A later run without the flag grants nothing but is still honestly labelled prestocked.
    again = FakeAdapter(root, prestock_handler())
    _, later = bt.run_trial(args(), adapter=again)
    assert json.loads(Path(later["report_path"]).read_text())["trial_kind"] == "block_test_prestocked"
    assert not any("storage-chest" in c for c in again.bridge.commands)


# --------------------------------------------------------------------------- site and power

def test_origins_stay_inside_the_logistic_area_with_margin_nearest_first():
    template = bt.block_template()
    origins = bt.origin_candidates(template, PORT, margin=2)
    x_lo, x_hi, y_lo, y_hi = bt.logistic_window(PORT, 2)
    assert (x_lo, x_hi, y_lo, y_hi) == (-20, 26, -24, 22)
    for ox, oy in origins:
        assert ox + template.min_tx >= x_lo and ox + template.max_tx + 1 <= x_hi
        assert oy + template.min_ty >= y_lo and oy + template.max_ty + 1 <= y_hi
    cx, cy = template.center
    distances = [((ox + cx - 3) ** 2 + (oy + cy + 1) ** 2) ** .5 for ox, oy in origins]
    assert distances == sorted(distances)
    # A larger margin shrinks the set; an origin on the window edge is excluded.
    assert set(bt.origin_candidates(template, PORT, margin=6)) < set(origins)
    assert (x_hi - template.max_tx - 1 + 1, 0) not in origins


def test_screen_skips_known_entities_and_unpowered_reach():
    template = bt.block_template()
    poles = [pole(*s) for s in SUBSTATIONS]
    roboport = {"name": "roboport", "box": [1.0, -3.0, 5.0, 1.0]}
    blocked = bt.known_tiles([roboport])
    assert blocked[(1, -3)] == blocked[(4, 0)] == "roboport" and (5, 0) not in blocked
    overlapping = bt.screen_origin((0, -2), template, blocked, poles, pole_wire=9.0)
    assert overlapping[0] == "known_entity_overlap:roboport"
    clear = bt.screen_origin((8, -16), template, bt.known_tiles([]), poles, pole_wire=9.0)
    assert clear == []
    far = bt.screen_origin((20, 12), template, bt.known_tiles([]), poles, pole_wire=9.0)
    assert far == ["no_substation_in_wire_reach"]


def test_site_search_returns_first_passing_origin_and_records_every_rejection():
    template = bt.block_template()
    blocked = bt.known_tiles([{"name": "roboport", "box": [1.0, -3.0, 5.0, 1.0]}])
    poles = [pole(*s) for s in SUBSTATIONS]
    origins = bt.origin_candidates(template, PORT, margin=2)
    verified = []
    def attempt(origin, number):
        verified.append(origin)
        return {"reasons": ["collision"] if len(verified) == 1 else []}
    chosen, outcome, rejected, stop = bt.search_sites(
        origins, lambda o: bt.screen_origin(o, template, blocked, poles, pole_wire=9.0), attempt, max_attempts=4)
    assert stop is None and chosen == verified[1] and outcome == {"reasons": []}
    stages = [r["stage"] for r in rejected]
    assert stages.count("verifier") == 1 and {"origin": list(verified[0]), "stage": "verifier",
                                              "reasons": ["collision"]} in rejected
    assert all(r["reasons"] for r in rejected)
    tiles = {(tx + chosen[0], ty + chosen[1]) for tx, ty in template.tiles}
    assert not tiles & set(blocked)
    # Examined origins come out in distance order, so the first screen rejection is nearer than the pick.
    first = tuple(rejected[0]["origin"])
    assert origins.index(first) < origins.index(chosen)


def test_site_search_stops_at_the_attempt_budget():
    chosen, _, rejected, stop = bt.search_sites([(0, 0), (1, 0), (2, 0)], lambda o: [],
                                                lambda o, n: {"reasons": ["collision"]}, max_attempts=2)
    assert chosen is None and stop == "max_site_attempts_reached" and len(rejected) == 2


def test_power_anchor_is_the_nearest_powered_substation_in_reach():
    poles = [pole(8, -8, 1234), pole(-10, -8, 1237), pole(8, 10, 1235),
             pole(9, -9, 1300, generated=False), pole(7, -7, 1301, grid="nil"),
             {**pole(8, -7, 1302), "name": "medium-electric-pole"}, pole(8, -7, 1303, quality="uncommon")]
    del poles[0]["unit_number"]
    poles.append(pole(8, -8, 1234))
    anchors = bt.power_anchors(poles, [(2.5, -9.5), (6.5, -9.5)], pole_wire=9.0)
    assert [a["unit_number"] for a in anchors] == [1234]
    assert anchors[0]["position"] == {"x": 8, "y": -8} and anchors[0]["grid"] == "1"
    assert bt.power_anchors(poles, [(-100, -100)], pole_wire=9.0) == []
    # Reach is the smaller of the two poles' wires, so a short block pole limits the choice.
    assert bt.power_anchors(poles, [(2.5, -9.5), (6.5, -9.5)], pole_wire=1.0) == []
    near_two = [pole(0, 0, 1), pole(3, 0, 2)]
    assert [a["unit_number"] for a in bt.power_anchors(near_two, [(2.0, 0)], pole_wire=9.0)] == [2, 1]


# --------------------------------------------------------------------------- build loop

class BuildAdapter(FakeAdapter):
    def __init__(self, root, built_after=None):
        super().__init__(root)
        self.built_after, self.ticks = built_after, 0

    def advance(self, ticks):
        super().advance(ticks)
        self.ticks += ticks

    def query(self, candidates=None, inspect=None):
        if self.built_after is not None and self.ticks >= self.built_after:
            return {"built": 4, "pending": 0, "missing": 0}
        return {"built": 1, "pending": 3, "missing": 0}


PLAN = {"phases": [{"actions": [{"action_type": "place_ghost", "entity": "inserter", "position": {"x": i + .5, "y": .5}}
                                for i in range(4)]}]}


def test_build_loop_times_out_without_claiming_completion(root):
    adapter = BuildAdapter(root)
    result = bt.drive_construction(adapter, PLAN, {"construction": {"built": 1, "pending": 3, "missing": 0},
                                                   "after": {"tick": 700}}, chunk=600, max_build_ticks=1500, start_tick=100)
    assert result["complete"] is False and result["timed_out"] is True
    assert adapter.advanced == [600, 300]  # the last chunk is clipped to the budget
    assert [t["elapsed_ticks"] for t in result["timeline"]] == [600, 1200, 1500]
    assert result["build_ticks"] == 1500 and result["planned_entities"] == 4
    assert all(t["pending"] == 3 for t in result["timeline"])


def test_build_loop_stops_when_every_planned_entity_is_built(root):
    adapter = BuildAdapter(root, built_after=1200)
    result = bt.drive_construction(adapter, PLAN, {"construction": {"built": 1, "pending": 3, "missing": 0},
                                                   "after": {"tick": 700}}, chunk=600, max_build_ticks=7200, start_tick=100)
    assert result["complete"] is True and adapter.advanced == [600, 600]
    assert result["timeline"][-1] == {"tick": 1900, "elapsed_ticks": 1800, "built": 4, "pending": 0, "missing": 0}
    done = bt.drive_construction(BuildAdapter(root), PLAN, {"construction": {"built": 4, "pending": 0, "missing": 0},
                                                            "after": {"tick": 700}}, chunk=600, max_build_ticks=600,
                                 start_tick=100)
    assert done["complete"] is True and done["timeline"][0]["built"] == 4
    # Built entities without matching counts (e.g. a ghost was lost) never count as complete.
    short = bt.drive_construction(BuildAdapter(root), PLAN, {"construction": {"built": 3, "pending": 0, "missing": 1},
                                                            "after": {"tick": 700}}, chunk=600, max_build_ticks=600,
                                  start_tick=100)
    assert short["complete"] is False


# --------------------------------------------------------------------------- measurement and report

def state(tick, *, status="working", copper=10, gears=10, requests=None):
    requests = requests if requests is not None else [{"name": "copper-plate", "count": 24},
                                                     {"name": "iron-gear-wheel", "count": 24}]
    return {"tick": tick, "rows": [
        {"name": "assembling-machine-1", "x": 1.5, "y": 3.5, "found": True, "status": status},
        {"name": "requester-chest", "x": -1.5, "y": -1.5, "found": True, "status": "normal",
         "contents": {"copper-plate": copper, "iron-gear-wheel": gears}, "requests": requests}]}


def test_classification_distinguishes_power_starvation_and_healthy_blocks():
    assert bt.classify_limit([state(1)], 1.0, .9)["limited_by"] == "none"
    assert bt.classify_limit([state(1, status="no_power")], 0.0, .9)["limited_by"] == "power"
    starved = bt.classify_limit([state(1, status="no_ingredients", copper=0)], .3, .9)
    assert starved["limited_by"] == "starvation" and starved["detail"]["empty_requester_samples"] == {"copper-plate": 1}
    assert bt.classify_limit([state(1, status="full_output")], .3, .9)["limited_by"] == "output_blocked"
    assert bt.classify_limit([state(1)], .3, .9)["limited_by"] == "unexplained"
    missing = state(1)
    missing["rows"][0]["found"] = False
    assert bt.classify_limit([missing], 0, .9)["limited_by"] == "entities_missing"


def test_requester_configuration_is_compared_with_the_plan():
    plan = {"phases": [{"actions": [
        {"action_type": "place_ghost", "entity": "requester-chest", "position": {"x": -1.5, "y": -1.5},
         "logistic_requests": [{"name": "copper-plate", "count": 24}]}]}]}
    good = {"rows": [{"name": "requester-chest", "x": -1.5, "y": -1.5, "requests": [{"name": "copper-plate", "count": 24}]}]}
    assert bt.confirm_requests(plan, good)["all_match"] is True
    wrong = bt.confirm_requests(plan, {"rows": [{"name": "requester-chest", "x": -1.5, "y": -1.5, "requests": []}]})
    assert wrong["mismatch"] is True and wrong["all_match"] is False
    # An unreadable chest is unknown: neither confirmed nor called misconfigured.
    for rows in ([{"name": "requester-chest", "x": -1.5, "y": -1.5, "requests_error": "no api"}], []):
        unknown = bt.confirm_requests(plan, {"rows": rows})
        assert unknown["unreadable"] is True and unknown["mismatch"] is False and unknown["all_match"] is False


def test_measurement_reports_ratio_and_samples(root):
    before, after = world_facts(tick=1000), world_facts(tick=4600)
    after["production"][bt.PACK] = 12
    after["consumption"].update({"copper-plate": 12, "iron-gear-wheel": 12})
    rows = iter([state(1000)] + [state(1000 + 600 * i) for i in range(1, 7)])
    facts = iter([before, after])
    adapter = FakeAdapter(root, lambda text: next(rows))
    adapter.query = lambda candidates=None, inspect=None: next(facts)
    plan = {"phases": [{"actions": [{"action_type": "place_ghost", "entity": "assembling-machine-1",
                                     "position": {"x": 1.5, "y": 3.5}}]}]}
    result = bt.measure_block(adapter, plan, warmup_ticks=300, measure_ticks=3600, min_ratio=.9)
    assert adapter.advanced == [300] + [600] * 6
    assert result["status"] == "measured" and result["window_ticks"] == 3600 and result["packs_produced"] == 12
    assert result["measured_per_second"] == pytest.approx(0.2) and result["ratio"] == pytest.approx(1.0)
    assert result["expected_per_second"] == 0.2 and len(result["samples"]) == 7
    assert result["limit"]["limited_by"] == "none"
    assert result["consumption_per_second"] == {"copper-plate": pytest.approx(0.2), "iron-gear-wheel": pytest.approx(0.2)}


def test_report_never_claims_success_without_a_measurement(root):
    parts = dict(report_path="x", stage="constructed")
    base = bt.build_report(args=args(), root=root, trial=("fresh_start", None), **parts)
    assert base["outcome"] == {"status": "incomplete", "meets_expected": None, "min_ratio": .9,
                               "note": "no success is claimed without a measurement"}
    assert base["evidence_level"].startswith("integration only")
    built = bt.build_report(args=args(), root=root, trial=("fresh_start", None), submitted=True,
                            construction={"complete": True}, **parts)
    assert built["evidence_level"] == "deployed runtime (construction observed; throughput not measured)"
    assert built["outcome"]["meets_expected"] is None
    waiting = bt.build_report(args=args(), root=root, trial=("fresh_start", None), submitted=True,
                              construction={"complete": False}, failure={"stage": "x"}, **parts)
    assert waiting["evidence_level"] == "deployed runtime (ghosts submitted; construction not completed)"
    assert waiting["outcome"]["status"] == "failed"
    measured = bt.build_report(args=args(), root=root, trial=("block_test_prestocked", "/r"), submitted=True,
                               construction={"complete": True}, measurement={"status": "measured", "ratio": .5},
                               **parts)
    assert measured["evidence_level"].startswith("deployed runtime / observed live result")
    assert measured["outcome"]["status"] == "measured" and measured["outcome"]["meets_expected"] is False
    assert {"trial_kind", "commit", "root", "prestock_receipt", "evidence_level", "target"} <= set(measured)
    assert set(measured["commit"]) == {"head", "dirty"}


# --------------------------------------------------------------------------- the candidate chain

def synthetic_runtime_catalog():
    entities = [entity(name, kind, size, []) for name, kind, size in (
        ("assembling-machine-1", "assembling-machine", (3, 3)), ("inserter", "inserter", (1, 1)),
        ("transport-belt", "transport-belt", (1, 1)), ("requester-chest", "logistic-container", (1, 1)),
        ("passive-provider-chest", "logistic-container", (1, 1)), ("medium-electric-pole", "electric-pole", (1, 1)),
        ("substation", "electric-pole", (2, 2)))]
    for record in entities:
        if record["type"] == "electric-pole":
            wire, supply = (9, 3.5) if record["name"] == "medium-electric-pole" else (18, 9)
            record["electric_pole"] = {"quality": "normal", "max_wire_distance": wire, "supply_area_distance": supply}
        if record["type"] in {"assembling-machine", "inserter"}:
            record["energy"] = {"source": "electric"}
    report = json.loads(catalog_bytes(recipes=False))
    report["entities"] = entities
    report["provenance"]["scope"]["entity_types"] = sorted({e["type"] for e in entities})
    report["force_state"] = {"name": "player", "research_progress": 0, "research_queue": [],
                             "research_enabled": True, "bonuses": {}}
    return json.dumps(report, sort_keys=True).encode()


@pytest.fixture
def chain(tmp_path, monkeypatch):
    if not _FIXTURE.is_file():
        pytest.skip("local historical recipe export is unavailable")
    root = tmp_path.resolve()
    manifest = b'{"runtime":"disposable_saved_world","production_authority":false}'
    (root / "manifest.json").write_bytes(manifest)
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = root
    monkeypatch.setattr(integration, "owned_root", lambda path: path.resolve())
    monkeypatch.setattr(integration, "verify_instance", lambda _root, **_kwargs: {"runtime": "disposable_saved_world"})
    recipe_bytes, runtime_bytes = _FIXTURE.read_bytes(), synthetic_runtime_catalog()
    catalogs = {"source": "fixture", "tick": 50, "mods": dict(MODS), "runtime_bytes": runtime_bytes,
                "runtime_sha256": hashlib.sha256(runtime_bytes).hexdigest(), "recipe_bytes": recipe_bytes,
                "recipe_provenance": _provenance(recipe_bytes, json.loads(recipe_bytes)), "game_version": "2.1.21",
                "game_version_basis": "test"}
    recipe_catalog, demand = bt.plan_demand(catalogs)
    ctx = bt.Context(root=root, scope=ConstructionScope(str(root), hashlib.sha256(manifest).hexdigest(), "nauvis", "player"),
                     catalogs=catalogs, placement=bt.placement_capture(catalogs), recipe_catalog=recipe_catalog,
                     demand=demand, pole_wire=bt.pole_reach(runtime_bytes), template=bt.block_template(),
                     port=PORT, network_anchor={"x": 3, "y": -1}, network_id="1", base_episode="trial")
    return adapter, ctx


def test_chain_binds_power_attachment_and_holds_the_exact_bill(chain):
    adapter, ctx = chain
    seen = {}
    def options(facts, seed=0, *, additional_candidates=(), check_verifier=None):
        seen.update(facts=facts, offered=list(additional_candidates), verifier=check_verifier)
        legal = [{**c, "check_evidence": {"fluid_purity": {"fluid_entities": 0},
                                          "safe_infrastructure_attachment": {"anchor_unit": 1234}}}
                 for c in additional_candidates]
        return legal, {"proposed": legal, "validations": {}}
    adapter.options = options
    facts = world_facts()
    outcome = bt.attempt_site(adapter, ctx, facts, (8, -16), 1)
    assert outcome["reasons"] == [] and outcome["episode"] == "trial-s1"
    bound = outcome["bound"]
    assert dict(bound.plan.required_materials) == {
        "assembling-machine-1": 2, "inserter": 7, "medium-electric-pole": 4, "passive-provider-chest": 1,
        "requester-chest": 2, "transport-belt": 14}
    attachment = bound.artifact["planning_context"]["data"]["power_attachment"]
    assert attachment["anchor"]["unit_number"] == 1234 and attachment["anchor"]["entity"] == "substation"
    assert outcome["anchor"]["unit_number"] == 1234
    project = outcome["ledger"].projects["optimizer:" + bound.plan.binding.project_id]
    assert project.state == "ready" and project.reserved == project.required
    assert outcome["ledger"].path.parent == ctx.root / "server/logs/deterministic-material-reservations"
    # Only the one external candidate is evaluated, with the bound verifier.
    assert seen["facts"]["ports"] == [] and len(seen["offered"]) == 1
    assert isinstance(seen["verifier"], integration.OptimizerCheckVerifier)
    assert outcome["candidate"]["option_id"] == seen["offered"][0]["option_id"]
    assert outcome["check_evidence"]["safe_infrastructure_attachment"] == {"anchor_unit": 1234}
    assert {a["entity"] for a in bt.actions(bound.artifact["plan"])} >= {"requester-chest", "passive-provider-chest"}


def test_chain_reports_verifier_reasons_and_unreachable_power(chain):
    adapter, ctx = chain
    def reject(facts, seed=0, *, additional_candidates=(), check_verifier=None):
        option = additional_candidates[0]["option_id"]
        return [], {"validations": {option: {"reason": "collision", "required_check_reason": "attachment_collision"}}}
    adapter.options = reject
    outcome = bt.attempt_site(adapter, ctx, world_facts(), (8, -16), 1)
    assert outcome["reasons"] == ["collision", "attachment_collision"]
    far = bt.attempt_site(adapter, ctx, world_facts(), (20, 12), 2)
    assert far["reasons"][0].startswith("power_attachment_failed:")


def test_unfunded_bill_stops_the_run_and_names_the_shortage(chain):
    adapter, ctx = chain
    adapter.options = lambda *a, **k: pytest.fail("a shortfall must stop before any option is offered")
    no_belts = {k: v for k, v in STOCK.items() if k != "transport-belt"}
    with pytest.raises(bt.TrialError, match=r"transport-belt.*--prestock"):
        bt.attempt_site(adapter, ctx, world_facts(items=no_belts), (8, -16), 1)
    assert Fraction(1, 5) == bt.TARGET_RATE


# --------------------------------------------------------------------------- catalog capture and Lua

def capture_adapter(root, *, recipe_tick=100):
    adapter = FakeAdapter(root)
    adapter.bridge.export_runtime_catalog = lambda surface, force: catalog_bytes(tick=100)
    adapter.bridge.export_recipe_catalog_capture = lambda force: json.dumps(
        {"version": "1.3.0", "tick": recipe_tick, "force": force, "active_mods": dict(MODS)}).encode()
    return adapter


def test_capture_pins_both_catalogs_to_the_paused_tick(root):
    captured = bt.capture_catalogs(capture_adapter(root))
    assert captured["source"] == "live_capture" and captured["tick"] == 100
    assert captured["game_version"] == "2.1.21" and captured["game_version_basis"] == "base mod version"
    assert captured["recipe_provenance"]["export_tick"] == 100
    assert captured["recipe_provenance"]["catalog_sha256"] == hashlib.sha256(captured["recipe_bytes"]).hexdigest()
    with pytest.raises(bt.TrialError, match="does not match the paused world"):
        bt.capture_catalogs(capture_adapter(root, recipe_tick=99))
    with pytest.raises(bt.TrialError, match="game version"):
        bt.capture_catalogs(capture_adapter(root), game_version="two")


def test_lua_sources_parse_and_run_against_engine_stubs():
    lupa = pytest.importorskip("lupa")
    for source in (bt._ENTITIES_LUA, bt._PRESTOCK_LUA, bt._BLOCK_STATE_LUA):
        assert "--" not in bt._PRELUDE + source  # the RCON line is comment-stripped, so none may appear
        runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
        loaded = runtime.eval("load")("local request,defines,game={},{},{}; " + bt._PRELUDE + source)
        assert not isinstance(loaded, tuple), loaded  # load returns (nil, message) on a syntax error
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute("""
        defines={inventory={chest=1},entity_status={working=1}}
        local items={}
        local inv={get_contents=function() local r={} for k,v in pairs(items) do r[k]=v end return r end,
                   insert=function(stack) items[stack.name]=(items[stack.name] or 0)+stack.count return stack.count end}
        local chest={position={x=1.5,y=2.5},unit_number=9,get_inventory=function() return inv end}
        local surface={find_entities_filtered=function(f) if f.name=="storage-chest" then return {chest} end return {} end,
                       find_logistic_network_by_position=function() return {network_id=1} end}
        game={tick=7,tick_paused=true,ticks_to_run=0,surfaces={nauvis=surface},forces={player={}}}
        request={network="1",items={{name="copper-plate",count=5}}}
    """)
    body = bt._PRELUDE + bt._PRESTOCK_LUA
    result = runtime.eval("function() " + body + " end")()
    assert result.inserted_reported["copper-plate"] == 5 and result.after["copper-plate"] == 5
    assert result.tick == 7 and result.unit_number == 9
    runtime.execute('request.network="2"')
    ok, err = runtime.eval("pcall")(runtime.eval("function() " + body + " end"))
    assert ok is False and "expected exactly one storage-chest" in err
