# Path: tests/test_laya_ops_runner.py | Purpose: Protect Laya's loop over deterministic operations: options from the board and results, doc-rule choices, recorded outcomes.

import json

from training.laya_guided_policy import GuidedPolicy
from training.laya_ops_runner import compile_options, plate_stages, run, shortages

BOARD = {"needs": [{"item": "iron-plate", "condition": "unbuilt", "goal_required_per_s": 0.5},
                   {"item": "copper-plate", "condition": "unbuilt", "goal_required_per_s": 0.5}]}
SHORT = {"ok": False, "operation": "foundation", "params": {"recipe": "iron-plate"}, "code": "material_shortage",
         "details": {"required": {"transport-belt": 126, "inserter": 4}, "available": {"inserter": 18}}}


def test_results_become_plate_stages_and_shortages():
    results = [{"ok": True, "operation": "starter", "params": {"recipe": "iron-plate"}}, SHORT]
    assert plate_stages(results) == {"iron-plate": "starter"}
    # No foundation while the copper starter is missing (a placed foundation would reserve its drills).
    assert "op.foundation.iron-plate" not in {o["option_id"] for o in compile_options(BOARD, results)["options"]}
    results.append({"ok": True, "operation": "starter", "params": {"recipe": "copper-plate"}})
    assert shortages(results) == {"transport-belt": 126}  # inserters are in stock
    menu = compile_options(BOARD, results + [{"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}])
    ids = {o["option_id"] for o in menu["options"]}
    assert {"op.foundation.iron-plate", "op.foundation.copper-plate", "op.produce.transport-belt", "wait.300"} <= ids
    # The tooling's lifecycle is a hard order: no starter twice.
    assert "op.starter.iron-plate" not in ids


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


def test_accepted_window_does_not_stop_at_a_rate_proxy(tmp_path, monkeypatch):
    from training import laya_ops_runner as ops
    checks = iter([True, True, True, False])
    monkeypatch.setattr(ops, "ops_phase_reached", lambda *_: next(checks, False))

    class UnacceptedSession(FakeSession):
        def acceptance(self):
            return {"C2": "failed"}

    summary = run(FakeWorld(), GuidedPolicy(3), journal=tmp_path / "journal.jsonl", decisions=1,
                  measure_ticks=600, seed=3, run_id="t", session=UnacceptedSession(), until_accepted="C2")
    assert "C2" in summary["phases_reached"]
    assert summary["decisions"] == 1 and summary["stop"] == "decision_budget"
    assert "C2" not in summary["accepted"]


def test_accepted_window_stops_after_a_verified_checkpoint(tmp_path):
    class AcceptedSession(FakeSession):
        checks = 0

        def acceptance(self):
            self.checks += 1
            return {"C2": "passed" if self.checks > 1 else "failed"}

    summary = run(FakeWorld(), GuidedPolicy(3), journal=tmp_path / "journal.jsonl", decisions=12,
                  measure_ticks=600, seed=3, run_id="t", session=AcceptedSession(), until_accepted="C2")
    assert summary["decisions"] == 10
    assert summary["stop"] == "accepted_window_reached" and summary["accepted"] == ["C2"]


def test_acceptance_details_are_indexed_without_changing_operation_choices(tmp_path):
    import json
    from pathlib import Path
    from orchestrator.checkpoint_milestones import CheckpointEvaluator, FactorySnapshot
    from orchestrator.checkpoint_observer import StructuredObservation
    from training.laya_acceptance import acceptance_packet
    raw = {"tick": 120, "tick_end": 122, "base_valid": True}
    obs = StructuredObservation(FactorySnapshot.from_mapping(raw), "fixture", "fixture", raw)

    class DiagnosticSession(FakeSession):
        def acceptance(self):
            self.acceptance_details = acceptance_packet(obs, CheckpointEvaluator(), baseline=obs.snapshot,
                                                       history=[], scope={"episode_id": "test"})
            return self.acceptance_details["statuses"]

    plain, diagnostic = FakeSession(), DiagnosticSession()
    for name, s in (("plain", plain), ("diagnostic", diagnostic)):
        (tmp_path / name).mkdir()
        summary = run(FakeWorld(), GuidedPolicy(3), journal=tmp_path / name / "journal.jsonl", decisions=6,
                      measure_ticks=600, seed=3, run_id=name, session=s)
    assert plain.calls == diagnostic.calls
    path = Path(summary["acceptance_details"]["path"])
    saved = json.loads(path.read_text())
    assert saved["at"] == "end" and saved["results"]["C1"]["status"] == "unknown"
    assert summary["acceptance_details"]["results"]["C1"]["checks"]["stone_starter_ready"] is None
    rows = [json.loads(line) for line in (tmp_path / "diagnostic/journal.jsonl").read_text().splitlines()]
    assert all(Path(r["details_path"]).is_file() for r in rows if r.get("event") == "acceptance")


def test_mall_slots_wait_for_their_plates_and_borrowing_for_slots():
    recipes = {"iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}]},
               "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}]},
               "iron-gear-wheel": {"ingredients": [{"name": "iron-plate", "amount": 2}]},
               "copper-cable": {"ingredients": [{"name": "copper-plate", "amount": 1}]},
               "electronic-circuit": {"ingredients": [{"name": "iron-plate", "amount": 1}, {"name": "copper-cable", "amount": 3}]},
               "transport-belt": {"ingredients": [{"name": "iron-gear-wheel", "amount": 1}, {"name": "iron-plate", "amount": 1}]}}
    iron = [{"ok": True, "operation": "starter", "params": {"recipe": "iron-plate"}}, SHORT]  # copper not yet
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


def test_produce_asks_for_a_network_level_not_the_bare_shortage():
    # Live 2026-10-15: the stone starter needed 8 drills, the network held 6; asking for 2 gated the cell shut.
    starter = {"ok": False, "operation": "starter", "params": {"recipe": "stone-brick"}, "code": "material_shortage",
               "details": {"required": {"electric-mining-drill": 8}, "available": {"electric-mining-drill": 6}}}
    results = [{"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}, starter]
    options = compile_options(BOARD, results, stock={"electric-mining-drill": 6})["options"]
    drill = next(o for o in options if o["option_id"] == "op.produce.electric-mining-drill")
    assert drill["params"]["stock_target"] == 8 and drill["features"]["shortage"] == 2


def test_mall_side_shortages_are_kept_per_item_and_loans_count_as_progress():
    from training.laya_ops_runner import in_progress
    results = [{"ok": False, "operation": "mall_slot", "params": {"item": "electronic-circuit"}, "code": "material_shortage",
                "details": {"required": {"assembling-machine-1": 2}, "available": {}}},
               {"ok": False, "operation": "produce", "params": {"item": "inserter"}, "code": "material_shortage",
                "details": {"required": {"iron-gear-wheel": 5}, "available": {}}}]
    assert shortages(results) == {"assembling-machine-1": 2, "iron-gear-wheel": 5}
    assert in_progress("production_prerequisite_deferred") and not in_progress("material_shortage")


def test_retire_starter_is_offered_after_the_foundation_until_released():
    base = [{"ok": True, "operation": "starter", "params": {"recipe": p}} for p in ("iron-plate", "copper-plate")]
    base.append({"ok": True, "operation": "foundation", "params": {"recipe": "iron-plate"}})
    ids = {o["option_id"] for o in compile_options(BOARD, base)["options"]}
    assert {"op.extend.iron-plate", "op.retire_starter.iron-plate"} <= ids
    from training.laya_ops import receipt
    # Through the real receipt: a stringified result once crashed the next menu (live 2026-10-15).
    released = base + [receipt("retire_starter", {"recipe": "iron-plate"}, {"lifecycle": "released", "submitted": True})]
    assert "op.retire_starter.iron-plate" not in {o["option_id"] for o in compile_options(BOARD, released)["options"]}


def test_real_receipt_preserves_retirement_mapping_and_compiles_next_menu():
    from training.laya_ops import receipt
    from training.laya_ops_runner import retired
    result = receipt("retire_starter", {"recipe": "iron-plate"}, {"lifecycle": "released", "recipe": "iron-plate"})
    assert isinstance(result["result"], dict)
    assert result["ok"] and result["code"] == "released"
    assert retired([result], "iron-plate")
    base = [{"ok": True, "operation": "foundation", "params": {"recipe": "iron-plate"}}, result]
    assert "op.retire_starter.iron-plate" not in {o["option_id"] for o in compile_options(BOARD, base)["options"]}


def test_legacy_string_receipt_is_unverified_retirement_without_crashing():
    from training.laya_ops_runner import retired
    legacy = {"ok": True, "operation": "retire_starter", "params": {"recipe": "iron-plate"},
              "result": "{'lifecycle': 'released'}"}
    assert retired([legacy], "iron-plate") is False


def test_retirement_survives_satisfied_rates_and_later_phase_without_new_capacity():
    from training.laya_ops import receipt
    base = [{"ok": True, "operation": "foundation", "params": {"recipe": "iron-plate"}}]
    boards = [{"needs": [{"item": "iron-plate", "condition": "satisfied", "goal_required_per_s": 3}]},
              {"needs": [{"item": "stone-brick", "condition": "short", "goal_required_per_s": 1}]}]
    for board in boards:
        options = {o["option_id"]: o for o in compile_options(board, base)["options"]}
        option = options["op.retire_starter.iron-plate"]
        assert option["features"]["lifecycle_pending"] and option["features"]["goal_need"]
        assert option["need_id"] == "lifecycle:iron-plate"
        assert "op.extend.iron-plate" not in options
        for lifecycle in ("validating", "retiring", None):
            unverified = receipt("retire_starter", {"recipe": "iron-plate"}, {"lifecycle": lifecycle})
            assert not unverified["ok"] and unverified["code"] == "retirement_pending"
            assert "op.retire_starter.iron-plate" in {o["option_id"] for o in compile_options(board, base + [unverified])["options"]}
        released = receipt("retire_starter", {"recipe": "iron-plate"}, {"lifecycle": "released"})
        assert "op.retire_starter.iron-plate" not in {o["option_id"] for o in compile_options(board, base + [released])["options"]}


def test_scoped_submission_recovers_retirement_after_shortage_without_claiming_ready():
    from training.laya_ops_runner import pending_retirements
    results = [{"ok": True, "operation": "starter", "params": {"recipe": "iron-plate"}}, SHORT]
    board = {"needs": []}  # A later proxy phase need not mention plates at all.
    for lifecycle in ("provisioning", "validating", "retiring", "released", "pioneer"):
        for submitted in (False, True):
            districts = {"iron-plate": {"lifecycle_state": lifecycle, "replacement_submitted": submitted}}
            pending = lifecycle in {"provisioning", "validating", "retiring"} and submitted
            options = {o["option_id"]: o for o in compile_options(board, results, bootstrap_districts=districts)["options"]}
            assert ("op.retire_starter.iron-plate" in options) == pending
            assert pending_retirements(results, districts) == ({"iron-plate"} if pending else set())
            assert "op.foundation.iron-plate" not in options
            assert "op.extend.iron-plate" not in options
            if pending:
                assert options["op.retire_starter.iron-plate"]["features"]["plate_stage"] == "starter"
                assert options["op.retire_starter.iron-plate"]["features"]["lifecycle_state"] == lifecycle


def test_loop_records_ledger_obligation_and_samples_both_sides_of_retirement(tmp_path):
    import json
    from training.laya_ops import receipt

    class PendingSession(FakeSession):
        lifecycle = "provisioning"

        def economics(self):
            return {"loans": [], "projects": {}, "bootstrap_districts": {
                "iron-plate": {"lifecycle_state": self.lifecycle, "replacement_submitted": True}}}

        def acceptance(self):
            self.calls.append(("acceptance", self.lifecycle))
            return {"C2": "passed" if self.lifecycle == "released" else "failed"}

        def run(self, operation, **params):
            self.calls.append((operation, params))
            self.lifecycle = "released"
            return receipt(operation, params, {"lifecycle": self.lifecycle})

    class ChooseRetirement(GuidedPolicy):
        def choose(self, state, menu):
            choice = super().choose(state, menu)
            return {**choice, "option_id": "op.retire_starter.iron-plate", "need_id": "lifecycle:iron-plate"}

    session = PendingSession()
    summary = run(FakeWorld(), ChooseRetirement(3), journal=tmp_path / "journal.jsonl", decisions=1,
                  measure_ticks=600, seed=3, run_id="t", session=session, until_accepted="C2")
    assert summary["stop"] == "accepted_window_reached"
    assert session.calls == [("acceptance", "provisioning"), ("acceptance", "provisioning"),
                             ("retire_starter", {"recipe": "iron-plate"}),
                             ("acceptance", "released"), ("acceptance", "released")]
    row = json.loads((tmp_path / "decision-states.jsonl").read_text())
    assert row["state"]["observation_digest"]["saved_world"]["bootstrap_districts"]["iron-plate"]["lifecycle_state"] == "provisioning"
    assert "iron-plate replacement submitted" in row["state"]["brief"]


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


def test_laya_sees_its_recent_decisions():
    deferred = {"ok": False, "operation": "mall_slot", "params": {"item": "electronic-circuit"},
                "code": "rationed_mall_borrow_deferred"}
    starters = [{"ok": True, "operation": "starter", "params": {"recipe": p}, "code": "submitted"}
                for p in ("iron-plate", "copper-plate")]
    gear = {"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}, "code": "producing"}
    wait = {"ok": True, "operation": "wait", "params": {"ticks": 300}}
    recipes = {"iron-plate": {"ingredients": [{"name": "iron-ore"}]}, "copper-plate": {"ingredients": [{"name": "copper-ore"}]},
               "iron-gear-wheel": {"ingredients": [{"name": "iron-plate"}]},
               "copper-cable": {"ingredients": [{"name": "copper-plate"}]},
               "electronic-circuit": {"ingredients": [{"name": "copper-cable"}, {"name": "iron-plate"}]}}
    results = starters + [gear, deferred, wait, deferred, wait, wait, wait]
    features = {o["option_id"]: o["features"] for o in compile_options(BOARD, results, recipes=recipes)["options"]}
    # The same deferral twice in a row (waits between do not break the streak), and six decisions without a build.
    assert features["op.mall_slot.electronic-circuit"]["repeat_fails"] == 2
    # Its last attempt only borrowed another cell; a slot never asked for has borrowed nothing.
    assert features["op.mall_slot.electronic-circuit"]["borrows"]
    assert not features["op.mall_slot.copper-cable"]["borrows"]
    assert not features["op.foundation.iron-plate"]["tried"]
    assert features["op.foundation.iron-plate"]["stuck"] and features["wait.300"]["stuck"]
    assert not features["op.foundation.iron-plate"]["after_success_same_family"]
    # Right after a successful mall slot, another mall slot follows through.
    follow = {o["option_id"]: o["features"] for o in compile_options(BOARD, starters + [gear], recipes=recipes)["options"]}
    assert follow["op.mall_slot.copper-cable"]["after_success_same_family"]
    # Older decisions survive in the brief's TRIED line: attempts and last outcome per operation.
    from training.laya_ops_runner import brief
    told = brief(results, None, None, {}, 0)
    assert "mall_slot electronic-circuit 2x last rationed_mall_borrow_deferred" in told
    assert "RECENT: " in told and "SINCE LAST BUILD: 6 decisions" in told


def test_brief_layers_goal_next_major_and_recent_within_budget():
    from training.laya_mission import PHASES
    from training.laya_ops_runner import brief
    starter = {"ok": True, "operation": "starter", "params": {"recipe": "iron-plate"}, "tick": 1600}
    stone = {"ok": False, "operation": "starter", "params": {"recipe": "stone-brick"}, "tick": 9000,
             "code": "material_shortage", "details": {"required": {"inserter": 7}, "available": {"inserter": 1}}}
    drill = {"ok": False, "operation": "produce", "params": {"item": "electric-mining-drill"}, "tick": 9100,
             "code": "rationed_mall_borrow_deferred"}
    board = {"needs": [{"item": "stone-brick", "measured_per_s": 0.0, "condition": "unbuilt"}]}
    text = brief([starter, stone, drill, drill, drill], board, PHASES[1], {"C0": 8000}, 1000,
                 {"mission.phase-goals-first", "mission.ops-history-no-repeat"})
    assert text.startswith("GOAL: mining-productivity-4 via C0 > C1")
    # Lines name the shown doc rules that act on them; rules not shown are not named.
    assert "NOW [mission.phase-goals-first]: C1" in text and "RECENT [mission.ops-history-no-repeat]: " in text
    assert "mission.serve-bottleneck" not in text
    assert "NEXT: C2 iron_copper_rollout" in text and "stone-brick 0.00/0.25/s short" in text
    assert "+0.2min starter iron-plate | +2.2min C0 reached | +2.2min starter stone-brick -> material_shortage (inserter 6)" in text
    assert "produce electric-mining-drill -> rationed_mall_borrow_deferred x3" in text  # repeats collapse
    # Other codes may carry a bare count in details.required (rotating_mall_prerequisite_handoff).
    handoff = {"ok": False, "operation": "produce", "params": {"item": "electric-mining-drill"}, "tick": 9200,
               "code": "rotating_mall_prerequisite_handoff",
               "details": {"target": "electric-mining-drill", "prerequisite": "electronic-circuit", "required": 6}}
    assert "electric-mining-drill -> rotating_mall_prerequisite_handoff" in brief([handoff], board, PHASES[1], {}, 1000)
    many = [dict(drill, params={"item": f"item-{i}"}) for i in range(200)]
    assert len(brief([starter] * 300 + many, board, PHASES[1], {"C0": 8000}, 1000, limit=600)) <= 600
    # The doc rules get what is left after the brief; hard rules always stay.
    from training.laya_ops_runner import fit_rules
    rules = [{"rule_id": "hard", "kind": "hard", "statement": "x" * 50}] + [
        {"rule_id": f"soft{i}", "kind": "soft", "statement": "y" * 100} for i in range(10)]
    kept = fit_rules(rules, "b" * 300, limit=700)
    assert kept[0]["rule_id"] == "hard" and 1 < len(kept) < len(rules)


def test_receipts_say_whether_the_operation_finished():
    from training.laya_ops import receipt
    from training.laya_ops_runner import repeat_fails, since_build
    # ensure_produced returns None after building one missing stage: progress, not a standing producer.
    step = receipt("mall_slot", {"item": "electronic-circuit"}, None)
    assert (step["ok"], step["code"]) == (False, "prerequisite_advanced")
    assert receipt("mall_slot", {"item": "electronic-circuit"}, (45.5, 38.5))["code"] == "producing"
    assert receipt("foundation", {"recipe": "iron-plate"}, (1, 2))["code"] == "submitted"
    fence = receipt("reserve_steel_seed", {}, {"ok": True, "code": "reserved"})
    assert fence["ok"] and fence["code"] == "reserved"
    # A stage built toward the slot keeps the slot on the menu, retried after a wait.
    starters = [receipt("starter", {"recipe": p}, (0, 0)) for p in ("iron-plate", "copper-plate")]
    recipes = {"iron-plate": {"ingredients": [{"name": "iron-ore"}]}, "copper-plate": {"ingredients": [{"name": "copper-ore"}]},
               "copper-cable": {"ingredients": [{"name": "copper-plate"}]},
               "electronic-circuit": {"ingredients": [{"name": "copper-cable"}, {"name": "iron-plate"}]}}
    options = {o["option_id"]: o for o in compile_options(BOARD, starters + [step], tick=0, recipes=recipes)["options"]}
    assert options["op.mall_slot.electronic-circuit"]["features"]["waiting"]
    # Successes are not a failure streak; a reservation is not a build.
    produced = receipt("produce", {"item": "x"}, (0, 0))
    assert repeat_fails([produced, produced], ("produce", "x")) == 0
    assert since_build(starters + [fence]) == 1 and since_build(starters + [step]) == 0


def test_mall_slot_weights_are_exclusive():
    import json
    from training.laya_guided_policy import matches
    rule = next(r for r in json.load(open("docs/rl/laya_ops_guidelines.json"))["rules"] if r["rule_id"] == "mission.ops-mall-slots")
    def weight(product):
        w = 1.0
        for pref in rule["prefer"]:
            if matches(pref["when"], {"family": "mall_slot", "product": product}):
                w *= pref["weight"]
        return w
    assert weight("electronic-circuit") == 180.0 and weight("transport-belt") == 6.0


def test_options_carry_known_bills_and_lent_cells():
    from training.laya_ops_runner import brief, with_economics
    starters = [{"ok": True, "operation": "starter", "params": {"recipe": p}, "code": "submitted"}
                for p in ("iron-plate", "copper-plate")]
    gear = {"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}, "code": "producing"}
    results = starters + [gear, SHORT]
    economics = {"projects": {"paired_mall_inserter": {"state": "supply_wait", "reserved": {"inserter": 1},
                                                       "missing": {"electronic-circuit": 1, "inserter": 6}}},
                 "loans": [{"lender": "transport-belt", "making": "electric-mining-drill", "target": 2}]}
    menu = with_economics(compile_options(BOARD, results), results, economics)
    options = {o["option_id"]: o for o in menu["options"]}
    # The iron foundation reported its bill when short; the copper one has never said: unknown, not free.
    assert options["op.foundation.iron-plate"]["bill"] == {"transport-belt": 126, "inserter": 4}
    assert options["op.foundation.iron-plate"]["features"]["bill_known"]
    assert not options["op.foundation.copper-plate"]["features"]["bill_known"]
    assert options["op.produce.transport-belt"]["features"]["cell_lent"]  # its cell is making drills
    unknown = with_economics(compile_options(BOARD, results), results, {"projects": {}, "loans": None})
    assert next(o for o in unknown["options"] if o["family"] == "produce")["features"]["cell_lent"] is None
    text = brief(results, None, None, {}, 0, economics=economics)
    assert "MALL: transport-belt cell lent to make electric-mining-drill (to 2)" in text
    assert "RESERVED: paired_mall_inserter supply_wait missing electronic-circuit 1, inserter 6" in text



def _gear_world(gear_status="working", stock=0):
    recipes = {"iron-gear-wheel": {"ingredients": [{"name": "iron-plate", "amount": 2}]},
               "inserter": {"ingredients": [{"name": "iron-gear-wheel", "amount": 1}]}}
    machines = [{"name": "assembling-machine-1", "recipe": "iron-gear-wheel", "status": gear_status},
                {"name": "assembling-machine-1", "recipe": "inserter", "status": "item_ingredient_shortage"}]
    return recipes, machines, {"iron-gear-wheel": stock}


def test_a_flat_out_gear_producer_with_starved_consumers_offers_growth():
    # Live 2026-10-15: one gear AM1 at ~0.97/s behind every belt, inserter and drill; network gears 0.
    recipes, machines, stock = _gear_world()
    grow = lambda results=(), **world: [o for o in compile_options(
        BOARD, list(results), recipes=recipes, stock=world.get("stock", stock), machines=world.get("machines", machines),
        growable=("iron-gear-wheel",))["options"] if o["family"] == "grow"]
    (option,) = grow()
    assert option["params"] == {"item": "iron-gear-wheel", "machines": 1}
    assert not grow(stock={"iron-gear-wheel": 400})  # stocked: the producer is not the limit
    assert not grow(machines=_gear_world("disabled_by_control_behavior")[1])  # capped, not saturated
    asked = {"ok": False, "operation": "grow", "params": {"item": "iron-gear-wheel", "machines": 1},
             "code": "prerequisite_advanced"}
    assert not grow([asked])  # already asked to grow from 1; wait for that cell


def test_a_restarted_controller_inherits_only_lifecycle_receipts(tmp_path):
    from training.laya_ops_runner import lifecycle_receipts, plate_stages
    def record(family, params, ok, code):
        return json.dumps({"choice": {"option_id": "x"}, "options": [{"option_id": "x", "family": family, "params": params}],
                           "outcome": {"ok": ok, "code": code, "result": None}, "tick": 1})
    states = tmp_path / "decision-states.jsonl"
    states.write_text("\n".join([record("starter", {"recipe": "iron-plate"}, True, "submitted"),
                                 record("foundation", {"recipe": "iron-plate"}, True, "submitted"),
                                 record("produce", {"item": "inserter", "stock_target": 9}, True, "producing"),
                                 record("foundation", {"recipe": "copper-plate"}, False, "material_shortage")]))
    carried = lifecycle_receipts(states)
    assert [r["operation"] for r in carried] == ["starter", "foundation"]
    assert plate_stages(carried) == {"iron-plate": "foundation"}


def test_a_produce_level_is_fixed_at_failure_time_and_dropped_once_covered():
    # Live 2026-10-15: a stale "splitter short 1" read off today's stock made one splitter per loan up to 158.
    blocked = {"ok": False, "operation": "retire_starter", "params": {"recipe": "iron-plate"},
               "code": "material_shortage", "stock_at": {"splitter": 0},
               "details": {"required": {"splitter": 1}, "available": {}}}
    results = [{"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}, blocked]
    produce = lambda have: [o for o in compile_options(BOARD, results, stock={"splitter": have})["options"]
                            if o["option_id"] == "op.produce.splitter"]
    (option,) = produce(0)
    assert option["params"]["stock_target"] == 1
    assert not produce(157)  # made since the failure: retry the blocked operation instead


def test_starter_retirement_stays_on_the_menu_after_the_plate_goal_is_met():
    # Live 2026-10-15: in C4 the board held only plastic and the iron starter was never retired.
    built = [{"ok": True, "operation": op, "params": {"recipe": "iron-plate"}} for op in ("starter", "foundation")]
    met = {"needs": [{"item": "plastic-bar", "condition": "recipe_unavailable", "goal_required_per_s": 0.5}]}
    ids = {o["option_id"] for o in compile_options(met, built)["options"]}
    assert "op.retire_starter.iron-plate" in ids and "op.extend.iron-plate" not in ids


def test_an_unbuildable_district_is_not_waiting_and_counts_toward_a_stop():
    from training.laya_ops_runner import in_progress, repeat_fails
    # Bots are still building: a wait. An exhausted district has nothing under construction.
    assert in_progress("pending_system_construction")
    assert not in_progress("district_expansion_unbuildable")
    refused = [{"ok": False, "operation": "extend", "params": {"recipe": "copper-plate"}, "tick": tick,
                "code": "district_expansion_unbuildable"} for tick in (1000, 2000, 3000)]
    assert repeat_fails(refused, ("extend", "copper-plate")) == 3


def test_a_plate_system_still_constructing_is_waiting_until_its_retry_horizon():
    from training.laya_ops import receipt
    built = [receipt(op, {"recipe": p}, (0, 0)) for op in ("starter", "foundation") for p in ("iron-plate", "copper-plate")]
    refused = {"ok": False, "operation": "extend", "params": {"recipe": "copper-plate"}, "tick": 1000,
               "code": "pending_system_construction"}
    def menu(tick):
        return {o["option_id"]: o for o in compile_options(BOARD, built + [refused], tick=tick)["options"]}
    # Bots are building the copper system: the extend is a wait, and waiting is the constructive choice.
    assert menu(1600)["op.extend.copper-plate"]["features"]["waiting"]
    assert menu(1600)["wait.300"]["features"]["constructing"]
    # After the retry horizon the extend is worth asking again.
    assert not menu(1000 + 1800)["op.extend.copper-plate"]["features"]["waiting"]




def test_a_short_item_can_get_its_own_standing_mall_slot():
    # Live 2026-10-10: splitter was only offered as a borrowed batch, never as a standing producer.
    recipes = {"iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}]},
               "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}]},
               "splitter": {"ingredients": [{"name": "iron-plate", "amount": 5}, {"name": "copper-plate", "amount": 1}]}}
    built = [{"ok": True, "operation": op, "params": {"recipe": p}} for op in ("starter",) for p in ("iron-plate", "copper-plate")]
    short = {"ok": False, "operation": "foundation", "params": {"recipe": "iron-plate"}, "code": "material_shortage",
             "details": {"required": {"splitter": 2}, "available": {}}}
    slot = {"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}
    ids = {o["option_id"] for o in compile_options(BOARD, built + [short, slot], recipes=recipes)["options"]}
    assert {"op.mall_slot.splitter", "op.produce.splitter"} <= ids
    # Once the slot stands it is not offered again.
    standing = {"ok": True, "operation": "mall_slot", "params": {"item": "splitter"}}
    ids = {o["option_id"] for o in compile_options(BOARD, built + [short, slot, standing], recipes=recipes)["options"]}
    assert "op.mall_slot.splitter" not in ids


class SteppingPolicy:
    """Always choose one named option (or the first non-wait) to force a repeated decision."""

    authority = "guided_exploration"

    def __init__(self, option_id):
        self.option_id = option_id

    def choose(self, state, menu):
        chosen = next((o for o in menu["options"] if o["option_id"] == self.option_id), None)
        chosen = chosen or next(o for o in menu["options"] if o["family"] != "wait")
        return {"option_id": chosen["option_id"], "need_id": chosen["need_id"], "probability": 1.0, "rules": [],
                "distribution": [{"option_id": chosen["option_id"], "need_id": chosen["need_id"],
                                  "probability": 1.0, "weight": 1.0, "rules": [], "features": {}}]}


class SteppingSession:
    """A tool call that spends game time (the world runs while it blocks) and returns a shortage."""

    def __init__(self, world, *, block_ticks=0, progress_every=0):
        self.world, self.block_ticks, self.progress_every, self.calls = world, block_ticks, progress_every, 0

    def run(self, operation, **params):
        self.calls += 1
        self.world.tick += self.block_ticks  # The world keeps running inside the blocking call.
        result = {"ok": False, "operation": operation, "params": params, "code": "material_shortage",
                  "details": {"required": {"transport-belt": 10}, "available": {}}}
        if self.progress_every and self.calls % self.progress_every == 0:
            result["progress"] = True  # A funded plan submitted as a side effect before the incomplete return.
        return result

    def close(self):
        pass


def test_a_long_blocking_shortage_spends_no_decision_idle_stall_budget(tmp_path):
    # Live 2026-10-10: one foundation call ate ~12k of the 18k-tick window; that is work, not idle.
    world = FakeWorld()
    session = SteppingSession(world, block_ticks=25000)  # One call exceeds STALL_TICKS on its own.
    summary = run(world, SteppingPolicy("op.reserve_steel_seed"), journal=tmp_path / "j.jsonl", decisions=4,
                  measure_ticks=600, seed=1, run_id="long", session=session)
    assert summary["stop"] == "decision_budget"  # Not falsely stalled by the call's game time.
    assert summary["stall"]["tool_ticks"] == 25000 and summary["stall"]["idle_ticks"] == 0


def test_the_total_game_cap_still_stops_after_a_long_blocking_call(tmp_path):
    from training.laya_clean_runner import Settings
    world = FakeWorld()
    session = SteppingSession(world, block_ticks=25000)
    summary = run(world, SteppingPolicy("op.reserve_steel_seed"), journal=tmp_path / "j.jsonl", decisions=50,
                  measure_ticks=600, seed=1, run_id="cap", session=session, settings=Settings(max_game_ticks=20000))
    assert summary["stop"] == "window_timeout"  # The independent total game cap still bounds the run.


def test_repeated_unchanged_choices_still_stall(tmp_path):
    world = FakeWorld()
    session = SteppingSession(world, block_ticks=10)  # Quick retries that never change the world.
    summary = run(world, SteppingPolicy("op.reserve_steel_seed"), journal=tmp_path / "j.jsonl", decisions=40,
                  measure_ticks=600, seed=1, run_id="repeat", session=session)
    assert summary["stop"] == "stalled"
    assert summary["decisions"] <= 14 and session.calls == summary["decisions"]


def test_a_funded_partial_submission_resets_the_stall_clock(tmp_path):
    import json
    world = FakeWorld()
    # Every fourth decision a funded plan is submitted, so the no-progress streak never reaches the bound.
    session = SteppingSession(world, block_ticks=10, progress_every=4)
    summary = run(world, SteppingPolicy("op.reserve_steel_seed"), journal=tmp_path / "j.jsonl", decisions=20,
                  measure_ticks=600, seed=1, run_id="funded", session=session)
    assert summary["stop"] == "decision_budget"
    assert summary["stall"]["decisions"] < 4
    assert "funded_plan" in summary["progress_guard"]["last_reset"]["reasons"]
    rows = [json.loads(line) for line in (tmp_path / "decision-states.jsonl").read_text().splitlines()]
    funded = [r for r in rows if r["outcome"].get("progress")]
    # Recorded honestly: a funded partial submission is progress, but it is not an ok/built operation.
    assert funded and all(not r["outcome"].get("ok") for r in funded)


def test_funded_plan_progress_counts_a_funded_submission_not_an_earmark():
    from orchestrator.material_reservations import MaterialProject
    from training.laya_ops import funded_plan_progress, funded_plan_signature

    def project(pid, state, reserved):
        return MaterialProject(project_id=pid, target_item=None, state=state, required={"x": 5},
                               reserved=reserved, source_producers={}, expected_rates={}, eta_seconds={},
                               priority=1, sequence=1)

    earmark = {"mine": project("mine", "supply_wait", {"x": 2})}
    assert funded_plan_signature(earmark) == {}
    assert not funded_plan_progress({}, earmark)  # A queued unfunded earmark is not a funded submission.
    ready = {"mine": project("mine", "ready", {"x": 5})}
    assert funded_plan_signature(ready) == {}
    assert not funded_plan_progress({}, ready)
    assert not funded_plan_progress({"mine": 5}, ready)  # Unchanged since the snapshot.
    grown = {"mine": project("mine", "constructing", {"x": 5})}
    assert funded_plan_progress({"mine": 2}, grown)  # The reservation grew.


def test_repeated_construction_code_without_observed_progress_stalls(tmp_path):
    world = FakeWorld()
    class UnchangedConstruction(SteppingSession):
        def run(self, operation, **params):
            result = super().run(operation, **params)
            result["code"] = "pending_system_construction"
            return result
    summary = run(world, SteppingPolicy("op.reserve_steel_seed"), journal=tmp_path / "j.jsonl", decisions=40,
                  measure_ticks=600, seed=1, run_id="unchanged-construction", session=UnchangedConstruction(world))
    assert summary["stop"] == "stalled"


def test_funded_submission_reads_the_replaced_ledger_mapping(monkeypatch):
    from types import SimpleNamespace
    from orchestrator.material_reservations import MaterialProject
    from training.laya_ops import DeterministicSession, ab
    ledger = SimpleNamespace(projects={})
    monkeypatch.setattr(ab, "_MATERIAL_RESERVATION_LEDGER", ledger)
    def submit():
        ledger.projects = {"mine": MaterialProject(project_id="mine", target_item=None, state="constructing",
            required={"x": 5}, reserved={"x": 5}, source_producers={}, expected_rates={}, eta_seconds={}, priority=1, sequence=1)}
    session = DeterministicSession.__new__(DeterministicSession)
    session.client, session.bridge, session.emit = object(), object(), lambda _:None
    monkeypatch.setattr(session, "_operations", lambda: {"starter": lambda **_kwargs: submit()})
    monkeypatch.setattr(session, "_maintain_mall", lambda: None)
    monkeypatch.setattr(session, "_prepare_operation", lambda: None)
    assert session.run("starter", recipe="iron-plate")["progress"] is True
def _started_and_built():
    starters = [{"ok": True, "operation": "starter", "params": {"recipe": p}} for p in ("iron-plate", "copper-plate")]
    return starters + [{"ok": True, "operation": "foundation", "params": {"recipe": "iron-plate"}}]


def test_known_shortage_names_missing_inputs_and_survives_unrelated_waits():
    from training.laya_ops_runner import with_economics
    # Live 2026-10-10: retire_starter short 1 splitter, foundation short 1 electric-mining-drill.
    retire = {"ok": False, "operation": "retire_starter", "params": {"recipe": "iron-plate"}, "code": "material_shortage",
              "details": {"required": {"splitter": 1}, "available": {}},
              "stock_at": {"splitter": 0}, "allocation_at": {}}
    copper = {"ok": False, "operation": "foundation", "params": {"recipe": "copper-plate"}, "code": "material_shortage",
              "details": {"required": {"electric-mining-drill": 1}, "available": {}},
              "stock_at": {"electric-mining-drill": 0}, "allocation_at": {}}
    wait = {"ok": True, "operation": "wait", "params": {"ticks": 300}}
    results = _started_and_built() + [retire, copper, wait]
    options = {o["option_id"]: o for o in
               with_economics(compile_options(BOARD, results), results, {"projects": {}, "loans": []})["options"]}
    # A known bill names exactly what was missing; the unrelated wait did not erase it.
    for option_id, item in (("op.retire_starter.iron-plate", "splitter"),
                            ("op.foundation.copper-plate", "electric-mining-drill")):
        features = options[option_id]["features"]
        assert features["bill_known"] and features["inputs_missing"] is None
        assert features["last_attempt_missing_items"] == {item: 1}
        assert features["missing_items"] is None
    # A budget never reported is unknown, not "nothing missing".
    never = options["op.extend.iron-plate"]["features"]
    assert never["bill_known"] is False and never["inputs_missing"] is None and never["missing_items"] is None


def test_last_outcome_distinguishes_construction_geometry_delivery_and_failure():
    from training.laya_ops_runner import outcome_category
    assert outcome_category(None) == "unknown"
    assert outcome_category({"ok": True, "operation": "starter"}) == "delivery"
    assert outcome_category({"ok": False, "code": "pending_system_construction"}) == "constructing"
    assert outcome_category({"ok": False, "code": "district_expansion_unbuildable"}) == "no_geometry"
    assert outcome_category({"ok": False, "code": "material_shortage"}) == "material_failure"
    assert outcome_category({"ok": False, "code": "tool_error"}) == "failed"


def test_last_outcome_survives_unrelated_waits_and_marks_unchanged_material_failure():
    copper = {"ok": False, "operation": "foundation", "params": {"recipe": "copper-plate"}, "code": "material_shortage",
              "details": {"required": {"electric-mining-drill": 1}, "available": {}}, "stock_at": {"electric-mining-drill": 0},
              "allocation_at": {}}
    wait = {"ok": True, "operation": "wait", "params": {"ticks": 300}}
    results = _started_and_built() + [copper, wait]
    option = next(o for o in compile_options(BOARD, results)["options"] if o["option_id"] == "op.foundation.copper-plate")
    assert option["features"]["last_outcome"] == "unchanged_material_failure"
    # Restocked: the failure is real but no longer unchanged, so a retry is worth a look.
    options = {o["option_id"]: o["features"] for o in
               compile_options(BOARD, results, stock={"electric-mining-drill": 5})["options"]}
    assert options["op.foundation.copper-plate"]["last_outcome"] == "material_failure"


def test_a_changed_reservation_allocation_clears_an_unchanged_failure():
    from training.laya_ops_runner import unchanged_failures
    failed = dict(SHORT, stock_at={"transport-belt": 4, "inserter": 18},
                  allocation_at={"transport-belt": (("paired_mall_inserter", 6),), "inserter": ()})
    stock = {"transport-belt": 4, "inserter": 18}
    same = {"transport-belt": (("paired_mall_inserter", 6),)}
    assert unchanged_failures([failed], stock, same) == {("foundation", "iron-plate")}
    # The claim shrank, or its owner changed: the allocation moved, so the failure is not unchanged.
    assert unchanged_failures([failed], stock, {"transport-belt": (("paired_mall_inserter", 3),)}) == set()
    assert unchanged_failures([failed], stock, {"transport-belt": (("paired_mall_circuit", 6),)}) == set()


def test_json_roundtrip_of_same_reservation_keeps_material_failure_unchanged():
    from training.laya_ops_runner import unchanged_failures

    failed = dict(
        SHORT,
        stock_at={"transport-belt": 4},
        allocation_at={"transport-belt": [["paired_mall_inserter", 6]]},
    )
    current = {"transport-belt": (("paired_mall_inserter", 6),)}

    assert unchanged_failures([failed], {"transport-belt": 4}, current) == {
        ("foundation", "iron-plate"),
    }


def test_reservation_owner_is_named_for_a_bill_and_unknown_stays_unknown():
    from training.laya_ops_runner import with_economics
    copper = {"ok": False, "operation": "foundation", "params": {"recipe": "copper-plate"}, "code": "material_shortage",
              "details": {"stage": "paired_mall_inserter", "required": {"transport-belt": 6}, "available": {}}, "stock_at": {"transport-belt": 4},
              "allocation_at": {}}
    results = _started_and_built() + [copper]
    economics = {"projects": {"paired_mall_inserter": {"state": "supply_wait", "reserved": {"transport-belt": 6}}},
                 "loans": []}
    option = next(o for o in with_economics(compile_options(BOARD, results), results, economics)["options"]
                  if o["option_id"] == "op.foundation.copper-plate")
    assert option["features"]["reservation_owner"] == {"transport-belt": ["paired_mall_inserter"]}
    unclaimed = dict(
        copper,
        details={"stage": "unrelated_stage", "required": {"transport-belt": 6}, "available": {}},
    )
    unclaimed_menu = with_economics(
        compile_options(BOARD, _started_and_built() + [unclaimed]),
        _started_and_built() + [unclaimed], economics,
        stock={"transport-belt": 6},
    )
    option = next(o for o in unclaimed_menu["options"]
                  if o["option_id"] == "op.foundation.copper-plate")
    assert option["features"]["reservation_owner"] is None
    assert option["features"]["inputs_missing"] is None
    no_bill = next(o for o in with_economics(compile_options(BOARD, results), results, {"projects": {}, "loans": []})["options"]
                   if o["option_id"] == "op.extend.iron-plate")
    assert no_bill["features"]["reservation_owner"] is None


def test_historical_shortage_is_preserved_but_current_inputs_follow_known_stock():
    from training.laya_ops_runner import with_economics

    failed = {
        "ok": False, "operation": "foundation", "params": {"recipe": "copper-plate"},
        "code": "material_shortage",
        "details": {"stage": "copper_foundation", "required": {"electric-mining-drill": 1},
                    "available": {"electric-mining-drill": 0}},
        "stock_at": {"electric-mining-drill": 0}, "allocation_at": {},
    }
    results = _started_and_built() + [failed]
    economics = {"projects": {"copper_foundation": {
        "state": "supply_wait", "reserved": {},
    }}, "loans": []}
    menu = compile_options(
        BOARD, results, stock={"electric-mining-drill": 4},
        allocation={"electric-mining-drill": ()},
    )
    options = with_economics(
        menu, results, economics, stock={"electric-mining-drill": 4},
    )["options"]
    option = next(o for o in options if o["option_id"] == "op.foundation.copper-plate")

    assert option["bill"] == {"electric-mining-drill": 1}
    assert option["features"]["last_attempt_available_items"] == {"electric-mining-drill": 0}
    assert option["features"]["last_attempt_missing_items"] == {"electric-mining-drill": 1}
    assert option["features"]["available_items"] == {"electric-mining-drill": 4}
    assert option["features"]["missing_items"] == {}
    assert option["features"]["inputs_missing"] is False
    assert option["features"]["reservation_owner"] == {
        "electric-mining-drill": ["copper_foundation"],
    }

    foreign_reserved = {
        "projects": {
            "copper_foundation": {"state": "supply_wait", "reserved": {}},
            "other_project": {"state": "supply_wait", "reserved": {"electric-mining-drill": 4}},
        },
        "loans": [],
    }
    reserved = with_economics(
        menu, results, foreign_reserved, stock={"electric-mining-drill": 4},
    )["options"]
    option = next(o for o in reserved if o["option_id"] == "op.foundation.copper-plate")
    assert option["features"]["available_items"] == {"electric-mining-drill": 0}
    assert option["features"]["missing_items"] == {"electric-mining-drill": 1}
    assert option["features"]["inputs_missing"] is True

    unknown = with_economics(menu, results, economics)["options"]
    option = next(o for o in unknown if o["option_id"] == "op.foundation.copper-plate")
    assert option["features"]["available_items"] is None
    assert option["features"]["missing_items"] is None
    assert option["features"]["inputs_missing"] is None


def test_option_demand_distance_comes_from_shared_facts_only():
    from training.laya_ops_runner import with_economics
    short = dict(SHORT, stock_at={"transport-belt": 4, "inserter": 18})
    slot = {"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}
    results = _started_and_built() + [slot, short]
    economics = {"projects": {}, "loans": [], "demand": {"transport-belt": {"point": [20.0, 5.0], "distance": 25.0}}}
    option = next(o for o in with_economics(compile_options(BOARD, results), results, economics)["options"]
                  if o["option_id"] == "op.produce.transport-belt")
    assert option["features"]["demand_point"] == [20.0, 5.0]
    assert option["features"]["demand_distance"] == 25.0
    # No demand fact read: unknown, never a zero-distance consumer at the anchor.
    unknown = with_economics(compile_options(BOARD, results), results, {"projects": {}, "loans": []})
    option = next(o for o in unknown["options"] if o["option_id"] == "op.produce.transport-belt")
    assert option["features"]["demand_point"] is None and option["features"]["demand_distance"] is None


def test_menu_exposes_feasible_shortfalls_and_scoped_measures_where_measured():
    recipes = {"iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}]},
               "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}]},
               "transport-belt": {"ingredients": [{"name": "iron-plate", "amount": 1}]},
               "splitter": {"ingredients": [{"name": "electronic-circuit", "amount": 5}, {"name": "iron-plate", "amount": 5}]},
               "electronic-circuit": {"ingredients": [{"name": "iron-plate", "amount": 1}, {"name": "copper-cable", "amount": 3}]},
               "copper-cable": {"ingredients": [{"name": "copper-plate", "amount": 1}]}}
    short = dict(SHORT, details={"required": {"splitter": 3, "transport-belt": 10}, "available": {}})
    results = _started_and_built() + [short, {"ok": True, "operation": "mall_slot", "params": {"item": "transport-belt"}}]
    measurement = {"production": {"iron-plate": {"per_second": 2.0}}, "consumption": {"iron-plate": {"per_second": 0.5}}}
    menu = compile_options(BOARD, results, recipes=recipes, measurement=measurement)
    facts = menu["facts"]
    assert facts["shortfall_feasible"]["splitter"] is True and facts["shortfall_feasible"]["transport-belt"] is True
    assert facts["measured_plate_net"]["iron-plate"] == 1.5
    assert facts["measured_plate_net"]["copper-plate"] is None
    assert facts["measured_force_consumption"]["iron-plate"] == 0.5
    assert facts["measured_force_consumption"]["copper-plate"] is None
    assert "mall_consumption" not in facts
    # Without a measurement window the rates are unknown, not zero.
    assert compile_options(BOARD, results, recipes=recipes)["facts"]["measured_plate_net"] is None


def test_repeated_loan_batch_receipt_is_new_then_unchanged_then_progressing(monkeypatch):
    """Live d99d5eb9: four nominal temporary_mall_batch retries with crafts 0/1
    and zero delivery could not be told apart. The receipt must classify the
    first sight as new (a zero first craft is not yet a stall), a repeated
    identical batch as unchanged, and a moving counter as progressing."""
    from types import SimpleNamespace
    from orchestrator.autonomous_builder import ProductionPrerequisiteDeferred
    from orchestrator.mall_bootstrap import MallBootstrapLoan
    from planners.recipe_data import LINE_RECIPES
    from training import laya_ops
    from training.laya_ops import DeterministicSession

    monkeypatch.setitem(LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-2",
        "ingredients": ["electronic-circuit", "transport-belt", "iron-plate"],
        "amounts": [1, 1, 1], "product_amount": 1, "craft_time": 2.0,
    })
    loan = MallBootstrapLoan(
        original_recipe="copper-cable", target_item="splitter", target_count=1,
        side="left", requester_position=(36.5, 45.5), current_recipe="splitter",
        step_recipe="splitter", step_target_count=1, step_baseline_finished=0,
        step_required_crafts=1, step_minimum_crafts=1,
        machine_name="assembling-machine-1",
    )
    counter = {"value": 0}
    monkeypatch.setattr(laya_ops.mall_bootstrap, "active_bootstrap_loans", lambda *_a, **_k: (loan,))
    monkeypatch.setattr(laya_ops.ab, "_bootstrap_loan_products_finished",
                        lambda *_a, **_k: counter["value"])
    monkeypatch.setattr(laya_ops.live_base, "transferable_items",
                        lambda *_a, **_k: {"splitter": 0, "electronic-circuit": 9,
                                           "transport-belt": 8, "iron-plate": 10})
    monkeypatch.setattr(laya_ops.live_base, "chest_contents",
                        lambda *_a, **_k: {"electronic-circuit": 9, "transport-belt": 8,
                                           "iron-plate": 10})
    monkeypatch.setattr(laya_ops.live_base, "entity_status_name", lambda *_a, **_k: "working")
    monkeypatch.setattr(laya_ops.ab, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(projects={}))

    session = DeterministicSession.__new__(DeterministicSession)
    session.client, session.bridge, session.emit = object(), None, lambda _line: None
    session.reference_point = (0.0, 0.0)

    def batch(**_kwargs):
        raise ProductionPrerequisiteDeferred(
            "splitter is being made as a temporary bootstrap mall batch",
            code="temporary_mall_batch", state="supply_wait",
            details={"item": "splitter", "target": 1},
        )

    monkeypatch.setattr(session, "_operations", lambda: {"mall_slot": batch})

    first = session.run("mall_slot", item="splitter")
    assert first["loan_batch"]["status"] == "new" and first["progress"] is True
    assert first["loan_batch"]["owner"] == "copper-cable"
    assert first["loan_batch"]["step_recipe"] == "splitter"
    assert first["loan_batch"]["counter"] == 0

    again = session.run("mall_slot", item="splitter")
    assert again["loan_batch"]["status"] == "unchanged" and again["progress"] is False

    # A new loan that cannot cover its inputs is recorded but is not funded progress.
    empty = DeterministicSession.__new__(DeterministicSession)
    empty.client, empty.bridge, empty.emit = object(), None, lambda _line: None
    empty.reference_point = (0.0, 0.0)
    monkeypatch.setattr(empty, "_operations", lambda: {"mall_slot": batch})
    monkeypatch.setattr(laya_ops.live_base, "transferable_items", lambda *_a, **_k: {
        "electronic-circuit": 9, "transport-belt": 8, "iron-plate": 10})
    monkeypatch.setattr(laya_ops.ab, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(
        projects={}, allocatable_stock=lambda stock: {item: 0 for item in stock}))
    monkeypatch.setattr(laya_ops.live_base, "chest_contents", lambda *_a, **_k: {})
    unfunded = empty.run("mall_slot", item="splitter")
    assert unfunded["loan_batch"]["inputs_covered"] is False
    assert unfunded["progress"] is False

    counter["value"] = 1
    advanced = session.run("mall_slot", item="splitter")
    assert advanced["loan_batch"]["status"] == "progressing" and advanced["progress"] is True
    assert advanced["loan_batch"]["crafts_done"] == 1


def test_with_economics_relays_generic_batch_progress_for_the_option_item():
    from training.laya_ops_runner import with_economics

    recipes = {"iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}]},
               "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}]},
               "splitter": {"ingredients": [{"name": "iron-plate", "amount": 5},
                                            {"name": "copper-plate", "amount": 1}]}}
    built = [{"ok": True, "operation": op, "params": {"recipe": p}}
             for op in ("starter",) for p in ("iron-plate", "copper-plate")]
    short = {"ok": False, "operation": "foundation", "params": {"recipe": "iron-plate"},
             "code": "material_shortage", "details": {"required": {"splitter": 2}, "available": {}}}
    slot = {"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}
    results = built + [short, slot]
    economics = {"projects": {}, "loans": [{
        "lender": "copper-cable", "making": "splitter", "target": 1, "status": "progressing",
        "step_recipe": "splitter", "crafts_done": 1, "remaining_bill": {"splitter": 1},
        "missing_inputs": {"electronic-circuit": 2},
    }]}
    options = {o["option_id"]: o for o in
               with_economics(compile_options(BOARD, results, recipes=recipes), results, economics)["options"]}
    features = options["op.mall_slot.splitter"]["features"]
    assert features["batch_status"] == "progressing"
    assert features["batch_step"] == "splitter"
    assert features["batch_crafts"] == 1
    assert features["batch_remaining_bill"] == {"splitter": 1}
    assert features["batch_missing_inputs"] == {"electronic-circuit": 2}
    # An item with no active loan is unknown, never "unchanged".
    none = {o["option_id"]: o for o in with_economics(
        compile_options(BOARD, results, recipes=recipes), results,
        {"projects": {}, "loans": []})["options"]}
    assert none["op.mall_slot.splitter"]["features"]["batch_status"] is None
GOAL_RECIPES = {
    "iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}]},
    "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}]},
    "copper-cable": {"ingredients": [{"name": "copper-plate", "amount": 1}]},
    "electronic-circuit": {"ingredients": [{"name": "iron-plate", "amount": 1}, {"name": "copper-cable", "amount": 3}]},
    "plastic-bar": {"ingredients": [{"name": "petroleum-gas", "amount": 20}, {"name": "coal", "amount": 1}]},
    "advanced-circuit": {"ingredients": [{"name": "plastic-bar", "amount": 2}, {"name": "copper-cable", "amount": 4}]},
    "automation-science-pack": {"ingredients": [{"name": "copper-plate", "amount": 1}, {"name": "iron-gear-wheel", "amount": 1}]},
}
_GOAL_STARTERS = [{"ok": True, "operation": "starter", "params": {"recipe": p}} for p in ("iron-plate", "copper-plate")]


def test_goal_targets_get_generic_produce_options_before_any_shortage():
    # C4 could not build plastic: the mission target was never offered because only shortages were producible.
    board = {"needs": [{"item": "plastic-bar", "condition": "unbuilt", "goal_required_per_s": 0.5},
                       {"item": "advanced-circuit", "condition": "unbuilt", "goal_required_per_s": 0.25},
                       {"item": "automation-science-pack", "condition": "unbuilt", "goal_required_per_s": 0.5}]}
    ids = {o["option_id"] for o in
           compile_options(board, _GOAL_STARTERS, recipes=GOAL_RECIPES)["options"]}
    assert {"op.produce.plastic-bar", "op.produce.advanced-circuit", "op.produce.automation-science-pack"} <= ids


def test_research_and_lab_options_are_backed_by_live_prerequisites():
    board = {"needs": [{"item": "automation-science-pack", "condition": "unbuilt", "goal_required_per_s": 0.5}]}
    open_status = {"technology": {"canonical_name": "mining-productivity-4", "state": "available", "target_completed": False}}

    def options(goal_status, labs):
        return {o["option_id"] for o in compile_options(
            board, _GOAL_STARTERS, recipes=GOAL_RECIPES, research_goal="mining-productivity-4",
            goal_status=goal_status, labs=labs)["options"]}

    assert "op.research.mining-productivity-4" in options(open_status, labs=2)
    # Unknown status, a completed target, or an unopened technology never queues research blindly.
    assert "op.research.mining-productivity-4" not in options(None, labs=2)
    done = {"technology": {"canonical_name": "mining-productivity-4", "state": "completed", "target_completed": True}}
    assert "op.research.mining-productivity-4" not in options(done, labs=2)
    future = {"technology": {"canonical_name": "mining-productivity-4", "state": "future", "target_completed": False}}
    assert "op.research.mining-productivity-4" not in options(future, labs=2)
    # No lab observed: provision one through the shared toolkit instead.
    assert "op.produce.lab" in options(open_status, labs=0)
    assert "op.produce.lab" not in options(open_status, labs=2)


def test_natural_goal_keeps_running_while_construction_advances(tmp_path):
    from training.laya_clean_runner import Settings

    class BuildingWorld(FakeWorld):
        def __init__(self):
            super().__init__()
            self.observations = 0
        def query(self):
            self.observations += 1
            facts = super().query()
            facts['pending_ghosts'] = max(0, 100 - self.observations)
            return facts

    world = BuildingWorld()
    class BuildingSession(FakeSession):
        def run(self, operation, **params):
            self.calls.append((operation, params))
            world.advance(100)
            return {'ok': False, 'operation': operation, 'params': params,
                    'code': 'pending_system_construction'}
        def goal_status(self, technology):
            return {'technology': {'requested_name': technology, 'target_completed': False},
                    'research_progress': 0.0}

    settings = Settings()
    # The prior runner accepts Settings but ignores the new goal field, so this
    # regression exercises actual premature stopping without a collection error.
    object.__setattr__(settings, 'goal_technology', 'mining-productivity-4')
    object.__setattr__(settings, 'stall_observation_ticks', 18000)
    summary = run(world, GuidedPolicy(3), journal=tmp_path/'building.jsonl', decisions=14,
                  measure_ticks=600, seed=3, run_id='building', session=BuildingSession(), settings=settings)
    assert summary['decisions'] == 14
    assert summary['stop'] == 'decision_budget'


def test_future_repeatable_goal_offers_current_live_level():
    board={'needs':[]}
    status={'technology':{'state':'future','enabled':True,'canonical_name':'mining-productivity-3',
                          'current_level':3,'requested_level':4,'target_completed':False}}
    menu=compile_options(board, [], research_goal='mining-productivity-4',goal_status=status,labs=1)
    research=[o for o in menu['options'] if o['family']=='research']
    assert [o['params']['technology'] for o in research] == ['mining-productivity-3']


def test_natural_goal_stops_on_first_hard_tool_failure(tmp_path):
    from training.laya_clean_runner import Settings
    class FailedSession(FakeSession):
        def run(self, operation, **params):
            return {'ok':False,'operation':operation,'params':params,'code':'untyped_stuck',
                    'state':'failed','classification':'bug','message':'shared-tool invariant failed'}
        def goal_status(self, technology):
            return {'technology':{'requested_name':technology,'target_completed':False}}
    settings=Settings()
    object.__setattr__(settings,'goal_technology','mining-productivity-4')
    object.__setattr__(settings,'stall_observation_ticks',18000)
    summary=run(FakeWorld(),GuidedPolicy(3),journal=tmp_path/'hard.jsonl',decisions=6,
                measure_ticks=600,seed=3,run_id='hard',session=FailedSession(),settings=settings)
    assert summary['decisions'] == 1
    assert summary['stop'] == 'hard_failure'


def test_goal_produce_skips_raw_resource_dependencies_but_keeps_manufactured_targets():
    from training.laya_factory_status import compile_status
    # The saved-world helper exports solid recipes only; raw ore has none.
    recipes = {
        "iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}], "product_amount": 1, "energy_ticks": 192},
        "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}], "product_amount": 1, "energy_ticks": 192},
    }
    facts = {"recipes": recipes, "machines": [], "networks": {}, "pending_ghosts": 0,
             "production": {}, "consumption": {}}
    # Episode episode-20261010T101241Z-a476f94f: goal_demand gives the C0 board's raw ore
    # dependency a goal_required_per_s, so the generic goal-produce path offered
    # op.produce.iron-ore. ensure_produced mines ore through a starter/mine, never an
    # assembler, so it raised untyped_stuck at decision 0 and ended the run.
    c0_ids = {o["option_id"] for o in
              compile_options(compile_status(facts, {"iron-plate": 0.5, "copper-plate": 0.5}), [],
                              recipes=recipes)["options"]}
    assert "op.produce.iron-ore" not in c0_ids and "op.produce.copper-ore" not in c0_ids
    # A manufactured goal target absent from the live recipe facts (plastic is fluid-backed)
    # must still be offered: the generic path exists for exactly these.
    c4_ids = {o["option_id"] for o in
              compile_options(compile_status(facts, {"plastic-bar": 0.5}), [], recipes=recipes)["options"]}
    assert "op.produce.plastic-bar" in c4_ids


def test_fresh_starter_is_not_offered_while_a_district_has_reserved_a_replacement():
    # episode-20261010T102842Z-68b348c2: DECISION 0's produce-lab prerequisite recorded a copper
    # managed refinery as the copper-plate district's pioneer (lifecycle "provisioning"). A later
    # direct starter recomputed a different pioneer; the tooling rejected it as
    # bootstrap_lifecycle_conflict (bug-classified) and the goal run ended. The district is
    # reconciled, not re-opened, so no fresh starter may be offered for it.
    claimed = {"copper-plate": {"lifecycle_state": "provisioning", "replacement_submitted": False}}
    ids = {o["option_id"] for o in compile_options(BOARD, [], bootstrap_districts=claimed)["options"]}
    assert "op.starter.copper-plate" not in ids
    assert "op.starter.iron-plate" in ids  # no district for iron: its starter is still legal
    # A district still in "pioneer" has not reserved a replacement: a same-plan starter retry is legal.
    pioneer = {"copper-plate": {"lifecycle_state": "pioneer", "replacement_submitted": False}}
    assert "op.starter.copper-plate" in {
        o["option_id"] for o in compile_options(BOARD, [], bootstrap_districts=pioneer)["options"]}

class _ClockWorld(FakeWorld):
    """Game time passes between observations; nothing else in the world changes."""

    def query(self):
        self.tick += 300
        return super().query()


class _BatchSession(FakeSession):
    def __init__(self, step):
        super().__init__()
        self.count, self.step = 100, step

    def active_batch_crafts(self):
        self.count += self.step
        return {("copper-cable", "pipe", "pipe", 33.5, 45.5): self.count}


def test_an_advancing_batch_survives_the_stall_detector_and_an_unchanged_one_does_not(tmp_path, monkeypatch):
    # batch-20261017-day pipe-300-s76401: pipe crafts 240 -> 278 of 300, then STALLED 5 s later.
    import training.laya_ops_runner as runner
    from training.laya_ops_runner import batch_advanced
    monkeypatch.setattr(runner, "STALL_TICKS", 1500)
    assert not batch_advanced({}, {("a",): 5}) and not batch_advanced({("a",): 5}, {("a",): 5})
    assert batch_advanced({("a",): 5}, {("a",): 6})
    stop = lambda step: run(_ClockWorld(), GuidedPolicy(3), journal=tmp_path / f"j{step}.jsonl", decisions=20,
                            measure_ticks=600, seed=3, run_id=f"t{step}", session=_BatchSession(step))["stop"]
    assert stop(step=4) == "decision_budget"  # crafting toward its bill: not stalled
    assert stop(step=0) == "stalled"  # an unchanged batch keeps nothing alive


class _NetworkSession(FakeSession):
    def __init__(self, counts):
        super().__init__()
        self.counts = iter(counts)

    def built_network(self):
        return next(self.counts, None)


def test_poles_and_roboports_chained_to_a_far_site_survive_the_stall_detector(tmp_path, monkeypatch):
    # laya-c6a-20261019 facts-s77605: roboports chained toward coal at (-334.5, 12.5), outside the facts'
    # bounds, one hop per decision -- then STALLED. Demolish/rebuild (40 -> 30 -> 40) is not progress.
    import training.laya_ops_runner as runner
    monkeypatch.setattr(runner, "STALL_TICKS", 1500)
    stop = lambda name, counts: run(_ClockWorld(), GuidedPolicy(3), journal=tmp_path / f"{name}.jsonl", decisions=20,
                                    measure_ticks=600, seed=3, run_id=name, session=_NetworkSession(counts))["stop"]
    assert stop("chain", range(40, 80, 2)) == "decision_budget"
    assert stop("cycle", [40, 30, 40] * 7) == "stalled"
    assert stop("unreadable", []) == "stalled"


class _LadderSession(FakeSession):
    """Every operation reports the chemical-ladder rung the tooling waits at next."""

    def __init__(self, rungs):
        super().__init__()
        self.rungs = iter(rungs)

    def run(self, operation, **params):
        self.calls.append((operation, params))
        return {"ok": False, "operation": operation, "params": params, "code": "chemical_capability_batch",
                "details": {"target": "plastic-bar", "rung": next(self.rungs, "pipe"), "required_stock": 1}}


def test_a_completed_ladder_rung_survives_the_stall_detector_and_a_remade_one_does_not(tmp_path, monkeypatch):
    # laya-c7 netstall-s78004 stalled in the decision that delivered its pumpjack rung: each rung loan finished
    # inside one decision, so no batch counter was ever seen rising. pipe <-> chemical-plant is make-then-consume.
    import training.laya_ops_runner as runner
    monkeypatch.setattr(runner, "STALL_TICKS", 1500)
    ladder = ("pipe", "steel-plate", "chemical-plant", "oil-refinery", "offshore-pump", "pumpjack", "plastic-bar",
              "advanced-circuit", "sulfur", "sulfuric-acid")
    stop = lambda name, rungs: run(_ClockWorld(), GuidedPolicy(3), journal=tmp_path / f"{name}.jsonl",
                                   decisions=8, measure_ticks=600, seed=3, run_id=name,
                                   session=_LadderSession(rungs))["stop"]
    assert stop("climb", ladder) == "decision_budget"
    assert stop("cycle", ["pipe", "chemical-plant"] * 10) == "stalled"


def test_busy_progress_without_a_new_phase_or_acceptance_stops_on_the_phase_budget(tmp_path, monkeypatch):
    # laya-c7 netstall-s78045: 1,505 decisions over ~430k ticks at C0, every check finding some "progress".
    import training.laya_ops_runner as runner
    monkeypatch.setattr(runner, "PHASE_BUDGET_TICKS", 6000)
    summary = run(_ClockWorld(), GuidedPolicy(3), journal=tmp_path / "j.jsonl", decisions=40, measure_ticks=600,
                  seed=3, run_id="t", session=_BatchSession(4))
    assert summary["stop"] == "phase_budget" and summary["decisions"] < 40

def test_named_prerequisites_are_choices_outside_the_phase_alongside_all_shortages():
    # Day control-s76403 handed one chest off to plastic 125 times in C2 without a plastic choice.
    handoff = {"ok": False, "operation": "produce", "params": {"item": "passive-provider-chest"},
               "code": "chemical_capability_handoff", "details": {"rung": "plastic-bar"}}
    results = [{"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}},
               dict(SHORT, details={"required": {f"part-{n}": 100 for n in range(5)}}), handoff]
    options = {o["option_id"]: o for o in compile_options(BOARD, results)["options"]}
    plastic = options["op.produce.plastic-bar"]
    assert plastic["params"] == {"item": "plastic-bar", "stock_target": 1, "finite": False}
    assert plastic["features"]["prerequisite_for"] == ["produce:passive-provider-chest"]
    assert {o["product"] for o in options.values() if o["family"] == "produce"} == {
        "plastic-bar", *(f"part-{n}" for n in range(5))}
    # The parent's new result supersedes its handoff, so old prerequisites cannot accumulate forever.
    results.append(dict(handoff, ok=True, code="producing", details=None))
    assert "op.produce.plastic-bar" not in {o["option_id"] for o in compile_options(BOARD, results)["options"]}


def test_named_quantity_is_an_absolute_target_and_deduplicates_other_demand():
    handoff = {"ok": False, "operation": "produce", "params": {"item": "inserter"},
               "code": "rotating_mall_prerequisite_handoff",
               "details": {"prerequisite": "electronic-circuit", "required": 6}}
    slot = {"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}
    def produce(stock):
        return [o for o in compile_options(BOARD, [slot, handoff], stock=stock)["options"]
                if o["option_id"] == "op.produce.electronic-circuit"]
    assert produce({"electronic-circuit": 2})[0]["params"]["stock_target"] == 6
    assert not produce({"electronic-circuit": 6})


def test_goal_production_requires_an_enabled_recipe_and_preserves_plate_lifecycle():
    board = {"needs": [{"item": "plastic-bar", "condition": "recipe_unavailable", "goal_required_per_s": .5}]}
    def ids(enabled):
        return {o["option_id"] for o in compile_options(board, [], enabled_recipes=enabled)["options"]}
    assert "op.produce.plastic-bar" in ids({"plastic-bar": True})
    assert "op.produce.plastic-bar" not in ids({})
    assert "op.produce.iron-plate" not in {o["option_id"] for o in compile_options(
        BOARD, [], enabled_recipes={"iron-plate": True})["options"]}


def test_runner_queries_goal_and_new_prerequisite_facts_before_the_next_choice(tmp_path):
    class DemandWorld(FakeWorld):
        def query(self):
            return {**super().query(), "queried": list(self.extra_recipes)}

    class HandoffSession(FakeSession):
        def run(self, operation, **params):
            return {"ok": False, "operation": operation, "params": params,
                    "code": "chemical_capability_handoff", "details": {"rung": "audit-part"}}

    class Policy:
        authority = "test"
        def choose(self, state, menu):
            if world.tick == 1000:
                world.tick += 1
            else:
                assert "audit-part" in world.extra_recipes
            assert "plastic-bar" in world.extra_recipes  # mission data, even before C4
            option = next(o for o in menu["options"] if o["family"] != "wait")
            return {**option, "probability": 1., "rules": [], "distribution": []}

    world = DemandWorld()
    run(world, Policy(), journal=tmp_path / "journal.jsonl", decisions=2, measure_ticks=600,
        seed=3, run_id="facts", session=HandoffSession())


def test_selected_bill_exposes_missing_work_without_failure_and_retires_its_old_shortage():
    from training.laya_ops_runner import recipe_demand
    slot = {"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}
    bill = {"complete": False, "remaining": {"pipe-to-ground": 7}, "missing": {"pipe-to-ground": 3},
            "stock_levels": {"pipe-to-ground": 7}, "replaces_shortage_stages": ["chemical_packet"]}
    def menu(results, tx):
        return {o["option_id"]: o for o in compile_options(BOARD, results, transactions={"oil": tx})["options"]}
    option = menu([slot], bill)["op.produce.pipe-to-ground"]
    assert option["params"]["stock_target"] == 7 and option["features"]["transactions"] == ["oil"]
    assert "pipe-to-ground" in recipe_demand([], {"transactions": {"oil": bill}})
    old = dict(SHORT, details={"stage": "chemical_packet", "required": {"pipe-to-ground": 12}, "available": {}})
    done = dict(bill, remaining={}, missing={}, stock_levels={})
    assert "op.produce.pipe-to-ground" not in menu([slot, old], done)
    # An unreadable transaction cannot erase the last known shortage; another consumer keeps its own bill.
    assert "op.produce.pipe-to-ground" in menu([slot, old], {"error": "survey failed"})
    other = dict(old, details={**old["details"], "stage": "other_packet"})
    assert "op.produce.pipe-to-ground" in menu([slot, other], done)


def test_required_construction_delivery_is_progress_but_rebuild_cycles_are_bounded(tmp_path, monkeypatch):
    import training.laya_ops_runner as runner
    monkeypatch.setattr(runner, "STALL_TICKS", 1500)
    class Deliveries(FakeSession):
        def __init__(self, cycling):
            super().__init__()
            self.count, self.cycling = 0, cycling
        def material_transactions(self):
            self.count = (1 - self.count) if self.cycling else self.count + 1
            return {"test": {"intent_sha256": "fixed", "installed": {"pipe": self.count}}}
    for cycling in (False, True):
        summary = run(_ClockWorld(), GuidedPolicy(3), journal=tmp_path / f"{cycling}.jsonl", decisions=20,
                      measure_ticks=600, seed=3, run_id="delivery", session=Deliveries(cycling))
        assert summary["stop"] == ("stalled" if cycling else "decision_budget")


def test_selected_parent_option_carries_its_current_owned_bill():
    from training.laya_ops_runner import with_economics
    board = {"needs": [{"item": "plastic-bar", "condition": "unbuilt", "goal_required_per_s": .5}]}
    tx = {"target_item": "plastic-bar", "remaining": {"pipe": 2}, "missing": {}, "stock_levels": {},
          "complete": False, "replaces_shortage_stages": ["chemical_packet"]}
    old = {"ok": False, "operation": "produce", "params": {"item": "plastic-bar"}, "code": "material_shortage",
           "details": {"stage": "chemical_packet", "required": {"pipe": 10}, "available": {}}}
    def option():
        menu = compile_options(board, [old], enabled_recipes={"plastic-bar": True}, transactions={"oil": tx})
        return next(o for o in with_economics(menu, [old], {"transactions": {"oil": tx}})["options"]
                    if o["option_id"] == "op.produce.plastic-bar")
    assert option()["bill"] == {"pipe": 2} and option()["bill_owner"] == "oil"
    tx["remaining"] = {}
    assert option()["bill"] == {} and option()["features"]["bill_known"]  # Known paid material bill, service still pending.


def test_named_prerequisites_and_their_bill_items_inherit_the_consumers_goal():
    blocked = dict(SHORT, details={"required": {"passive-provider-chest": 1}, "available": {}})
    handoff = {"ok": False, "operation": "produce", "params": {"item": "passive-provider-chest"},
               "code": "chemical_capability_handoff", "details": {"rung": "plastic-bar"}}
    slot = {"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}}
    tx = {"target_item": "plastic-bar", "missing": {"pipe": 4}, "stock_levels": {"pipe": 4}}
    options = {o["option_id"]: o for o in compile_options(BOARD, [slot, blocked, handoff],
                                                         transactions={"oil": tx})["options"]}
    assert options["op.produce.plastic-bar"]["features"]["goal_need"]
    assert options["op.produce.pipe"]["features"]["goal_need"]


def test_a_fluid_starved_or_unreleased_goal_item_keeps_its_capability_choice():
    # Cohort 7: plastic machines stood, flipped to input_starved on petroleum, and the menu went wait-only.
    def board(statuses):
        return {"needs": [{"item": "plastic-bar", "condition": "input_starved", "goal_required_per_s": .5,
                           "status_counts": statuses}]}
    fluid, solid = board({"fluid_ingredient_shortage": 2}), board({"item_ingredient_shortage": 2})
    oil = {"target_item": "plastic-bar", "complete": False, "missing": {},
           "packets_pending": ["chemical_crude_pipeline", "chemical_plastic_petroleum_pipeline"]}
    def menu(board, results=(), tx=None):
        return {o["option_id"]: o for o in compile_options(board, list(results), enabled_recipes={"plastic-bar": True},
                                                           transactions=tx)["options"]}
    plastic = menu(fluid, tx={"opening_oil": oil})["op.produce.plastic-bar"]
    assert plastic["params"]["finite"] is False and plastic["features"]["packets_pending"] == 2
    assert "packets_pending" not in menu(fluid)["op.produce.plastic-bar"]["features"]
    # Solid starvation is unchanged, unless the item's own packets are still unreleased.
    assert "op.produce.plastic-bar" not in menu(solid)
    assert "op.produce.plastic-bar" not in menu(solid, tx={"opening_oil": dict(oil, packets_pending=[])})
    assert "op.produce.plastic-bar" in menu(solid, tx={"opening_oil": oil})
    # A re-pick that changes nothing is visible as no progress.
    stalled = {"ok": False, "operation": "produce", "params": {"item": "plastic-bar"}, "code": "stuck",
               "message": "petroleum-gas route blocked"}
    again = menu(fluid, [stalled, stalled])["op.produce.plastic-bar"]["features"]
    assert again["no_progress"] == 2 and again["last_code"] == "stuck"


def test_the_brief_counts_unreleased_chemical_packets():
    from training.laya_ops_runner import brief
    economics = {"transactions": {"opening_oil": {"remaining": {}, "missing": {}, "packets_pending": ["a", "b"]}}}
    assert "packets_pending 2" in brief([], None, None, {}, 0, economics=economics)
    economics["transactions"]["opening_oil"]["packets_pending"] = None
    assert "packets_pending ?" in brief([], None, None, {}, 0, economics=economics)


class _RegressingSession(FakeSession):
    """C2 passes on the first sample, then fails; the latch keeps reporting it as reached."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def acceptance(self):
        self.calls += 1
        self.acceptance_fresh = {"C2": "passed" if self.calls == 1 else "failed"}
        return {"C2": "passed"}


def test_summary_final_acceptance_shows_a_regression_the_latch_hides(tmp_path):
    summary = run(FakeWorld(), GuidedPolicy(3), journal=tmp_path / "j.jsonl", decisions=3, measure_ticks=600,
                  seed=3, run_id="regress", session=_RegressingSession())
    assert summary["accepted_ever"] == ["C2"] and summary["accepted_final"] == []
    rows = [json.loads(line) for line in (tmp_path / "j.jsonl").read_text().splitlines()]
    assert [r["fresh"]["C2"] for r in rows if r.get("event") == "acceptance"][:2] == ["passed", "failed"]


def test_an_observed_ready_foundation_offers_its_starter_retirement_after_a_wait_outcome():
    # Live C12 ref-s79400: the copper foundation returned roboport_coverage_construction_wait, bots finished it,
    # deterministic C2 saw it ready, but the menu still thought copper was at "starter": with copper satisfied it
    # offered nothing for copper, so the starter was never retired (30 of 111 never-C2 runs had this shape).
    board = {"needs": [{"item": "iron-plate", "condition": "unbuilt", "goal_required_per_s": 0.5},
                       {"item": "copper-plate", "condition": "satisfied", "goal_required_per_s": 0.5}]}
    results = [{"ok": True, "operation": "starter", "params": {"recipe": p}} for p in ("iron-plate", "copper-plate")]
    results.append({"ok": False, "operation": "foundation", "params": {"recipe": "copper-plate"},
                    "code": "roboport_coverage_construction_wait"})
    ids = lambda **kw: {o["option_id"] for o in compile_options(board, results, **kw)["options"]}
    assert not any(i.endswith(".copper-plate") for i in ids())
    assert "op.retire_starter.copper-plate" in ids(foundations={"copper-plate": True})
    # Unknown or not-ready observations never invent a foundation.
    assert not any(i.endswith(".copper-plate") for i in ids(foundations={"copper-plate": None}))
    assert not any(i.endswith(".copper-plate") for i in ids(foundations={"copper-plate": False}))
