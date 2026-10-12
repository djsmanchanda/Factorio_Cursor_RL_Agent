# Path: tests/test_seed_vertical_mall.py
# Purpose: Mall slots 5-8 reuse the seed pairs' chests (north/south) before a third left/right cell opens.

from __future__ import annotations

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator import mall_builder as mall
from planners.mall_layout import generate_paired_mall_layout
from planners.plan_validation import validate_no_collisions
from planners.quad_mall_layout import generate_quad_mall_layout, generate_seed_vertical_slot
from planners.recipe_data import LINE_RECIPES

REFERENCE = (3.0, -1.0)
SEED_A, SEED_B = mall._cell_origins(REFERENCE)[:2]


def _vertical(recipe: str, origin, side: str) -> dict:
    spec = LINE_RECIPES[recipe]
    return generate_seed_vertical_slot(
        recipe, spec["machine"], spec["ingredients"], spec["amounts"],
        mall.seed_vertical_center(origin), side, craft_time=spec["craft_time"],
    )


def _paired(recipe: str, origin, side: str) -> dict:
    spec = LINE_RECIPES[recipe]
    return generate_paired_mall_layout(
        recipe, spec["machine"], spec["ingredients"], spec["amounts"], origin, side,
        craft_time=spec["craft_time"],
        include_substation=mall.seed_cell_includes_substation(REFERENCE, origin, side),
        substation_position=mall.seed_shared_substation(REFERENCE),
    )


def test_vertical_slots_fit_the_seed_pairs_and_the_third_module_without_collision():
    def without_shared_requester(plan):  # the right half reuses the left half's requester
        return {"phases": [{**phase, "actions": [a for a in phase["actions"] if a["entity"] != "requester-chest"]}
                           for phase in plan["phases"]]}
    plans = [("iron-gear-wheel", _paired("iron-gear-wheel", origin, "left")) for origin in (SEED_A, SEED_B)]
    plans += [("copper-cable", without_shared_requester(_paired("copper-cable", origin, "right")))
              for origin in (SEED_A, SEED_B)]
    plans += [("pipe", _vertical("pipe", origin, side))
              for origin in (SEED_A, SEED_B) for side in ("north", "south")]
    spec = LINE_RECIPES["copper-cable"]
    third_module = mall.dense_mall_module_centers(REFERENCE)[2]
    plans += [("copper-cable", generate_quad_mall_layout(
        "copper-cable", spec["machine"], spec["ingredients"], spec["amounts"], third_module, group,
        craft_time=spec["craft_time"], support_inserter_type="long-handed-inserter"))
        for group in ("top", "bottom")]

    validate_no_collisions(plans)


def test_vertical_slot_reuses_the_cell_requester_and_provider():
    actions = _vertical("pipe", SEED_A, "north")["phases"][0]["actions"]
    configure = actions[0]
    assert configure["action_type"] == "configure_entity"
    assert (configure["position"]["x"], configure["position"]["y"]) == mall.seed_vertical_center(SEED_A)
    assert configure["logistic_sections"][0]["group"] == "mall:pipe:north"
    placed = {a["entity"] for a in actions if a["action_type"] in {"place_entity", "place_ghost"}}
    assert placed == {LINE_RECIPES["pipe"]["machine"], "long-handed-inserter"}
    # The bill is one machine and two long-handed transfers plus one craft: no chest or substation.
    bill = mall.compact_mall_project_bill("pipe", side="north", machine_name="assembling-machine-1")
    assert bill["assembling-machine-1"] == 1 and bill["long-handed-inserter"] == 2
    assert not {"passive-provider-chest", "requester-chest", "substation"} & set(bill)


@pytest.fixture
def full_seed_pairs(monkeypatch):
    """Both seed pairs hold left/right machines; their north/south and every other cell are empty."""
    occupied: set = set()

    def district(_c, _s, origins):
        return {o: (("iron-gear-wheel", "copper-cable", "requester-chest") if o in {SEED_A, SEED_B}
                    else ("-", "-", "-")) for o in origins}

    def quad(_c, _s, center):
        top = "pipe" if (center, "north") in occupied else "-"
        bottom = "pipe" if (center, "south") in occupied else "-"
        requester = "requester-chest" if center in {mall.seed_vertical_center(SEED_A),
                                                     mall.seed_vertical_center(SEED_B)} else "-"
        return top, bottom, requester

    monkeypatch.setattr(mall, "_district_state", district)
    monkeypatch.setattr(mall, "_quad_mall_state", quad)
    monkeypatch.setattr(mall.live_base, "area_clear", lambda *_a: True)
    monkeypatch.setattr(mall.live_base, "entity_at", lambda *_a: None)
    monkeypatch.setattr(mall.resource_patches, "box_has_reserved_patch", lambda *_a: False)
    return occupied


def test_fifth_slot_uses_a_seed_north_slot_before_a_third_cell(full_seed_pairs):
    assert mall._choose_slot(object(), "nauvis", "pipe", REFERENCE, vertical_ok=True) == (SEED_A, "north")
    full_seed_pairs.add((mall.seed_vertical_center(SEED_A), "north"))
    assert mall._choose_slot(object(), "nauvis", "pipe", REFERENCE, vertical_ok=True) == (SEED_A, "south")
    for origin in (SEED_A, SEED_B):
        for side in ("north", "south"):
            full_seed_pairs.add((mall.seed_vertical_center(origin), side))
    # Only once every seed north/south slot is taken does a third left/right cell open.
    origin, side = mall._choose_slot(object(), "nauvis", "pipe", REFERENCE, vertical_ok=True)
    assert origin not in {SEED_A, SEED_B} and side == "left"
    assert not mall.seed_vertical_slots_open(object(), "nauvis", REFERENCE)


def test_without_long_handed_capability_the_third_cell_is_used(full_seed_pairs):
    origin, side = mall._choose_slot(object(), "nauvis", "pipe", REFERENCE, vertical_ok=False)
    assert origin not in {SEED_A, SEED_B} and side == "left"


def test_seed_vertical_machine_counts_as_one_slot(full_seed_pairs, monkeypatch):
    full_seed_pairs.add((mall.seed_vertical_center(SEED_A), "north"))

    assert mall.quad_mall_slot_count(object(), "nauvis", REFERENCE) == 1
    assert mall.mall_slot_count(object(), "nauvis", REFERENCE) == 5


def test_dense_bay_waits_for_open_seed_vertical_slots(monkeypatch):
    class Client:
        def command(self, _text):
            return ""
    monkeypatch.setattr(builder, "_dense_bootstrap_mall_enabled", lambda *_a: True)
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 4)
    monkeypatch.setattr(builder, "seed_vertical_slots_open", lambda *_a: True)
    assert not builder._quad_bootstrap_mall_enabled(Client(), "nauvis", "player", REFERENCE, "pipe")
    monkeypatch.setattr(builder, "seed_vertical_slots_open", lambda *_a: False)
    assert builder._quad_bootstrap_mall_enabled(Client(), "nauvis", "player", REFERENCE, "pipe")


@pytest.mark.parametrize(("item", "stock", "producing", "expected"), [
    ("pipe", 2, False, True), ("pipe", 0, True, True), ("pipe", 1, False, False),
    ("long-handed-inserter", 9, True, False),
])
def test_vertical_slot_needs_long_handed_supply(monkeypatch, item, stock, producing, expected):
    class Client:
        def command(self, _text):
            return ""
    monkeypatch.setitem(LINE_RECIPES, "long-handed-inserter", LINE_RECIPES.get("long-handed-inserter") or {
        "machine": "assembling-machine-1", "ingredients": ["iron-plate"], "amounts": [1], "craft_time": 0.5})
    monkeypatch.setattr(builder, "_transferable_or_available_stock", lambda *_a: {"long-handed-inserter": stock})
    monkeypatch.setattr(builder, "_production_started", lambda *_a: producing)

    assert builder._seed_vertical_mall_slot_ok(Client(), "nauvis", "player", item) is expected


def test_supplied_blueprint_repeats_six_crosses_and_expansion_keeps_clearance():
    import json
    from pathlib import Path
    fixture=json.loads((Path(__file__).parent/'fixtures/user_repeating_cross_mall.json').read_text())
    reference=tuple(fixture['reference'])
    origins=mall._cell_origins(reference)
    centres=mall.dense_mall_module_centers(reference)
    supplied={tuple(e['position'].values()) for e in fixture['entities'] if e['name']=='requester-chest'}
    assert set(centres[:6]) == supplied
    plans=[]
    expected=set()
    for origin in origins:
        for side in ('left','right'):
            spec=LINE_RECIPES['iron-gear-wheel']
            p=generate_paired_mall_layout('iron-gear-wheel','assembling-machine-1',spec['ingredients'],spec['amounts'],
                origin,side,craft_time=spec['craft_time'],include_substation=mall.seed_cell_includes_substation(reference,origin,side),
                substation_position=mall.reserved_substation_position(reference,origin))
            if side=='right':
                p['phases'][0]['actions']=[a for a in p['phases'][0]['actions'] if a['entity']!='requester-chest']
            plans.append(('iron-gear-wheel',p))
        for side in ('north','south'):
            plans.append(('iron-gear-wheel',generate_seed_vertical_slot('iron-gear-wheel','assembling-machine-1',
                spec['ingredients'],spec['amounts'],mall.seed_vertical_center(origin),side,craft_time=spec['craft_time'])))
        if origin in origins[:6]:
            for _,plan in plans[-4:]:
                for phase in plan['phases']:
                    for a in phase['actions']:
                        if a['entity']!='substation' and a['action_type'].startswith('place'):
                            expected.add((a['entity'],a['position']['x'],a['position']['y'],a.get('direction')))
    directions={0:'north',4:'east',8:'south',12:'west'}
    actual={(e['name'],e['position']['x'],e['position']['y'],
             directions.get(e.get('direction',0)) if 'inserter' in e['name'] else None)
            for e in fixture['entities'] if e['name']!='substation'}
    assert actual==expected
    validate_no_collisions(plans)


def test_vertical_order_repeats_after_the_opening_modules(full_seed_pairs, monkeypatch):
    origins=mall._cell_origins(REFERENCE)
    later=origins[2]
    states={o:('iron-gear-wheel','copper-cable','requester-chest') for o in origins}
    vertical={o:('pipe','pipe') for o in origins}
    vertical[later]=('-','-')
    monkeypatch.setattr(mall,'_district_state',lambda *a: states)
    monkeypatch.setattr(mall,'_vertical_mall_states',lambda *a: vertical)
    assert mall._choose_slot(object(),'nauvis','pipe',REFERENCE,vertical_ok=True)==(later,'north')
    vertical[later]=('pipe','-')
    assert mall._choose_slot(object(),'nauvis','pipe',REFERENCE,vertical_ok=True)==(later,'south')


def test_preflight_claims_empty_vertical_halves_before_ghost_submission(full_seed_pairs):
    from orchestrator.execution_slice import execution_slice
    runtime={}
    first, second={}, {}
    with execution_slice(checkpoint=first, runtime=runtime):
        assert mall.preview_mall_allocation(object(),'nauvis','inserter',REFERENCE,vertical_ok=True)==(SEED_A,'north')
    with execution_slice(checkpoint=second, runtime=runtime):
        assert mall.preview_mall_allocation(object(),'nauvis','splitter',REFERENCE,vertical_ok=True)==(SEED_A,'south')
    assert first['mall_allocation:nauvis:player:inserter']==[list(SEED_A),'north']
    assert second['mall_allocation:nauvis:player:splitter']==[list(SEED_A),'south']


def test_vertical_submission_uses_the_preflight_owner(monkeypatch):
    names=[]
    monkeypatch.setattr(mall,'assert_mall_cell_footprint_free',lambda *a:None)
    monkeypatch.setattr(mall,'_submit',lambda c,b,s,p,name,emit:names.append(name))
    mall._build_seed_vertical_slot(object(),object(),'nauvis','player','inserter',{},REFERENCE,
                                  lambda *a,**kw:None,lambda _:None,SEED_A,'north')
    assert names[0]=='paired_mall_inserter'
    assert not any(name.startswith('seed_vertical_mall_') for name in names)


def test_vertical_bringup_covers_gated_machine_beyond_covered_requester(monkeypatch):
    covered=[]
    monkeypatch.setattr(mall,'assert_mall_cell_footprint_free',lambda *a:None)
    monkeypatch.setattr(mall,'_submit',lambda *a,**kw:None)
    def bring(*args,**kwargs):covered.extend(kwargs['logistic_chest_positions'])
    mall._build_seed_vertical_slot(object(),object(),'nauvis','player','inserter',{},REFERENCE,
                                  bring,lambda _:None,(59,42),'south',stock_gate_target=28)
    assert (63.5,43.5) in covered and (63.5,48.5) in covered
    # Live boundary: requester is served by (52,20); its machine is outside25.
    assert max(abs(63.5-52),abs(43.5-20))<25
    assert max(abs(63.5-52),abs(48.5-20))>25


@pytest.mark.parametrize('machine,provider', [((63.5,38.5),(63.5,42.5)),((63.5,48.5),(63.5,44.5))])
def test_vertical_provider_is_recognized_from_declared_cross_offsets(monkeypatch,machine,provider):
    entities={(63.5,43.5):{'name':'requester-chest'},provider:{'name':'passive-provider-chest'}}
    monkeypatch.setattr(builder.live_base,'entity_at',lambda c,s,p:entities.get(p))
    assert builder._paired_mall_provider(object(),'nauvis',[machine])==provider
    entities.pop((63.5,43.5))
    assert builder._paired_mall_provider(object(),'nauvis',[machine]) is None


def test_capacity_job_advances_from_configured_half_but_preserves_its_unfinished_half():
    from orchestrator.execution_slice import execution_slice
    origin=(59,42)
    key='mall_allocation:nauvis:player:long-handed-inserter'
    state={key:[list(origin),'south']}
    runtime={'mall_allocations':{'nauvis:player:59:42:south':'long-handed-inserter'}}
    with execution_slice(checkpoint=state,runtime=runtime):
        mall.advance_completed_mall_allocation('nauvis','player','long-handed-inserter',[(60.5,38.5)])
        assert key in state  # Another producer does not complete this half's ghosts.
        mall.advance_completed_mall_allocation('nauvis','player','long-handed-inserter',[(63.5,48.5)])
        assert key not in state
        assert runtime['mall_allocations']=={}


def test_capacity_reservation_advances_completed_slot_before_pricing_next_kit(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from orchestrator.material_reservations import MaterialReservationLedger
    from orchestrator.execution_slice import execution_slice
    ledger=MaterialReservationLedger(tmp_path,episode_id='capacity',surface='nauvis',force='player')
    monkeypatch.setattr(builder,'_MATERIAL_RESERVATION_LEDGER',ledger)
    monkeypatch.setattr(builder,'_independent_mall_ready',lambda *a:False)
    monkeypatch.setattr(builder,'_quad_bootstrap_mall_enabled',lambda *a:False)
    monkeypatch.setattr(builder,'_seed_vertical_mall_slot_ok',lambda *a:True)
    stock={'assembling-machine-1':1,'long-handed-inserter':2,'inserter':1,'iron-gear-wheel':1,'iron-plate':1}
    monkeypatch.setattr(builder,'compact_mall_project_bill',lambda *a,**kw:stock)
    monkeypatch.setattr(builder.live_base,'transferable_items',lambda *a:stock)
    monkeypatch.setattr(builder,'_material_sources_and_rates',lambda *a:({},{}))
    key='mall_allocation:nauvis:player:long-handed-inserter'
    state={key:[[59,42],'south']}
    def preview(*a,**kw):
        assert key not in state
        return ((50,47),'south')
    monkeypatch.setattr(builder,'preview_mall_allocation',preview)
    plan=SimpleNamespace(existing=SimpleNamespace(machine_positions=((63.5,48.5),)),at_size=False,
                         mall_storage_limit=35,fill_provider=False,mall_request_multiplier=None)
    with execution_slice(checkpoint=state):
        builder._reserve_compact_mall_project(object(),'nauvis','player','long-handed-inserter',plan,35,
                                             lambda _:None,reference_point=REFERENCE)
    assert ledger.projects['paired_mall_long-handed-inserter'].state=='ready'
