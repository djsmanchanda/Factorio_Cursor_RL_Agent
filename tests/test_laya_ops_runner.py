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
    monkeypatch.setattr(ops, "phase_reached", lambda _: next(checks, False))

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
    released = base + [{"ok": True, "operation": "retire_starter", "params": {"recipe": "iron-plate"},
                        "result": {"lifecycle": "released"}}]
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
    fence = receipt("reserve_steel_seed", {}, None)
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


def test_an_unbuildable_district_is_not_waiting_and_counts_toward_a_stop():
    from training.laya_ops_runner import in_progress, repeat_fails
    # Bots are still building: a wait. An exhausted district has nothing under construction.
    assert in_progress("pending_system_construction")
    assert not in_progress("district_expansion_unbuildable")
    refused = [{"ok": False, "operation": "extend", "params": {"recipe": "copper-plate"}, "tick": tick,
                "code": "district_expansion_unbuildable"} for tick in (1000, 2000, 3000)]
    assert repeat_fails(refused, ("extend", "copper-plate")) == 3


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
