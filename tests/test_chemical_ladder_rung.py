# Path: tests/test_chemical_ladder_rung.py | Purpose: Protect the chemical ladder rung as a world count: reserved and placed rung items satisfy it, nothing is latched.

from orchestrator import autonomous_builder as ab


class Client:
    def command(self, text):
        return ""


def _world(monkeypatch, stock, placed):
    monkeypatch.setattr(ab, "_chemical_uses_rotating_batch", lambda *a: True)
    monkeypatch.setattr(ab.live_base, "transferable_items", lambda *a: dict(stock))
    monkeypatch.setattr(ab.live_base, "placed_entity_count", lambda c, s, f, name: placed.get(name, 0))
    monkeypatch.setitem(ab._CHEMICAL_BATCH_TARGETS, "pipe", 100)


def test_pipe_the_oil_cell_reserved_or_placed_still_satisfies_its_rung(monkeypatch):
    # batch-20261016-night: the rung reopened once the oil cell claimed its pipe (~780 pipe loans, no oil cell).
    _world(monkeypatch, stock={"pipe": 12}, placed={"pipe": 95})
    assert ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")


def test_a_rung_the_world_does_not_hold_is_missing_and_nothing_is_latched(monkeypatch):
    stock, placed = {"pipe": 100}, {}
    _world(monkeypatch, stock, placed)
    assert ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")
    stock["pipe"] = 10  # consumed somewhere that did not place it: the world no longer holds the rung
    assert not ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")


def _loan(step_recipe, crafts=1):
    return ab.MallBootstrapLoan(
        original_recipe="copper-cable", target_item=step_recipe, target_count=1,
        side="left", requester_position=(42.5, 38.5), current_recipe=step_recipe,
        step_recipe=step_recipe, step_required_crafts=crafts,
    )


def _drawing(monkeypatch, *loans):
    monkeypatch.setitem(ab.LINE_RECIPES, "oil-refinery", {
        "ingredients": ["steel-plate", "iron-gear-wheel", "stone-brick", "electronic-circuit", "pipe"],
        "amounts": [15, 10, 10, 10, 10],
    })
    monkeypatch.setitem(ab.LINE_RECIPES, "pipe-to-ground", {
        "ingredients": ["pipe", "iron-plate"], "amounts": [10, 5],
    })
    monkeypatch.setattr(ab, "active_bootstrap_loans", lambda *a: loans)


def test_pipe_a_later_rung_loan_has_drawn_is_still_the_ladders(monkeypatch):
    # reset cohort network-s76601: the oil-refinery loan drew pipe 101 -> 81, the reopened
    # pipe rung restored the loan, recovery returned the pipe, and the pair cycled six times.
    _world(monkeypatch, stock={"pipe": 81}, placed={})
    _drawing(monkeypatch, _loan("oil-refinery"))  # one craft plus headroom requests 20 pipe
    assert ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")


def test_pipe_consumed_outside_the_ladder_still_reopens_the_rung(monkeypatch):
    _world(monkeypatch, stock={"pipe": 81}, placed={})
    _drawing(monkeypatch, _loan("pipe-to-ground"))
    assert not ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")
    _world(monkeypatch, stock={"pipe": 10}, placed={})
    _drawing(monkeypatch, _loan("oil-refinery"))  # a real shortage is not hidden by the draw
    assert not ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")


def _later_rungs(monkeypatch):
    for rung, pipe in (("chemical-plant", 5), ("oil-refinery", 10), ("offshore-pump", 1), ("pumpjack", 10)):
        monkeypatch.setitem(ab.LINE_RECIPES, rung, {"ingredients": ["steel-plate", "pipe"], "amounts": [5, pipe],
                                                    "product_amount": 1})
    monkeypatch.setattr(ab, "active_bootstrap_loans", lambda *a: ())


def test_pipe_crafted_into_later_rungs_still_satisfies_its_rung(monkeypatch):
    # reset-chest rung arm: 124 regressions, all back to pipe, after later rungs consumed it (rung-s76714).
    stock, placed = {"pipe": 70, "chemical-plant": 2, "pumpjack": 1}, {"oil-refinery": 1, "offshore-pump": 1}
    _world(monkeypatch, stock, placed)
    _later_rungs(monkeypatch)
    assert ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")  # 70 + 10 + 10 + 10 + 1
    stock.update({"chemical-plant": 0, "pumpjack": 0})
    assert not ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")  # 70 + 10 + 1


def test_embodied_rungs_do_not_hide_a_real_pipe_shortage(monkeypatch):
    _world(monkeypatch, stock={"pipe": 10, "chemical-plant": 2}, placed={})
    _later_rungs(monkeypatch)
    assert not ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")
