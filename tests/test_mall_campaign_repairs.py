# Path: tests/test_mall_campaign_repairs.py | Purpose: Regress the stopped campaign's loan, stock-scope and scheduling failures.
from types import SimpleNamespace
import json

import pytest

from orchestrator import autonomous_builder as b, live_base
from orchestrator.mall_bootstrap import MallBootstrapLoan
from training.laya_ops import DeterministicSession
from training import laya_ops_runner as ops

pytestmark = pytest.mark.fast


def loan(**kwargs):
    return MallBootstrapLoan(original_recipe="copper-cable", target_item="transport-belt",
                             target_count=10, side="left", requester_position=(42.5, 38.5),
                             current_recipe="copper-cable", **kwargs)


def setup_loan(monkeypatch, current, available, spendable):
    monkeypatch.setattr(b, "_MATERIAL_RESERVATION_LEDGER", None)
    monkeypatch.setattr(b, "_BLOCKING_MALL_ITEMS", set())
    monkeypatch.setattr(b.live_base, "available_items", lambda *_: available)
    monkeypatch.setattr(b.live_base, "transferable_items", lambda *_: spendable)
    monkeypatch.setattr(b.live_base, "chest_contents", lambda *_: {})
    monkeypatch.setattr(b.live_base, "machine_input_contents", lambda *_: {}, raising=False)
    monkeypatch.setattr(b.live_base, "find_line", lambda *_a, **_k: None)
    monkeypatch.setattr(b, "active_bootstrap_loans", lambda *_: [current])
    monkeypatch.setattr(b, "_bootstrap_loan_products_finished", lambda *_: 0)
    monkeypatch.setattr(b, "_missing_chemical_ladder_predecessor", lambda *_: None)
    monkeypatch.setattr(b, "mall_slot_uses_shared_provider", lambda *_: False)
    plans = []
    monkeypatch.setattr(b, "_submit", lambda _c, _br, _s, plan, *_a, **_kw: plans.append(plan))
    return plans


def test_completed_prerequisite_restore_removes_the_installed_tag(monkeypatch):
    current = loan(spare_target_count=20, step_recipe="iron-gear-wheel", step_target_count=50,
                   step_baseline_finished=0, step_required_crafts=5, step_minimum_crafts=5)
    stock = {"transport-belt": 10, "iron-gear-wheel": 50, "iron-plate": 100}
    plans = setup_loan(monkeypatch, current, stock, stock)
    monkeypatch.setattr(b, "_BLOCKING_MALL_ITEMS", {"transport-belt"})
    b._submit_bootstrap_loan(object(), object(), "nauvis", "player", current, lambda _: None)
    requester = plans[0]["phases"][0]["actions"][0]
    assert current.group in requester["clear_logistic_groups"]
    assert "mall:copper-cable:right" not in requester["clear_logistic_groups"]


def test_peer_requester_wip_is_not_supply_for_this_batch(monkeypatch):
    current = loan()
    plans = setup_loan(monkeypatch, current,
                       {"iron-gear-wheel": 50, "iron-plate": 100}, {"iron-plate": 100})
    monkeypatch.setattr(b.live_base, "chest_contents", lambda *_: {"iron-gear-wheel": 3})
    c = SimpleNamespace(command=lambda _: "NONE")
    b._submit_bootstrap_loan(c, object(), "nauvis", "player", current, lambda _: None)
    machine = plans[0]["phases"][0]["actions"][1]
    assert machine["recipe"] == "iron-gear-wheel"
    # Five gears are needed for ten belts: three are owned, two still owed.
    assert machine["logistic_condition"]["constant"] == 52


def test_existing_ingredient_producer_gets_the_gate_instead_of_the_loan(monkeypatch):
    current = loan()
    plans = setup_loan(monkeypatch, current, {"iron-plate": 100}, {"iron-plate": 100})
    monkeypatch.setattr(b.live_base, "find_line", lambda *_a, **_k:
                        live_base.LineState("iron-gear-wheel", 1, 0, (20.5, 20.5), ((18.5, 20.5),)))
    monkeypatch.setattr(b, "_rationed_mall_wip_gap", lambda *_: 0)
    gates = []
    monkeypatch.setattr(b, "_configure_owned_mall_gate", lambda *a: gates.append((a[4], a[7])))
    monkeypatch.setattr(b, "_deliver_cell_ingredients", lambda *_: None)
    result = b._submit_bootstrap_loan(SimpleNamespace(command=lambda _: "NONE"), object(),
                                    "nauvis", "player", current, lambda _: None)
    assert gates == [("iron-gear-wheel", 5)]
    assert not plans and "standing iron-gear-wheel" in result


def test_economics_reports_live_loans_without_changing_progress_memory(monkeypatch):
    from training.laya_ops import mall_bootstrap
    current = loan()
    s = object.__new__(DeterministicSession)
    s.client = object(); s.reference_point = (0, 0); s.mall_targets = {}
    s._loan_batch_seen = {}
    s._loan_batch_facts = lambda *_a, **_k: {"target": "transport-belt"}
    s._demand_facts = lambda: {}
    monkeypatch.setattr(b, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(projects={}))
    monkeypatch.setattr(b, "_BOOTSTRAP_DISTRICT_LEDGER", SimpleNamespace(states=lambda: []))
    monkeypatch.setattr(mall_bootstrap, "active_bootstrap_loans", lambda *_: [current])
    monkeypatch.setattr(b.live_base, "transferable_items", lambda *_: {})
    result = s.economics()
    assert result["loan_error"] is None
    assert result["loans"][0]["status"] == "new"
    assert s._loan_batch_seen == {}


def test_batch_spares_stay_at_twenty_five_percent_after_starters(monkeypatch):
    monkeypatch.setattr(b, "_metal_starter_transition_complete", lambda *_: True)
    monkeypatch.setattr(b, "_rationed_mall_wip_gap", lambda *_: 0)
    assert b._rationed_mall_spare_target(object(), "nauvis", "player", "splitter", 4) == 5
    assert b._rationed_mall_spare_target(object(), "nauvis", "player", "transport-belt", 148) == 185


def test_bootstrap_finishes_owned_construction_before_new_plate_or_chemical_work():
    board = {"needs": [{"item": p, "condition": "unbuilt", "goal_required_per_s": 1}
                       for p in ("iron-plate", "copper-plate", "plastic-bar")]}
    recipes = {"iron-plate": {"ingredients": [{"name": "iron-ore", "amount": 1}]},
               "copper-plate": {"ingredients": [{"name": "copper-ore", "amount": 1}]},
               "plastic-bar": {"ingredients": [{"name": "copper-plate", "amount": 1}]}}
    results = [{"operation": "starter", "ok": True, "params": {"recipe": p}} for p in ops.PLATES]
    districts = {"copper-plate": {"lifecycle_state": "validating", "replacement_submitted": True,
                                  "construction_pending": True}}
    menu = ops.compile_options(board, results, recipes=recipes, bootstrap_districts=districts)
    ids = {o["option_id"] for o in menu["options"]}
    assert "op.foundation.iron-plate" not in ids
    assert "op.produce.plastic-bar" not in ids
    assert "op.retire_starter.copper-plate" in ids
    needed = ops.compile_options(board, results, recipes=recipes, bootstrap_districts=districts,
                                 pending_precursors=("plastic-bar",))
    assert "op.produce.plastic-bar" in {o["option_id"] for o in needed["options"]}
    districts["copper-plate"]["construction_pending"] = False
    ids = {o["option_id"] for o in ops.compile_options(board, results, recipes=recipes,
                                                      bootstrap_districts=districts)["options"]}
    assert {"op.foundation.iron-plate", "op.produce.plastic-bar"} <= ids
    districts["copper-plate"]["construction_pending"] = True
    results.append({"operation": "foundation", "ok": True, "params": {"recipe": "copper-plate"}})
    ids = {o["option_id"] for o in ops.compile_options(board, results, recipes=recipes,
                                                      bootstrap_districts=districts)["options"]}
    assert "op.extend.copper-plate" not in ids
    districts["copper-plate"].update(lifecycle_state="released", construction_pending=None)
    ids = {o["option_id"] for o in ops.compile_options(board, results, recipes=recipes,
                                                      bootstrap_districts=districts)["options"]}
    assert "op.foundation.iron-plate" not in ids  # An unavailable survey cannot certify completion.


def test_current_queue_overrides_historical_shortage_and_uses_total_target():
    receipts = [{"ok": True, "operation": "mall_slot", "params": {"item": "iron-gear-wheel"}},
                {"ok": False, "operation": "foundation", "params": {"recipe": "iron-plate"},
                 "code": "material_shortage", "details": {"required": {"transport-belt": 10}, "available": {}}}]
    menu = ops.compile_options({"needs": []}, receipts, queued_targets={"inserter": 5}, material_stock={"inserter": 2})
    options = {o["option_id"]: o for o in menu["options"]}
    assert "op.produce.transport-belt" not in options
    assert options["op.produce.inserter"]["params"]["stock_target"] == 5


def test_steel_site_refusal_waits_for_layout_change():
    refusal = {"ok": False, "operation": "produce", "params": {"item": "plastic-bar"},
               "code": "steel_seed_site_wait", "state": "planned", "layout_at": (4, 8)}
    assert ops.outcome_category(refusal) == "no_geometry"
    assert ("produce", "plastic-bar") in ops.unchanged_failures([refusal], {}, layout=(4, 8))
    assert ("produce", "plastic-bar") not in ops.unchanged_failures([refusal], {}, layout=(5, 8))


def test_pending_precursors_rank_ahead_of_pending_final_item_and_new_work():
    from training.laya_guided_policy import GuidedPolicy
    rule = next(r for r in json.loads(open("docs/rl/laya_ops_guidelines.json").read())["rules"]
                if r["rule_id"] == "mission.ops-pending-first")
    menu = {"options": [{"option_id": item, "need_id": "stock:" + item, "family": "produce",
                         "product": item, "params": {}, "features": {}}
                        for item in ("inserter", "iron-gear-wheel", "plastic-bar")], "needs": []}
    ops.with_economics(menu, [], {"blocking_items": ["inserter"], "precursors": {"inserter": ["iron-gear-wheel"]}})
    rows = GuidedPolicy(3).distribution({"guidelines": [rule]}, menu)
    weights = {r["option_id"]: r["weight"] for r in rows}
    assert weights["iron-gear-wheel"] > weights["inserter"] > weights["plastic-bar"]


def test_laya_shortage_is_retained_in_the_deterministic_queue(monkeypatch):
    s = object.__new__(DeterministicSession)
    s.client = object(); s.bridge = object(); s.emit = lambda _: None; s.mall_targets = {}
    monkeypatch.setattr(b, "_MATERIAL_RESERVATION_LEDGER", None)
    monkeypatch.setattr(b, "_BLOCKING_MALL_ITEMS", set())
    for name in ("_refresh_logistic_chest_budget", "_refresh_owned_standing_gates", "_advance_bootstrap_starter_retirements"):
        monkeypatch.setattr(b, name, lambda *_a, **_k: None)
    def failed(**kwargs):
        raise b.MaterialShortage("paired_mall_lab", {"inserter": 5}, {"inserter": 2})
    s._operations = lambda: {"produce": failed}
    s._loan_batch_diagnostics = lambda *_a, **_k: None
    result = s.run("produce", item="lab")
    assert result["code"] == "material_shortage"
    assert s.mall_targets == {"inserter": 5}
    assert "inserter" in b._BLOCKING_MALL_ITEMS


def test_prepare_reconciles_caps_on_wait_turns_after_loan_restoration(monkeypatch):
    s = object.__new__(DeterministicSession)
    s.client = object(); s.bridge = object(); s.reference_point = (0, 0)
    s.emit = lambda _: None; s.mall_targets = {}; s.priorities = object()
    events = []
    monkeypatch.setattr(b, "_survey_pass", lambda *_: events.append("survey"))
    monkeypatch.setattr(b, "_release_completed_construction_loans", lambda *_: events.append("restore"))
    monkeypatch.setattr(b, "_refresh_owned_standing_gates",
                        lambda *_a, **kw: events.append(("gates", kw["rate_targets"])))
    s.set_rate_targets({"copper-cable": .5, "stone-brick": .25})
    s.prepare_decision()
    assert events == ["survey", "restore", ("gates", {"copper-cable", "stone-brick"})]


def test_shared_project_queue_uses_aggregate_bill_not_missing_quantity(monkeypatch, tmp_path):
    from orchestrator.material_reservations import MaterialReservationLedger
    from orchestrator.priority_list import PriorityList
    ledger = MaterialReservationLedger(tmp_path, episode_id="two-cells", surface="nauvis", force="player")
    ledger.declare("cell-a", {"inserter": 2}, {"inserter": 2})
    ledger.declare("cell-b", {"inserter": 3}, {"inserter": 2})
    monkeypatch.setattr(b, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(b, "_BLOCKING_MALL_ITEMS", set())
    targets = {}
    b._promote_material_bill_prerequisites({"inserter": 2}, targets, PriorityList(tmp_path / "priority.json", 0), 0)
    assert targets == {"inserter": 5}


def test_last_real_precursor_producer_cannot_be_a_batch_lender(monkeypatch):
    monkeypatch.setitem(b.LINE_RECIPES, "electric-mining-drill", {
        "ingredients": ["electronic-circuit"], "amounts": [3], "machine": "assembling-machine-1"})
    monkeypatch.setattr(b, "_MATERIAL_RESERVATION_LEDGER", None)
    monkeypatch.setattr(b.live_base, "available_items", lambda *_: {"electronic-circuit": 50})
    monkeypatch.setattr(b.live_base, "find_line", lambda *_a, **_k:
                        live_base.LineState("electronic-circuit", 1, 1, (10.5, 9.5), ((7.5, 10.5),)))
    monkeypatch.setattr(b, "locate_mall_cell", lambda *_: ((6, 9), "left"))
    monkeypatch.setattr(b, "mall_slot_uses_shared_provider", lambda *_: False)
    def entity(_c, _s, point):
        return {"name": "assembling-machine-1" if point == (7.5, 10.5)
                else "requester-chest" if point == (10.5, 10.5) else "passive-provider-chest"}
    monkeypatch.setattr(b.live_base, "entity_at", entity)
    assert b._borrow_free_mall_cell(object(), "nauvis", "player", "electric-mining-drill", (0, 0),
                                   allowed_original_recipes=frozenset({"electronic-circuit"}),
                                   allow_shared_provider=True, require_stocked_original=True,
                                   excluded_origins=set()) is None


def test_steel_seed_fallback_sites_its_single_drill_plan(monkeypatch, tmp_path):
    from orchestrator.material_reservations import MaterialReservationLedger
    ledger = MaterialReservationLedger(tmp_path, episode_id="single-seed", surface="nauvis", force="player")
    monkeypatch.setattr(b, "_MATERIAL_RESERVATION_LEDGER", ledger)
    state = SimpleNamespace(lifecycle_state="released", reservations={"growth": {(9, 9)}})
    monkeypatch.setattr(b, "_bootstrap_state", lambda _: state)
    monkeypatch.setattr(b.live_base, "occupied_tiles", lambda *_a, **_k: set())
    # A one-drill patch cannot satisfy the old two-lane starter survey.
    monkeypatch.setattr(b.live_base, "direct_plate_starter_site", lambda *_: None)
    def site(_c, _s, _f, _ore, _near, plans, **kwargs):
        assert kwargs["reserved_tiles"] == {(9, 9)}
        for plan in plans:
            assert sum(a["entity"] == "electric-mining-drill" for a in b.plan_actions(plan)) == 1
        return 2, (20.5, 20.5)
    monkeypatch.setattr(b.live_base, "mining_plan_site", site, raising=False)
    submitted = []
    monkeypatch.setattr(b, "assert_affordable", lambda *_a, **_k: None)
    monkeypatch.setattr(b, "_submit", lambda _c, _br, _s, plan, *_a, **_k: submitted.append(plan))
    monkeypatch.setattr(b, "bring_stage_up", lambda *_a, **_k: None)
    monkeypatch.setattr(b, "_ensure_power_anchor_on_generated_network", lambda *_a, **_k: None)
    monkeypatch.setattr(b, "_diagnose_machines", lambda *_a, **_k: [])
    result = b._build_compact_steel_seed(SimpleNamespace(command=lambda _: "[]"), object(),
                                      "nauvis", "player", (0, 0), (0, 0), lambda _: None)
    assert result and len(submitted) == 1
    assert sum(a["entity"] == "electric-mining-drill" for a in b.plan_actions(submitted[0])) == 1


def test_actual_mining_plan_survey_skips_reserved_patch_and_tries_the_next(tmp_path):
    import subprocess
    from planners.steel_bootstrap import steel_seed
    from orchestrator.extraction_transport import planned_footprint_tiles
    plan = steel_seed((.5, .5), "east", mined=True)
    class LuaClient:
        def command(self, command):
            prelude = """
            defines={direction={north=0,east=4,south=8,west=12},build_check_type={manual=1}}
            local rs={{name='iron-ore',position={x=.5,y=.5}},{name='iron-ore',position={x=20.5,y=.5}}}
            local s={}
            s.find_entities_filtered=function(q) if q.name then return rs else return {rs[1]} end end
            s.can_place_entity=function(q) return true end
            game={surfaces={nauvis=s},forces={player={}}};rcon={print=print}
            """
            script = tmp_path / "survey.lua"
            script.write_text(prelude + command.removeprefix("/sc "))
            return subprocess.run(["lua", str(script)], check=True, capture_output=True, text=True).stdout
    assert live_base.mining_plan_site(LuaClient(), "nauvis", "player", "iron-ore", (0, 0), [plan],
                                     reserved_tiles=planned_footprint_tiles(plan)) == (0, (20.5, .5))


@pytest.mark.parametrize("ghost_count", [0, 1])
def test_delivery_claim_retires_only_when_its_construction_demand_is_gone(monkeypatch, tmp_path, ghost_count):
    from orchestrator.material_reservations import MaterialReservationLedger
    from orchestrator.priority_list import PriorityList
    ledger = MaterialReservationLedger(tmp_path, episode_id="delivery", surface="nauvis", force="player")
    ledger.declare("deliver_electric-mining-drill", {"passive-provider-chest": 1}, {})
    ledger.declare("paired_mall_lab", {"passive-provider-chest": 1}, {})
    monkeypatch.setattr(b, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(b, "_BLOCKING_MALL_ITEMS", set())
    monkeypatch.setattr(b, "_transferable_or_available_stock", lambda *_: {})
    monkeypatch.setattr(b.live_base, "game_tick", lambda *_: 0)
    monkeypatch.setattr(b.live_base, "pending_construction_items", lambda *_: {"electric-mining-drill": ghost_count})
    monkeypatch.setattr(b, "_rationed_mall_completion_target", lambda _c, _s, _f, _i, n: n)
    monkeypatch.setattr(b, "_loan_craft_proof_complete", lambda *_a, **_k: False)
    monkeypatch.setattr(b, "active_bootstrap_loans", lambda *_: [])
    b._survey_pass(SimpleNamespace(command=lambda _: "NONE"), "nauvis", "player", {},
                   PriorityList(tmp_path / "priorities.json", 0))
    assert ledger.project_is_active("deliver_electric-mining-drill") is bool(ghost_count)
    assert ledger.project_is_active("paired_mall_lab")


def test_session_gate_maintenance_receives_full_construction_queue(monkeypatch):
    calls = []
    monkeypatch.setattr(b, "_survey_pass", lambda *_: None)
    monkeypatch.setattr(b, "_release_completed_construction_loans", lambda *_: None)
    monkeypatch.setattr(b, "_advance_bootstrap_starter_retirements", lambda *_: None)
    monkeypatch.setattr(b, "_refresh_logistic_chest_budget", lambda *_: None)
    monkeypatch.setattr(b, "_refresh_owned_standing_gates", lambda *_a, **kw: calls.append(kw))
    monkeypatch.setattr(b, "_MATERIAL_RESERVATION_LEDGER", None)
    session = DeterministicSession.__new__(DeterministicSession)
    session.client, session.bridge, session.emit = object(), object(), lambda _: None
    session.reference_point, session.priorities = (0, 0), object()
    session.mall_targets = {"electronic-circuit": 18}
    session.rate_targets = {"copper-cable"}
    monkeypatch.setattr(session, "_operations", lambda: {"reserve_steel_seed": lambda: True})
    session.prepare_decision()
    session.run("reserve_steel_seed")
    assert len(calls) == 2
    assert all(c["requested"] == {"electronic-circuit": 18} for c in calls)
    assert all(c["rate_targets"] == {"copper-cable"} for c in calls)


def test_maintenance_services_other_completed_loans_while_one_craft_finishes(monkeypatch):
    from orchestrator.stage_services import StuckError
    loans=[SimpleNamespace(target_item=i,production_target=1,target_count=1) for i in ('inserter','electric-mining-drill')]
    monkeypatch.setattr(b,'active_bootstrap_loans',lambda *a:loans)
    monkeypatch.setattr(b,'_transferable_or_available_stock',lambda *a:{'inserter':1,'electric-mining-drill':1})
    monkeypatch.setattr(b,'_bootstrap_loan_minimum_fulfilled',lambda *a:True)
    monkeypatch.setattr(b,'_bootstrap_loan_products_finished',lambda *a:1)
    serviced=[]
    def submit(c,bridge,s,f,loan,*a,**k):
        serviced.append(loan.target_item)
        if loan.target_item=='inserter':
            raise StuckError('waiting for craft',code='craft_boundary_wait',state='producing')
    monkeypatch.setattr(b,'_submit_bootstrap_loan',submit)
    messages=[]
    b._release_completed_construction_loans(object(),object(),'private','player',(0,0),messages.append)
    assert serviced==['inserter','electric-mining-drill']
    assert any('waiting for craft' in m for m in messages)
    def unexpected(*a,**k):
        raise StuckError('unexpected adapter failure',code='broken_contract')
    monkeypatch.setattr(b,'_submit_bootstrap_loan',unexpected)
    with pytest.raises(StuckError,match='unexpected adapter'):
        b._release_completed_construction_loans(object(),object(),'private','player',(0,0),lambda _:None)


def test_disconnected_logistic_gate_is_repaired_before_disabled_classification(monkeypatch):
    existing=SimpleNamespace(machine_count=1,working_count=0,machine_positions=((63.5,48.5),),output_position=(63.5,44.5))
    plan=SimpleNamespace(existing=existing)
    client=SimpleNamespace(command=lambda _:None)
    monkeypatch.setattr(b.live_base,'entity_statuses',lambda *a:{(63.5,48.5):'disabled_by_control_behavior'})
    covered=[]
    monkeypatch.setattr(b,'extend_roboport_coverage',lambda *a,**kw:(covered.append((a[4],kw['purpose'])) or False))
    assert b._repair_stalled_line(client,object(),'nauvis','player','inserter',(3,-1),lambda _:None,
                                 plan,mall_provider=(63.5,44.5),upgrade_bootstrap=False)==(63.5,44.5)
    assert covered==[((63.5,48.5),'logistic')]


def test_borrower_census_is_one_read_and_keeps_the_last_feedstock_anchor(monkeypatch):
    """Large live catalogs must not create hundreds of per-recipe RCON probes."""
    records=[{'recipe':'iron-gear-wheel','x':7.5,'y':10.5,'working':False,'made':20},
             {'recipe':'iron-gear-wheel','x':7.5,'y':16.5,'working':True,'made':30},
             {'recipe':'copper-cable','x':7.5,'y':22.5,'working':True,'made':40}]
    commands=[]
    client=SimpleNamespace(command=lambda lua:commands.append(lua) or json.dumps(records))
    monkeypatch.setattr(b.live_base,'available_items',lambda *_:{'iron-gear-wheel':100,'copper-cable':100})
    monkeypatch.setattr(b.live_base,'find_line',lambda *a,**k: (_ for _ in ()).throw(AssertionError('per-recipe census')))
    monkeypatch.setattr(b,'locate_mall_cell',lambda point,_:((6,int(point[1]-1.5)),'left'))
    monkeypatch.setattr(b,'mall_slot_uses_shared_provider',lambda *_:False)
    def entity(_c,_s,point):
        if point[0]==7.5:
            return {'name':'assembling-machine-2' if point[1]==16.5 else 'assembling-machine-1'}
        return {'name':'requester-chest' if point[1] in (10.5,16.5,22.5) else 'passive-provider-chest'}
    monkeypatch.setattr(b.live_base,'entity_at',entity)
    selected=b._borrow_free_mall_cell(client,'private','player','steel-chest',(0,0),
        allowed_original_recipes=None,allow_shared_provider=True,require_stocked_original=True,
        excluded_origins=set(),batch_target=1)
    assert selected[0]=='iron-gear-wheel' and selected[-1] in live_base.ASSEMBLER_TIERS
    assert len(commands)==1  # The one real copper source remains protected.
    census=live_base.assembler_line_census(client,'private','player')
    assert census['iron-gear-wheel'].machine_count==2
    assert census['iron-gear-wheel'].working_count==1
    assert census['iron-gear-wheel'].produced_count==50
