# Path: tests/test_laya_experiment_context.py | Purpose: Protect experimental request identity, truthful stock-lock context and unchanged baseline settings.
from copy import deepcopy
from training.laya_experiment_context import with_request_multiplier,dependency_context
from training.laya_clean_runner import Settings,requested_items
from training.laya_variants import apply,load_variant,ROOT
import pytest


def candidate():
 return {'option_id':'assemble.old','family':'assemble','product':'long-handed-inserter','bill':{'inserter':2},
  'plan':{'phases':[{'actions':[{'entity':'requester-chest','position':{'x':0,'y':0},'logistic_sections':[{'multiplier':15,'requests':[{'name':'inserter','count':1},{'name':'iron-gear-wheel','count':1}]}]}]}]}}


def test_request_intervention_changes_identity_and_draws_without_mutating_default():
 c=candidate();copy=deepcopy(c);small=with_request_multiplier(c,1)
 assert c==copy and small['option_id']!=c['option_id']
 assert requested_items(c)['inserter']==15 and requested_items(small)['inserter']==1
 assert with_request_multiplier(small,1)==small
 with pytest.raises(ValueError):with_request_multiplier(c,0)


def test_context_reports_when_draws_block_another_currently_funded_build():
 c=candidate();other={'option_id':'gear','bill':{'inserter':2}}
 f={'networks':{'n':{'items':{'inserter':18},'reserved':{}}},'recipes':{}}
 r=dependency_context(f,[c,other],{'needs':[]})[c['option_id']]
 assert r['blocks_other_builds']==1 and r['unbacked_inputs']==['iron-gear-wheel']
 small=with_request_multiplier(c,1)
 assert dependency_context(f,[small,other],{'needs':[]})[small['option_id']]['blocks_other_builds']==0


def test_declared_variant_and_baseline_are_independent():
 baseline,_,_=apply(load_variant(None))
 small,_,_=apply(load_variant(ROOT/'docs/rl/laya_variants/context-20261004/buffer-1.json'))
 assert baseline==Settings() and baseline.request_multiplier is None and not baseline.dependency_context
 assert small.request_multiplier==1


def test_observed_productivity_changes_demand_and_capacity_without_changing_defaults():
 from training.laya_factory_status import goal_demand,machine_rate
 recipe={'ingredients':[{'name':'coal','amount':1}],'product_amount':2,'energy_ticks':60}
 assert goal_demand({'plastic-bar':2},{'plastic-bar':recipe})['coal']['required_per_s']==1
 boosted={**recipe,'productivity_bonus':.1}
 assert goal_demand({'plastic-bar':2.2},{'plastic-bar':boosted})['coal']['required_per_s']==1
 assert machine_rate('chemical-plant','plastic-bar',recipe)==2
 assert machine_rate('chemical-plant','plastic-bar',boosted)==2.2
