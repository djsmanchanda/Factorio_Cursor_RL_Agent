# Path: tests/test_laya_research.py | Purpose: Protect level-aware research completion, useful throughput ramp and funded operation boundaries.
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from training.laya_research import ResearchObjective
from orchestrator.research_operations import research_observation, select_research
from planners.research_labs import logistic_lab_row
from planners.plan_validation import validate_no_collisions
from orchestrator.work_state import WorkStateSignal

PACK = "automation-science-pack"


def reports(tick=100, *, level=4, complete=False):
    tech = {"name": "mining-productivity-3", "canonical_name": "mining-productivity-3",
            "requested_name": "mining-productivity-4", "requested_level": 4, "current_level": level,
            "target_completed": complete, "state": "completed" if complete else "current",
            "researched": complete and level == 4, "enabled": True, "science_packs": {PACK: 1},
            "research_unit_count": 1000}
    science = json.loads((Path(__file__).parent / "fixtures/science_status/science_progressing_start.json").read_text())
    science["tick"] = tick
    science["research"].update(current="mining-productivity-3", progress=0, queue=["mining-productivity-3"])
    return {"research": {"ok": True, "force": "player", "tick": tick, "technology": tech,
                         "current_research": "mining-productivity-3", "research_progress": 0,
                         "research_queue": ["mining-productivity-3"]}, "science": science}


def pair(tick, *, made=0, consumed=0, progress=0, complete=False, inventory=10):
    raw = reports(tick, complete=complete)
    raw["research"]["research_progress"] = progress
    raw["science"]["labs"]["input_inventory"][PACK] = inventory
    observed = research_observation(raw, tick=tick, surface="nauvis", force="player", requested="mining-productivity-4")
    facts = {"tick": tick, "surface": "nauvis", "force": "player", "mods": {"base": "2.1.23"},
             "production": {PACK: made}, "consumption": {PACK: consumed}, "networks": {},
             "infrastructure_counts": {"lab": 1},
             "recipe_observations": {PACK: {"enabled": True, "ingredients": [{"name": "iron-plate"}], "machine_speeds": {}}}}
    return observed, facts


def test_unchanged_science_reports_keep_fresh_research_evidence(tmp_path, monkeypatch):
    from orchestrator.game_bridge import GameBridge, load_json

    monkeypatch.setattr("orchestrator.game_bridge.RconClient", lambda *a, **kw: object())
    bridge = GameBridge(tmp_path, retain_reports=2)
    directory = tmp_path / "factorio_mod/science_reports"
    directory.mkdir(parents=True)
    objective = ResearchObjective()
    for tick in (100, 1000, 2000):
        raw = reports(tick)
        path = directory / f"science_status_{tick}.json"

        def command(text):
            assert text.startswith("/science_status ")
            path.write_text(json.dumps(raw["science"]))
            return ""

        monkeypatch.setattr(bridge, "command", command)
        raw["science"] = load_json(bridge.science_status("nauvis", "player"))
        observation = research_observation(raw, tick=tick, surface="nauvis", force="player",
                                           requested="mining-productivity-4")
        _, facts = pair(tick)
        evidence = objective.observe(observation, facts)
        assert evidence["available"], evidence["reason"]
        assert evidence["previous"]["tick"] == tick
        assert not evidence["earned"]
        assert len(list(directory.glob("*.json"))) <= 2


@pytest.mark.parametrize("mutation", ["stale", "force", "level", "identity"])
def test_research_report_fails_closed(mutation):
    raw = reports()
    if mutation == "stale": raw["research"]["tick"] = 0
    if mutation == "force": raw["research"]["force"] = "other"
    if mutation == "level": raw["research"]["technology"]["requested_level"] = 3
    if mutation == "identity": raw["research"]["technology"]["canonical_name"] = "unrelated"
    result = research_observation(raw, tick=500 if mutation == "stale" else 100,
                                  surface="nauvis", force="player", requested="mining-productivity-4")
    assert result["available"] is False


def test_science_production_alone_and_precompleted_start_cannot_earn_research():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    result = objective.observe(*pair(18000, made=10000, consumed=0), service_ok=True)
    assert not result["earned"] and result["target"] == 0.25
    precompleted = ResearchObjective()
    precompleted.observe(*pair(0, complete=True), service_ok=True)
    result = precompleted.observe(*pair(18000, made=100, consumed=100, complete=True), service_ok=True)
    assert not result["earned"] and result["ineligible_start"]


def test_progressive_target_requires_useful_consumed_window_and_restores_exactly():
    objective = ResearchObjective(sustain_ticks=600)
    objective.observe(*pair(0), service_ok=True)
    restored = ResearchObjective(sustain_ticks=600, state=json.loads(json.dumps(objective.snapshot())))
    data = pair(600, made=3, consumed=3, progress=.003)
    result = objective.observe(*data, service_ok=True)
    assert result == restored.observe(*data, service_ok=True)
    assert result["target"] == .5 and result["achieved"] == .25 and result["advanced"]
    assert not result["earned"] and result["research_units"] == 3
    # Changing demand neither manufactures science nor increments useful research.
    repeat = objective.observe(*data, service_ok=True)
    assert repeat["research_units"] == 3 and len(repeat["rungs"]) == 1


@pytest.mark.parametrize("condition", ["inventory_depleted", "service_lost", "fault", "not_consumed", "alternate_consumer"])
def test_ramp_does_not_advance_from_depletion_faults_or_unconsumed_stock(condition):
    objective = ResearchObjective(sustain_ticks=600)
    objective.observe(*pair(0), service_ok=True)
    obs, facts = pair(600, made=3, consumed=3, progress=.003)
    if condition == "inventory_depleted": obs["labs"]["input_inventory"][PACK] = 0
    if condition == "not_consumed": facts["consumption"][PACK] = 0
    if condition == "alternate_consumer": facts["infrastructure_counts"]["recycler"] = 1
    result = objective.observe(obs, facts, service_ok=condition != "service_lost", clean=condition != "fault")
    assert result["target"] == .25 and result["achieved"] == 0


def test_completed_requested_level_needs_fresh_factory_earned_supply():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    result = objective.observe(*pair(600, made=10, consumed=10, complete=True), service_ok=True)
    assert result["earned"] and result["reason"] == "requested_level_completed_with_earned_supply"
    assert result["requested"] == "mining-productivity-4"


def test_funded_lab_geometry_has_no_scaffolding_or_item_grants():
    plan = logistic_lab_row(6, {PACK: 1, "chemical-science-pack": 1})
    validate_no_collisions([("labs", plan)])
    actions = plan["phases"][0]["actions"]
    assert all(a["action_type"] == "place_ghost" for a in actions)
    assert sum(a["entity"] == "lab" for a in actions) == 6
    assert not any(a["entity"] in {"electric-energy-interface", "substation"} for a in actions)
    assert all(a["logistic_sections"][0]["requests"] for a in actions if a["entity"] == "requester-chest")


def test_research_selection_is_guarded_idempotent_and_checks_readback(monkeypatch):
    import orchestrator.research_operations as module
    monkeypatch.setattr(module, "load_json", lambda value: value)
    before = reports()["research"]
    before.update(current_research=None, research_queue=[])
    before["technology"]["state"] = "available"
    calls = []
    def select(technology, **kwargs):
        calls.append(technology)
        before.update(current_research="mining-productivity-3", research_queue=["mining-productivity-3"])
        return {"ok": True, "force": "player", "technology": technology, "canonical_name": "mining-productivity-3", "tick": 100}
    bridge = SimpleNamespace(research_status=lambda **_: copy.deepcopy(before), set_research=select)
    outcome = select_research(bridge, force="player", technology="mining-productivity-4", expected_current=None, expected_queue=[])
    assert outcome["verified"] and outcome["submitted"]
    result = select_research(bridge, force="player", technology="mining-productivity-4",
                             expected_current="mining-productivity-3", expected_queue=["mining-productivity-3"])
    assert result["idempotent"] and calls == ["mining-productivity-4"]
    with pytest.raises(WorkStateSignal) as error:
        select_research(bridge, force="player", technology="mining-productivity-4", expected_current=None, expected_queue=[])
    assert error.value.code == "research_queue_changed"


def test_repeatable_level_transition_counts_real_progress_across_reset():
    objective = ResearchObjective(sustain_ticks=600)
    observed, facts = pair(0, progress=.9)
    observed["technology"].update(requested_level=4, current_level=3, state="future")
    objective.observe(observed, facts, service_ok=True)
    after, facts = pair(600, progress=.01, made=100, consumed=100)
    after["technology"]["research_unit_count"] = 2000
    result = objective.observe(after, facts, service_ok=True)
    assert result["progressed"] and result["research_units"] == pytest.approx(120)
    assert not result["earned"]


def test_fixed_target_keeps_rate_while_recording_sustained_consumption():
    objective = ResearchObjective(progressive=False, sustain_ticks=600)
    objective.observe(*pair(0), service_ok=True)
    result = objective.observe(*pair(600, progress=.003, made=3, consumed=3), service_ok=True)
    assert result["target"] == .25 and result["achieved"] == .25 and not result["advanced"]


def test_science_readiness_is_separate_from_research_phase():
    from training.laya_mission import PHASES, research_phase
    reached = [p["phase_id"] for p in PHASES]
    assert research_phase(reached, {PACK: .25})["phase_id"] == "R1"
    assert research_phase(reached[:-1], {PACK: .25})["targets"] == {PACK: .25}


def test_lab_packet_retries_use_same_owned_plan_and_real_bill(tmp_path, monkeypatch):
    import orchestrator.research_operations as operation
    from orchestrator import autonomous_builder as ab
    from orchestrator.material_reservations import MaterialReservationLedger, plan_material_bill
    ledger=MaterialReservationLedger(tmp_path/'script-output',episode_id='labs',surface='nauvis',force='player')
    monkeypatch.setattr(ab,'_MATERIAL_RESERVATION_LEDGER',ledger)
    monkeypatch.setattr(ab.live_base,'find_clear_area',lambda *a,**kw:(20,20))
    submitted=[]
    monkeypatch.setattr(ab,'_submit',lambda c,b,s,p,name,emit,**kw:submitted.append((name,copy.deepcopy(p))))
    monkeypatch.setattr('orchestrator.construction_coverage._await_bot_built_infrastructure',lambda *a,**kw:None)
    monkeypatch.setattr(ab,'extend_power',lambda *a,**kw:None)
    monkeypatch.setattr(ab,'ensure_logistic_coverage',lambda *a,**kw:None)
    session=SimpleNamespace(client=object(),bridge=object(),reference_point=(0,0),emit=lambda _:None)
    first=operation.build_labs(session,count=1,packs={PACK:1})
    second=operation.build_labs(session,count=1,packs={PACK:1})
    assert first==second and len(submitted)==4
    assert submitted[:2]==submitted[2:]
    assert all(a["action_type"]=="configure_entity" for a in submitted[1][1]["phases"][0]["actions"])
    assert plan_material_bill(submitted[0][1])=={'lab':1,'requester-chest':1,'inserter':1,'medium-electric-pole':1}


def test_background_production_cannot_repeatedly_credit_one_finite_obligation():
    from training.laya_research import finite_material_progress
    memory={}
    result={'operation':'foundation','params':{'recipe':'iron-plate'},'ok':False,'code':'material_shortage',
            'details':{'stage':'owned','required':{'inserter':4},'available':{}}}
    assert finite_material_progress(memory,[result],{}, {'inserter':100})==[]
    credited=finite_material_progress(memory,[result],{}, {'inserter':103})
    assert credited[0]['credited']==3
    assert finite_material_progress(memory,[result],{}, {'inserter':10000})[0]['credited']==4
    assert finite_material_progress(memory,[result],{}, {'inserter':20000})==[]
    assert finite_material_progress(memory,[],{}, {'inserter':30000})==[]


def test_research_option_facts_follow_current_goal_and_unknown_capacity():
    from training.laya_research import add_research_options
    observed,facts=pair(100)
    menu=add_research_options({'options':[]},{'targets':{'iron-plate':.5},'needs':[]},observed,facts,ResearchObjective())
    assert all(not o['features']['goal_need'] for o in menu['options'])
    # Existing working labs with unknown capacity cannot justify unlimited growth.
    assert not any(o['family']=='research_labs' and o['option_id']!='op.research_labs.reconcile' for o in menu['options'])


# --- C96d: research observation must stay fresh when the science report does not change ---
# Recorded shape: data/laya-experiments/laya-c96d-c0-science3-2x-20261112/.../C0-science-s84200/
# server/script-output/factorio_mod/science_reports/science_status_238724.json (0 labs, no research).
C96D_SCIENCE = {"schema_version": "1.0.0", "ok": True, "surface": "nauvis", "force": "player",
                "research": {"current": None, "progress": 0, "queue": [], "current_science_packs": {},
                             "current_research_unit_count": 0},
                "labs": {"total": 0, "working": 0, "status_counts": {}, "input_inventory": {}, "entries": []}}
NIGHT_VISION = {"name": "night-vision-equipment", "canonical_name": "night-vision-equipment",
                "requested_name": "night-vision-equipment", "researched": False, "enabled": True,
                "target_completed": False, "state": "available", "current_level": 1, "prototype_level": 1,
                "max_level": 1, "research_unit_count": 50,
                "science_packs": {"automation-science-pack": 1, "logistic-science-pack": 1}}


class _FakeMod:
    """Answers bridge commands the way the mod does: one tick-stamped file per command."""

    def __init__(self, root, tick):
        self.root, self.tick = root, tick

    def __call__(self, text):
        name, _, body = text.partition(" ")
        payload = json.loads(body) if body.startswith("{") else {}
        reports = self.root / "factorio_mod"
        if name == "/research_status":
            target = dict(NIGHT_VISION, requested_name=payload["technology"])
            report = {"ok": True, "tick": self.tick, "force": payload["force"], "research_progress": 0,
                      "research_queue": [], "science_packs": {}, "technology": target,
                      "request_id": payload["request_id"]}
            path = reports / "research_reports" / f"research_status_{payload['request_id']}.json"
        elif name == "/science_status":
            report, path = dict(C96D_SCIENCE, tick=self.tick), reports / "science_reports" / f"science_status_{self.tick}.json"
        elif name == "/research_options":
            option = {"technology": "night-vision-equipment", "canonical_name": "night-vision-equipment",
                      "state": "available", "current_level": 1, "max_level": 1,
                      "science_packs": NIGHT_VISION["science_packs"]}
            report = {"ok": True, "tick": self.tick, "force": "player", "research_queue": [], "options": [option]}
            path = reports / "research_reports" / f"research_options_{self.tick}.json"
        elif name == "/sc":
            return json.dumps({"tick": self.tick, "force": "player", "canonical": "night-vision-equipment",
                               "unit_energy": 900, "lab_speed": 1, "speed_bonus": 2.5})
        else:
            raise AssertionError(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report))
        return ""


def test_unchanged_science_report_keeps_research_observation_fresh(tmp_path, monkeypatch):
    """C96d: 0 labs and no research make every science report identical except its tick. The bridge
    used to delete the new report and return the decision-1 file, so every later observation was
    'stale or from future' (1,738 of 1,745 decisions). The caller must get its own report."""
    from orchestrator.game_bridge import GameBridge
    from orchestrator.research_operations import collect_research_reports
    monkeypatch.setattr("orchestrator.game_bridge.RconClient", lambda *_a, **_kw: object())
    bridge = GameBridge(script_output=tmp_path, poll_interval=0.01)
    mod = _FakeMod(tmp_path, 238724)
    monkeypatch.setattr(bridge, "command", mod)
    target = "night-vision-equipment"
    for tick in (238724, 239470, 240216):  # recorded first report tick, then the median 746-tick decision spacing
        mod.tick = tick
        reports = collect_research_reports(bridge, surface="nauvis", force="player", requested=target)
        observed = research_observation(reports, tick=tick + 5, surface="nauvis", force="player", requested=target)
        assert observed["available"] is True, observed.get("reason")
        assert observed["report_ticks"] == {"research": tick, "science": tick}
    # The disk saving is kept: identical science reports collapse to one file, the newest.
    assert [p.name for p in (tmp_path / "factorio_mod/science_reports").glob("*.json")] == ["science_status_240216.json"]


def test_valid_unresearched_target_with_labs_offers_select_and_labs(tmp_path, monkeypatch):
    """Fake reports for a valid, unresearched target and one idle lab: the runner's own menu
    contains research_select (from the live options report) and research_labs."""
    from orchestrator.game_bridge import GameBridge
    from orchestrator.research_operations import collect_research_reports
    from training.laya_research import add_research_options
    monkeypatch.setattr("orchestrator.game_bridge.RconClient", lambda *_a, **_kw: object())
    bridge = GameBridge(script_output=tmp_path, poll_interval=0.01)
    monkeypatch.setattr(bridge, "command", _FakeMod(tmp_path, 5000))
    labs = {"total": 1, "working": 0, "status_counts": {"no_research_in_progress": 1}, "input_inventory": {},
            "entries": [{"unit_number": 7, "position": {"x": 0.5, "y": 0.5}, "status": "no_research_in_progress",
                         "input_inventory": {}}]}
    monkeypatch.setitem(C96D_SCIENCE, "labs", labs)
    target = "night-vision-equipment"
    reports = collect_research_reports(bridge, surface="nauvis", force="player", requested=target)
    observed = research_observation(reports, tick=5003, surface="nauvis", force="player", requested=target)
    assert observed["available"] is True and observed["selection_candidates"]
    facts = {"tick": 5003, "surface": "nauvis", "force": "player", "mods": {"base": "2.1.24"}}
    objective = ResearchObjective(target, progressive=False)
    menu = add_research_options({"options": []}, {"targets": objective.targets(observed), "needs": []},
                                observed, facts, objective)
    ids = {o["option_id"] for o in menu["options"]}
    assert "op.research_select.night-vision-equipment" in ids
    assert any(i.startswith("op.research_labs.") and i != "op.research_labs.reconcile" for i in ids)
    assert {o["family"] for o in menu["options"]} >= {"research_select", "research_labs"}
