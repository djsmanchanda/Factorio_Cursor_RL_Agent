# Path: tests/test_line_supply.py | Purpose: Distinguish usable output backpressure from extraction shortage in the real Lua survey and promotion guard.
from __future__ import annotations

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator import live_base

lupa = pytest.importorskip("lupa")
pytestmark = [pytest.mark.deterministic, pytest.mark.fast, pytest.mark.lua]


class SurveyClient:
    def __init__(self, working: int, blocked: int, *, status="full_output", energy=1,
                 ore=1, active=True, ghosts=False):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.output = []
        self.lua.globals().emit = self.output.append
        self.lua.execute("""
            defines = {entity_status={working=1,full_output=2,no_ingredients=3},
                       inventory={crafter_input=1}}
            real, ghosts = {}, {}
            function add_machine(status, energy, ore, active, ghost)
                local e = {type=ghost and 'entity-ghost' or 'furnace',
                    name='electric-furnace',ghost_name='electric-furnace',
                    status=status,energy=energy,active=active,products_finished=1,
                    position={x=#real+#ghosts,y=0}}
                function e.get_recipe()
                    return {name='copper-plate',ingredients={
                        {name='copper-ore',type='item',amount=1}}}
                end
                function e.get_inventory(index)
                    assert(not ghost,'ghost must not be treated as live capacity')
                    assert(index==defines.inventory.crafter_input,
                        'Factorio 2.1.24 requires crafter_input')
                    return {get_item_count=function(name)
                        assert(name=='copper-ore');return ore end}
                end
                if ghost then ghosts[#ghosts+1]=e else real[#real+1]=e end
            end
            local surface = {find_entities_filtered=function(filter)
                return filter.type=='entity-ghost' and ghosts or real end}
            game = {surfaces={nauvis=surface},forces={player={}}}
            rcon = {print=emit}
        """)
        add = self.lua.globals().add_machine
        for _ in range(working):
            add(1, 1, 1, True, False)
        state = self.lua.globals().defines.entity_status[status]
        for _ in range(blocked):
            add(state, energy, ore, active, ghosts)

    def command(self, text):
        assert text.startswith('/sc ')
        self.lua.execute(text[4:])
        return str(self.output.pop())


def test_six_backed_up_furnaces_still_cannot_feed_a_nine_per_second_line(monkeypatch):
    # Six full_output statuses and 4,818 stocked plates match W3F's initial
    # observation. Input-ready state is a synthetic conservative best case.
    client = SurveyClient(0, 6)
    source = live_base.find_line(client, 'nauvis', 'player', 'copper-plate', 'electric-furnace')
    assert source.working_count == 0
    assert source.live_machine_count == 6
    assert source.output_blocked_ready_count == 6
    monkeypatch.setattr(live_base, 'available_items', lambda *_a: {'copper-plate': 4818})

    shortfall = builder._promotion_upstream_shortfall(client, 'nauvis', 'player', 'copper-cable', 6)

    assert shortfall == ('copper-plate', 3.75, 9.0)


@pytest.mark.parametrize('unavailable', [None, 'no_ingredients', 'ghost', 'unpowered', 'empty_input', 'inactive'])
def test_promotion_uses_ready_backpressure_but_not_unusable_machines(monkeypatch, unavailable):
    # W3F step107's 8.75/s implies 14 working furnaces. The remaining ten
    # machines and their states are synthetic contrasts, not captured facts.
    client = SurveyClient(
        14, 10, status='no_ingredients' if unavailable == 'no_ingredients' else 'full_output',
        energy=0 if unavailable == 'unpowered' else 1,
        ore=0 if unavailable == 'empty_input' else 1,
        active=unavailable != 'inactive', ghosts=unavailable == 'ghost',
    )
    source = live_base.find_line(client, 'nauvis', 'player', 'copper-plate', 'electric-furnace')
    assert source.machine_count == 24
    assert source.working_count == 14
    assert source.output_blocked_ready_count == (10 if unavailable is None else 0)
    assert source.live_machine_count == (14 if unavailable == 'ghost' else 24)
    monkeypatch.setattr(live_base, 'available_items', lambda *_a: {'copper-plate': 4800})

    shortfall = builder._promotion_upstream_shortfall(client, 'nauvis', 'player', 'copper-cable', 6)

    assert shortfall == (None if unavailable is None else ('copper-plate', 8.75, 9.0))
