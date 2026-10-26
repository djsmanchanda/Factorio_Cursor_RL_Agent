# Path: tests/test_chemical_ladder_rung.py | Purpose: Protect the chemical ladder rung as a world count: reserved and placed rung items satisfy it, nothing is latched.

import pytest

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


def _chemical_plant_loan(monkeypatch, held):
    monkeypatch.setitem(ab.LINE_RECIPES, "chemical-plant", {
        "ingredients": ["steel-plate", "iron-gear-wheel", "electronic-circuit", "pipe"], "amounts": [5, 5, 5, 5],
    })
    monkeypatch.setattr(ab, "active_bootstrap_loans", lambda *a: (_loan("chemical-plant", crafts=2),))
    monkeypatch.setattr(ab.live_base, "loan_cell_item_count", lambda c, s, requester, machine, item: held)


def test_pipe_inside_a_later_rungs_loan_cell_is_still_the_ladders(monkeypatch):
    # opus-10h cohort 2 ladder arm: 24/25 pipe reopenings during a later rung's first craft; the cell
    # held more than its 15-pipe request (requester refilled after the inserter loaded the assembler).
    _world(monkeypatch, stock={"pipe": 82}, placed={})
    _chemical_plant_loan(monkeypatch, held=18)
    assert ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")  # 82 + 18


def test_a_loan_cell_does_not_hide_a_real_pipe_shortage(monkeypatch):
    _world(monkeypatch, stock={"pipe": 60}, placed={})
    _chemical_plant_loan(monkeypatch, held=18)
    assert not ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")
    _chemical_plant_loan(monkeypatch, held=0)  # an empty or unreadable cell keeps the planned request credit
    stock_82 = {"pipe": 82}
    _world(monkeypatch, stock=stock_82, placed={})
    assert not ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")  # 82 + 15


def test_loan_cell_item_count_reads_one_number():
    class Reply:
        def __init__(self, text):
            self.text, self.sent = text, []

        def command(self, text):
            self.sent.append(text)
            return self.text

    client = Reply("18\n")
    assert ab.live_base.loan_cell_item_count(client, "nauvis", (42.5, 38.5), (39.5, 38.5), "pipe") == 18
    # Factorio 2.1 names the assembler input inventory crafter_input (cohort 5: 46 nil-index failures).
    assert "defines.inventory.crafter_input or" in client.sent[0] and "held_stack" in client.sent[0]
    with pytest.raises(RuntimeError):  # a broken query must surface, not read as an empty cell
        ab.live_base.loan_cell_item_count(Reply("Cannot execute command"), "nauvis", (0, 0), (3, 0), "pipe")


def test_original_recipe_stock_left_in_a_loan_cell_is_not_ladder_credit(monkeypatch):
    # review 2026-10-18: a cell that made pipe-to-ground keeps its pipe; only a few crafts of buffer count.
    _world(monkeypatch, stock={"pipe": 60}, placed={})
    _chemical_plant_loan(monkeypatch, held=80)
    assert not ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")  # 60 + min(80, 15 + 15)


def test_a_reopened_rung_explains_its_count_and_an_unreadable_cell(monkeypatch):
    _world(monkeypatch, stock={"pipe": 60}, placed={})
    _chemical_plant_loan(monkeypatch, held=0)

    def unreadable(*args):
        raise RuntimeError("loan cell query returned 'error'")

    monkeypatch.setattr(ab.live_base, "loan_cell_item_count", unreadable)
    monkeypatch.setattr(ab, "_chemical_ladder_predecessors", lambda item: ("pipe",))
    monkeypatch.setattr(ab, "_missing_chemical_ladder_predecessor",
                        lambda c, s, f, item: "pipe" if not ab._chemical_capability_started(c, s, f, "pipe") else None)
    monkeypatch.setattr(ab, "_start_bootstrap_loan", lambda *a: "loan")
    lines = []
    with pytest.raises(ab.ProductionPrerequisiteDeferred):  # the borrowed rung parks the caller
        ab._ensure_chemical_ladder_predecessor(Client(), object(), "nauvis", "player", "chemical-plant", (0, 0),
                                               lines.append)
    assert "waits at pipe" in lines[0]
    assert "held 75/100 = stock 60 + placed 0 + loan 15 + embodied 0" in lines[0]
    assert "loan cell unreadable: RuntimeError" in lines[0]
