# Path: tests/test_laya_mall_growth.py | Purpose: Protect exact growth budgets, finite reward shaping, hardware legality and seeded policy learning.
from copy import deepcopy
import numpy as np
import pytest
from training.laya_mall import scenario,compile_candidates
from training.laya_mall_supply import MALL_KIT,new_supply
from training.laya_mall_growth import reward,manufacturing_potential
from training.laya_mall_population import action_vector,initial_population,evolve,choose
from tests.test_laya_mall import facts
from planners.laya_quad_cell import generate_quad_slot,quad_reservation_reason


def test_latest_kit_distance_and_steel_bootstrap_are_declared_without_item_grants():
    sc=scenario(15,'growth')
    assert MALL_KIT=={'requester-chest':6,'passive-provider-chest':12,'assembling-machine-1':4,'inserter':8}
    starter=next(f for f in sc['fixtures'] if f['id']=='starter')['position'];center=sc['centers'][0]
    assert starter['x']==center['x']-15 and starter['y']==center['y']
    assert {r['item'] for r in sc['resources']}=={'stone','coal'}


def test_earlier_physical_supply_upgrade_beats_later_and_fixture_inputs_are_not_production():
    before=facts();after=deepcopy(before);after['tick']+=600;after['elapsed_ticks']=600
    after['ledger']['upgrades']=1;after['ledger']['rate_halves_per_second']['iron-plate']=5
    early=reward(before,after,upgrade_tick=before['tick'])
    late=reward(before,after,upgrade_tick=after['tick']-60)
    assert early['reward']>late['reward'] and early['earned_supply_area']==pytest.approx(.025)
    after=deepcopy(before);after['tick']+=600;after['ledger']['fixture_delivered']={'iron-plate':999,'copper-plate':999}
    assert reward(before,after)['reward']==0
    after['viewing_only']=True
    with pytest.raises(RuntimeError,match='viewer'):reward(before,after)


def test_infinite_intermediate_cable_has_bounded_progress_credit():
    f=facts();f['production']['copper-cable']=100000
    potential=manufacturing_potential(f)
    f['production']['copper-cable']=200000
    assert manufacturing_potential(f)==potential and potential<1


def test_am2_and_fast_inserters_preserve_quad_slots_and_long_arm_requirement():
    f=facts();center=f['scenario']['centers'][0];spec=f['recipes']['iron-gear-wheel']
    plan={'phases':[{'actions':generate_quad_slot('iron-gear-wheel',spec,(center['x'],center['y']),'north',machine='assembling-machine-2')}]}
    assert quad_reservation_reason(plan,[center]) is None
    for a in plan['phases'][0]['actions']:
        if a['entity']=='long-handed-inserter':a['entity']='fast-inserter'
    assert quad_reservation_reason(plan,[center]) is not None


def test_population_retains_elites_and_seeded_diversity_without_rule_based_choices():
    f=facts();candidates=[c for c in compile_candidates(f) if c['family']=='wait'];width=len(action_vector(f,candidates[0]))
    population=initial_population(width,5);next_pop=evolve(population,list(range(20)),7)
    assert len(next_pop)==20 and next_pop[0]==population[19]
    assert len({tuple(w) for w in next_pop})==20
    assert next_pop==evolve(population,list(range(20)),7)
    assert choose(population[0],f,candidates,np.random.default_rng(8)) in (0,1)


def test_burner_furnace_allowance_is_explicit_and_cannot_escape_training_scope():
    from planners.plan_validation import validate_build_plan
    plan={'surface':'laya-mall/test','force':'laya-mall-test','atomic':True,'phases':[{'name':'steel','actions':[
        {'action_type':'place_ghost','entity':'stone-furnace','position':{'x':0,'y':13}}]}]}
    with pytest.raises(ValueError,match='Electric-only'):validate_build_plan(plan)
    validate_build_plan(plan,allow_training_fuel=True)
    plan['surface']='nauvis';plan['force']='player'
    with pytest.raises(ValueError,match='disposable mall'):validate_build_plan(plan,allow_training_fuel=True)
