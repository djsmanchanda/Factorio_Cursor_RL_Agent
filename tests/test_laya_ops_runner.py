# Path: tests/test_laya_ops_runner.py | Purpose: Protect Laya's loop over deterministic operations: options from the board and results, doc-rule choices, recorded outcomes.

from training.laya_guided_policy import GuidedPolicy
from training.laya_ops_runner import compile_options, plate_stages, run, shortages

BOARD = {"needs": [{"item": "iron-plate", "condition": "unbuilt", "goal_required_per_s": 0.5},
                   {"item": "copper-plate", "condition": "unbuilt", "goal_required_per_s": 0.5}]}
SHORT = {"ok": False, "operation": "foundation", "params": {"recipe": "iron-plate"}, "code": "material_shortage",
         "details": {"required": {"transport-belt": 126, "inserter": 4}, "available": {"inserter": 18}}}


def test_results_become_plate_stages_and_shortages():
    results = [{"ok": True, "operation": "starter", "params": {"recipe": "iron-plate"}}, SHORT]
    assert plate_stages(results) == {"iron-plate": "starter"}
    assert shortages(results) == {"transport-belt": 126}  # inserters are in stock
    menu = compile_options(BOARD, results + [{"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}])
    ids = {o["option_id"] for o in menu["options"]}
    assert {"op.foundation.iron-plate", "op.starter.copper-plate", "op.produce.transport-belt", "wait.300"} <= ids
    # The tooling's lifecycle is a hard order: no foundation before a starter, no starter twice.
    assert "op.starter.iron-plate" not in ids and "op.foundation.copper-plate" not in ids


def test_the_docs_order_fence_starter_foundation_then_shortage():
    from orchestrator.laya_knowledge import load_guideline_context
    from training.laya_clean_runner import ROOT
    from training.laya_mission import PHASES
    from training.laya_ops_runner import OPS_CATALOG
    state = {"observation_digest": {"factory_status": {"needs": [{"item": "iron-plate", "required": 0.5}]}},
             "guidelines": load_guideline_context(ROOT, PHASES[0]["scopes"], catalog=OPS_CATALOG, max_rules=32)["guidelines"]}
    policy = GuidedPolicy(1)

    def best(results):
        rows = policy.distribution(state, compile_options(BOARD, results))
        return max(rows, key=lambda r: r["probability"])["option_id"]
    assert best([]) == "op.reserve_steel_seed"  # poles first: nothing can make them before steel
    fenced = [{"ok": True, "operation": "reserve_steel_seed", "params": {}}]
    assert best(fenced).startswith("op.starter.")
    started = fenced + [{"ok": True, "operation": "starter", "params": {"recipe": p}} for p in ("iron-plate", "copper-plate")]
    assert best(started).startswith("op.foundation.")
    slot = [{"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}]
    assert best(started + [SHORT] + slot) == "op.produce.transport-belt"

class FakeWorld:
    root = None

    def __init__(self):
        self.tick = 1000

    def query(self):
        return {"tick": self.tick, "production": {}, "consumption": {}, "machines": [], "networks": {}, "ports": [],
                "resources": [], "recipes": {}}

    def advance(self, ticks):
        self.tick += ticks


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, operation, **params):
        self.calls.append((operation, params))
        if operation == "foundation":
            return dict(SHORT, params=params)
        return {"ok": True, "operation": operation, "params": params}

    def close(self):
        pass


def test_loop_runs_operations_and_records_each_outcome(tmp_path):
    import json
    session = FakeSession()
    summary = run(FakeWorld(), GuidedPolicy(3), journal=tmp_path / "journal.jsonl", decisions=6, measure_ticks=600,
                  seed=3, run_id="t", session=session)
    assert summary["decisions"] == 6 and session.calls
    rows = [json.loads(l) for l in (tmp_path / "decision-states.jsonl").read_text().splitlines()]
    assert len(rows) == 6 and all("outcome" in r and "distribution" in r for r in rows)


def test_mall_slots_wait_for_their_plates_and_borrowing_for_slots():
    recipes = {"iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}]},
               "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}]},
               "iron-gear-wheel": {"ingredients": [{"name": "iron-plate", "amount": 2}]},
               "copper-cable": {"ingredients": [{"name": "copper-plate", "amount": 1}]},
               "electronic-circuit": {"ingredients": [{"name": "iron-plate", "amount": 1}, {"name": "copper-cable", "amount": 3}]},
               "transport-belt": {"ingredients": [{"name": "iron-gear-wheel", "amount": 1}, {"name": "iron-plate", "amount": 1}]}}
    iron = [{"ok": True, "operation": "starter", "params": {"recipe": "iron-plate"}}, SHORT]
    ids = {o["option_id"] for o in compile_options(BOARD, iron, recipes=recipes)["options"]}
    assert {"op.mall_slot.iron-gear-wheel", "op.mall_slot.transport-belt"} <= ids
    assert "op.mall_slot.copper-cable" not in ids and "op.mall_slot.electronic-circuit" not in ids  # no copper yet
    assert not any(i.startswith("op.produce.") for i in ids)  # nothing to borrow from yet
    gear = next(o for o in compile_options(BOARD, iron, recipes=recipes)["options"] if o["option_id"] == "op.mall_slot.iron-gear-wheel")
    assert gear["features"]["goal_need"]  # the iron foundation is blocked on materials: the mall serves the goal


def test_no_produce_for_items_whose_plate_has_no_starter_yet():
    recipes = {"iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}]},
               "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}]},
               "splitter": {"ingredients": [{"name": "electronic-circuit", "amount": 5}, {"name": "iron-plate", "amount": 5}]},
               "electronic-circuit": {"ingredients": [{"name": "iron-plate", "amount": 1}, {"name": "copper-cable", "amount": 3}]},
               "copper-cable": {"ingredients": [{"name": "copper-plate", "amount": 1}]},
               "transport-belt": {"ingredients": [{"name": "iron-plate", "amount": 1}]}}
    short = dict(SHORT, details={"required": {"splitter": 3, "transport-belt": 10}, "available": {}})
    results = [{"ok": True, "operation": "starter", "params": {"recipe": "iron-plate"}}, short,
               {"ok": True, "operation": "mall_slot", "params": {"item": "transport-belt"}}]
    ids = {o["option_id"] for o in compile_options(BOARD, results, recipes=recipes)["options"]}
    assert "op.produce.transport-belt" in ids and "op.produce.splitter" not in ids  # splitter needs copper


def test_mall_side_shortages_are_kept_per_item_and_loans_count_as_progress():
    from training.laya_ops_runner import in_progress
    results = [{"ok": False, "operation": "mall_slot", "params": {"item": "electronic-circuit"}, "code": "material_shortage",
                "details": {"required": {"assembling-machine-1": 2}, "available": {}}},
               {"ok": False, "operation": "produce", "params": {"item": "inserter"}, "code": "material_shortage",
                "details": {"required": {"iron-gear-wheel": 5}, "available": {}}}]
    assert shortages(results) == {"assembling-machine-1": 2, "iron-gear-wheel": 5}
    assert in_progress("production_prerequisite_deferred") and not in_progress("material_shortage")


def test_retire_starter_is_offered_after_the_foundation_until_released():
    base = [{"ok": True, "operation": "starter", "params": {"recipe": "iron-plate"}},
            {"ok": True, "operation": "foundation", "params": {"recipe": "iron-plate"}}]
    ids = {o["option_id"] for o in compile_options(BOARD, base)["options"]}
    assert {"op.extend.iron-plate", "op.retire_starter.iron-plate"} <= ids
    released = base + [{"ok": True, "operation": "retire_starter", "params": {"recipe": "iron-plate"},
                        "result": {"lifecycle": "released"}}]
    assert "op.retire_starter.iron-plate" not in {o["option_id"] for o in compile_options(BOARD, released)["options"]}


def test_no_produce_for_items_nothing_can_make_yet():
    recipes = {"requester-chest": {"ingredients": [{"name": "electronic-circuit", "amount": 3},
                                                   {"name": "advanced-circuit", "amount": 1}]},
               "advanced-circuit": {"ingredients": [{"name": "plastic-bar", "amount": 2}, {"name": "copper-cable", "amount": 4}]},
               "plastic-bar": {"ingredients": [{"name": "petroleum-gas", "amount": 20}, {"name": "coal", "amount": 1}]},
               "electronic-circuit": {"ingredients": [{"name": "iron-plate", "amount": 1}, {"name": "copper-cable", "amount": 3}]},
               "copper-cable": {"ingredients": [{"name": "copper-plate", "amount": 1}]},
               "iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}]},
               "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}]}}
    short = dict(SHORT, details={"required": {"requester-chest": 2, "electronic-circuit": 5}, "available": {}})
    results = [{"ok": True, "operation": "starter", "params": {"recipe": p}} for p in ("iron-plate", "copper-plate")]
    results += [short, {"ok": True, "operation": "mall_slot", "params": {"item": "copper-cable"}}]
    ids = {o["option_id"] for o in compile_options(BOARD, results, recipes=recipes)["options"]}
    assert "op.produce.electronic-circuit" in ids and "op.produce.requester-chest" not in ids


def test_failed_operations_are_not_retried_until_their_materials_change():
    from training.laya_ops_runner import unchanged_failures
    failed = dict(SHORT, stock_at={"transport-belt": 4, "inserter": 18})
    assert unchanged_failures([failed], {"transport-belt": 4, "inserter": 18}) == {("foundation", "iron-plate")}
    assert unchanged_failures([failed], {"transport-belt": 30, "inserter": 18}) == set()  # belts were made


def test_a_running_mall_batch_is_left_to_finish():
    results = [{"ok": True, "operation": "starter", "params": {"recipe": "iron-plate"}}, SHORT,
               {"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}},
               {"ok": False, "operation": "produce", "params": {"item": "transport-belt"}, "code": "temporary_mall_batch"}]
    options = compile_options(BOARD, results)["options"]
    assert all(o["features"].get("batch_active") for o in options if o["family"] in ("produce", "mall_slot"))
    assert next(o for o in options if o["option_id"] == "wait.1800")["features"]["constructing"]
