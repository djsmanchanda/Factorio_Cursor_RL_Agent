# Path: tests/test_research_lab_construction.py | Purpose: Protect funded lab delivery and exact construction resumption.
from types import SimpleNamespace

import pytest

from orchestrator import stage_research as stage
from orchestrator.execution_slice import execution_slice
from orchestrator.material_reservations import plan_material_bill
from planners.infrastructure import strip_local_power
from planners.local_layout_planner import LocalLayoutPlanner
from planners.plan_validation import validate_no_collisions
from training.laya_ops import DeterministicSession
from orchestrator.autonomous_builder import ProductionPrerequisiteDeferred

pytestmark=pytest.mark.fast
PACKS=('automation-science-pack','logistic-science-pack','chemical-science-pack','production-science-pack')


def test_logistic_lab_row_uses_real_chests_and_no_gifted_science_or_power():
    plan=strip_local_power(LocalLayoutPlanner().generate_lab_row(4,science_packs=PACKS))
    validate_no_collisions([('research_labs',plan)])
    bill=plan_material_bill(plan)
    assert bill['lab']==4 and bill['requester-chest']==4 and bill['fast-inserter']==4
    assert not {'electric-energy-interface','infinity-chest','transport-belt'} & set(bill)
    chests=[a for p in plan['phases'] for a in p['actions'] if a['entity']=='requester-chest']
    assert all({r['name'] for r in a['logistic_sections'][0]['requests']}==set(PACKS) for a in chests)


def test_lab_row_waits_for_real_bots_and_restores_exact_plan_before_enabling_requests(monkeypatch):
    state={};built={};submitted=[];sites=[]
    def site(*a,**kw):sites.append(1);return (0,0)
    monkeypatch.setattr(stage.live_base,'find_clear_area',site)
    monkeypatch.setattr(stage.live_base,'entity_names_at',lambda *a:dict(built))
    monkeypatch.setattr(stage.live_base,'logistic_network_ids',lambda c,s,points:{p:1 for p in points})
    monkeypatch.setattr(stage,'extend_roboport_coverage',lambda *a,**kw:False)
    monkeypatch.setattr(stage,'extend_power',lambda *a,**kw:True)
    monkeypatch.setattr(stage,'_submit',lambda c,b,s,p,name,emit,**kw:submitted.append((name,p,kw)))
    with execution_slice(checkpoint=state):
        with pytest.raises(stage.StuckError) as wait:
            stage.build_research_labs(object(),object(),'private','player',(0,0),PACKS,lambda _:None)
        assert wait.value.code=='infrastructure_construction_wait'
        assert not any(name=='research_science_requests' for name,p,kw in submitted)
        initial=submitted[0]
        assert initial[2]['require_funded'] is True
        assert all('logistic_sections' not in a for p in initial[1]['phases'] for a in p['actions'])
        for phase in state['research_lab_plan']['plan']['phases']:
            for a in phase['actions']:built[(a['position']['x'],a['position']['y'])]=a['entity']
        result=stage.build_research_labs(object(),object(),'private','player',(0,0),PACKS,lambda _:None)
    assert result['labs']==4 and sites==[1]
    assert submitted[-1][0]=='research_science_requests'
    assert all(a['action_type']=='configure_entity' for p in submitted[-2][1]['phases'] for a in p['actions'])


def test_lab_operation_refuses_to_build_before_both_observed_chemical_products():
    session=object.__new__(DeterministicSession)
    for admission in [(),('C4',),('C6',)]:
        session.reached_phases=admission
        with pytest.raises(ProductionPrerequisiteDeferred,match='plastic and advanced-circuit output'):
            session._build_research_labs(PACKS)
