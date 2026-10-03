# Path: tests/test_laya_component_lab.py | Purpose: Protect reusable circuit/plastic composition, scoped geometry and protected power observations.
from collections import Counter
from training.laya_component_lab import component_spec
from training.laya_power import POWERED,world_obstacles
from planners.plan_validation import validate_build_plan
import pytest

@pytest.mark.parametrize('family,mode,producers',[('circuit','belt',2),('circuit','belt',3),('circuit','belt',4),('circuit','bots',3),('plastic','belt',1),('plastic','belt',2),('plastic','belt',3)])
def test_components_are_funded_ghosts_without_sandbox_production_grants(family,mode,producers):
 spec=component_spec(family,transport=mode,producers=producers)
 plan={'phases':[{'name':k,'actions':v} for k,v in spec['groups'].items()]};validate_build_plan(plan)
 assert all(a['action_type']=='place_ghost' for p in plan['phases'] for a in p['actions'])
 assert not any(a['entity'] in {'infinity-chest','infinity-pipe','electric-energy-interface'} for p in plan['phases'] for a in p['actions'])
 assert spec['fixture_funded'] and all(s.get('item') not in {'copper-cable','electronic-circuit','plastic-bar'} for s in spec['sources'])
 if family=='circuit':assert spec['budget']['assembling-machine-2']==producers+2
 else:assert spec['budget']['oil-refinery']==producers and spec['budget']['chemical-plant']==2


def test_power_recognizes_fluids_and_placed_transport_obstacles():
 assert {'oil-refinery','chemical-plant'}<=POWERED
 obstacle={'entity':'fast-transport-belt','position':{'x':10.5,'y':12.5}}
 assert obstacle in world_obstacles({'layout_obstacles':[obstacle]})
