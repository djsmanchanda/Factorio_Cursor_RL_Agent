# Path: tests/test_observed_consumer_demand.py | Purpose: Keep live consumer demand tied to observed effective speed across machine tiers.
from __future__ import annotations

import pytest

from orchestrator import autonomous_builder as builder, intermediate_scaling as scaling, live_base

lupa = pytest.importorskip('lupa')
pytestmark = [pytest.mark.deterministic, pytest.mark.fast, pytest.mark.lua]


class ConsumerClient:
    def __init__(self, machines):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.output = []
        self.lua.globals().emit = self.output.append
        self.lua.execute('''
            defines={entity_status={working=1,full_output=2},inventory={crafter_input=1}}
            machines={}
            function add_machine(name,speed,working,ghost)
                local e={name=name,ghost_name=name,type=ghost and 'entity-ghost' or 'assembling-machine',
                    crafting_speed=speed,status=working and 1 or 3,energy=1,active=true,
                    products_finished=0,position={x=#machines,y=0}}
                function e.get_recipe() return {name='electronic-circuit'} end
                machines[#machines+1]=e
            end
            game={forces={player={}},surfaces={nauvis={find_entities_filtered=function(filter)
                assert(filter.force==game.forces.player)
                local found={}
                for _,e in ipairs(machines) do
                    if (filter.type=='entity-ghost' and e.type=='entity-ghost') or
                       (filter.name==e.name and e.type~='entity-ghost') then found[#found+1]=e end
                end
                return found
            end}}}
            rcon={print=emit}
        ''')
        for machine in machines:
            self.lua.globals().add_machine(*machine)

    def command(self, text):
        assert text.startswith('/sc ')
        self.lua.execute(text[4:])
        return str(self.output.pop())


@pytest.fixture(autouse=True)
def circuit_recipe(monkeypatch):
    # Captured recipe contract: AM2 is the planner default, not the identity
    # of an observed AM1 consumer. Live catalogs can replace these defaults.
    monkeypatch.setitem(scaling.LINE_RECIPES, 'electronic-circuit', {
        'machine': 'assembling-machine-2', 'ingredients': ['iron-plate', 'copper-cable'],
        'amounts': [1, 3], 'craft_time': 0.5, 'product_amount': 1,
    })


@pytest.mark.parametrize('machines,rate,promote', [
    ([('assembling-machine-1', 0.5, True, False)], 3.0, None),
    ([('assembling-machine-2', 0.75, True, False)], 4.5, 6),
    ([('assembling-machine-1', 0.5, True, False),
      ('assembling-machine-2', 0.75, True, False)], 7.5, 6),
    ([('assembling-machine-1', 1.25, True, False)], 7.5, 6),
])
def test_observed_speed_controls_demand_and_promotion(machines, rate, promote):
    client = ConsumerClient(machines)
    demand = scaling.live_intermediate_demand(client, 'nauvis', 'player', 'copper-cable')
    assert demand == pytest.approx(rate)
    assert scaling.promoted_line_machine_count('copper-cable', demand, 1,
        saturated=True, backlog=0) == promote


def test_tier_counts_and_effective_speed_exclude_ghosts_and_nonworking_consumers():
    client = ConsumerClient([
        ('assembling-machine-1', 0.5, True, False),
        ('assembling-machine-2', 0.9, True, False),
        ('assembling-machine-3', 2.0, False, False),
        ('assembling-machine-3', 2.0, True, True),
    ])
    line = live_base.find_line(client, 'nauvis', 'player', 'electronic-circuit', 'assembling-machine-2')
    assert line.machine_count == 4 and line.working_count == 2
    assert line.working_machine_counts == {'assembling-machine-1': 1, 'assembling-machine-2': 1}
    assert line.working_crafting_speed == pytest.approx(1.4)
    assert scaling.live_intermediate_demand(client, 'nauvis', 'player', 'copper-cable') == pytest.approx(8.4)


@pytest.mark.parametrize('speed', [None, float('nan'), float('inf'), -1, True, '0.5'])
def test_unknown_effective_speed_is_not_replaced_with_planned_tier(speed):
    client = ConsumerClient([('assembling-machine-1', speed, True, False)])
    line = live_base.find_line(client, 'nauvis', 'player', 'electronic-circuit', 'assembling-machine-2')
    assert line.working_machine_counts == {'assembling-machine-1': 1}
    assert line.working_crafting_speed is None
    assert scaling.live_intermediate_demand(client, 'nauvis', 'player', 'copper-cable') is None


def test_no_working_consumer_establishes_zero_demand():
    client = ConsumerClient([('assembling-machine-1', None, False, False)])
    assert scaling.live_intermediate_demand(client, 'nauvis', 'player', 'copper-cable') == 0


def test_legacy_or_inconsistent_working_speed_payload_stays_unknown():
    for reply in ['1 1 0:0 0|1|0', '1 1 0:0 0|1|0|assembling-machine-1:2|0.5']:
        client = type('Client', (), {'command': lambda self, text: reply})()
        line = live_base.find_line(client, 'nauvis', 'player', 'electronic-circuit', 'assembling-machine-2')
        assert line.working_crafting_speed is None


def test_unknown_consumer_demand_defers_without_planning_or_building(monkeypatch):
    monkeypatch.setattr(builder, '_production_started', lambda *_: False)
    monkeypatch.setattr(builder.live_base, 'available_items', lambda *_: {})
    monkeypatch.setattr(builder, '_startup_mall_item_cap', lambda *_: 100)
    monkeypatch.setattr(builder, '_independent_mall_ready', lambda *_: False)
    monkeypatch.setattr(builder.live_base, 'find_line', lambda *_: None)
    monkeypatch.setattr(builder, '_mall_request_multiplier', lambda *a, **kw: None)
    monkeypatch.setattr(builder, 'live_intermediate_demand', lambda *_: None)
    monkeypatch.setattr(builder, 'promoted_line_machine_count', lambda *a, **kw: pytest.fail('unknown cannot size a line'))
    with pytest.raises(builder.ProductionPrerequisiteDeferred) as caught:
        builder._plan_line(object(), 'nauvis', 'player', 'copper-cable', lambda _: None,
            upgrade_bootstrap=False, stock_target=1, minimum_machines=1, allow_promotion=True)
    assert caught.value.code == 'consumer_demand_unknown'
