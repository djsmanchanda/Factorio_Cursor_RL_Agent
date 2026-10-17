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
