# Path: tests/test_feedstock_chest_caps.py | Purpose: Protect the gear/cable own-chest cap: built machines wired to their own provider, ghosts skipped, unchanged cells not resubmitted.

from orchestrator import autonomous_builder as ab
from orchestrator import live_base
from planners.stock_gating import chest_gate

GEAR, GHOST = (33.5, 33.5), (45.5, 33.5)


class Client:
    def command(self, text):
        return ""


def _install(monkeypatch):
    submitted = []
    monkeypatch.setattr(live_base, "find_line", lambda c, s, f, item, machine, **k: live_base.LineState(
        item, 2, 1, None, (GEAR, GHOST)) if item == "iron-gear-wheel" else None)
    monkeypatch.setattr(live_base, "entity_names_at", lambda c, s, positions: {
        GEAR: "assembling-machine-1", GHOST: "entity-ghost"})
    monkeypatch.setattr(ab, "_paired_mall_provider", lambda c, s, positions: (positions[0][0] + 3, positions[0][1] - 1))
    monkeypatch.setattr(ab, "_submit", lambda client, bridge, surface, plan, name, emit: submitted.append(plan))
    monkeypatch.setitem(ab.ITEM_STACK_SIZES, "iron-gear-wheel", 100)  # live catalog value
    ab._MALL_REFRESH_SIGNATURES.clear()
    return submitted


def test_built_gear_cells_are_wired_to_their_own_chest_once(monkeypatch):
    submitted = _install(monkeypatch)
    ab.refresh_feedstock_chest_caps(Client(), None, "nauvis", "player", lambda line: None)
    ab.refresh_feedstock_chest_caps(Client(), None, "nauvis", "player", lambda line: None)
    assert len(submitted) == 1  # unchanged cells are not resubmitted
    (action,) = submitted[0]["phases"][0]["actions"]  # the ghost has nothing to wire yet
    assert action["position"] == {"x": 33.5, "y": 33.5}
    assert action["chest_gate"] == chest_gate("iron-gear-wheel", (36.5, 32.5), 1000)  # 10 stacks


def test_cable_stops_at_1k_not_ten_200_stacks(monkeypatch):
    monkeypatch.setitem(ab.ITEM_STACK_SIZES, "copper-cable", 200)
    assert ab._feedstock_chest_cap("copper-cable") == 1000  # user: no more than 1k in a shared chest
    assert ab._feedstock_chest_cap("electronic-circuit") is None


def test_demand_production_caps_the_cell_at_the_asked_level_except_feedstock(monkeypatch):
    # Live 2026-10-15: an ungated inserter cell made 711 while drills starved for gears and circuits.
    calls = []
    monkeypatch.setattr(ab, "ensure_produced", lambda *a, **k: calls.append((a[4], k["stock_target"], k["stock_gate_target"])))
    ab.produce_to_demand(Client(), None, "nauvis", "player", "inserter", (0, 0), print, stock_target=497)
    ab.produce_to_demand(Client(), None, "nauvis", "player", "iron-gear-wheel", (0, 0), print, stock_target=40)
    assert calls == [("inserter", 497, 497), ("iron-gear-wheel", 40, None)]  # gear keeps its own-chest cap


def test_growth_adds_one_feedstock_machine_and_refuses_other_items(monkeypatch):
    calls = []
    monkeypatch.setattr(ab, "ensure_produced", lambda *a, **k: calls.append((a[4], k["minimum_machines"])))
    ab.grow_producer(Client(), None, "nauvis", "player", "iron-gear-wheel", (0, 0), lambda m: None, machines=1)
    assert calls == [("iron-gear-wheel", 2)]
    import pytest
    with pytest.raises(ValueError):
        ab.grow_producer(Client(), None, "nauvis", "player", "inserter", (0, 0), lambda m: None, machines=1)


def test_finite_demand_keeps_a_gate_laya_raised_against_a_bill(monkeypatch):
    # Review 2026-10-18: a later produce inserter stock_target=50 rewrote a verified raise (20 -> 200) down to 50,
    # finite-tagged the cell at 50 and vacated it, so the verified raise had no lasting effect.
    calls, tags = [], []
    monkeypatch.setattr(ab, "ensure_produced", lambda *a, **k: calls.append((k["stock_target"], k["stock_gate_target"])) or (1.5, 2.5))
    monkeypatch.setattr(ab, "_tag_finite_mall_cells", lambda *a: tags.append(a[5]))
    monkeypatch.setitem(ab._RAISED_STOCK_GATE_FLOORS, ("nauvis", "player", "inserter"), 200)
    ab.produce_to_demand(Client(), None, "nauvis", "player", "inserter", (0, 0), print, stock_target=50)
    assert calls == [(200, 200)] and tags == [200]
    assert ab._MALL_STOCK_GATE_FLOORS[("nauvis", "player", "inserter")] == 200
    ab.produce_to_demand(Client(), None, "nauvis", "player", "inserter", (0, 0), print, stock_target=300)
    assert calls[-1] == (300, 300)  # demand above the raise still governs
