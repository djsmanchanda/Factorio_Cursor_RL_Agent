# Path: tests/test_chemical_ladder_latch.py | Purpose: Protect the chemical ladder latch: a rotating rung stocked to its batch target stays satisfied after the oil cell consumes it.

from orchestrator import autonomous_builder as ab


class Client:
    def command(self, text):
        return ""


def test_a_stocked_rotating_rung_stays_satisfied_after_consumption(monkeypatch):
    # Batch 2026-10-16 night: pipe reopened as soon as the oil cell used it; ~780 pipe loans, no oil cell.
    stock = {"pipe": 100}
    monkeypatch.setattr(ab, "_chemical_uses_rotating_batch", lambda *a: True)
    monkeypatch.setattr(ab.live_base, "available_items", lambda *a: dict(stock))
    monkeypatch.setitem(ab._CHEMICAL_BATCH_TARGETS, "pipe", 100)
    ab._CHEMICAL_RUNGS_STOCKED.clear()
    assert ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")
    stock["pipe"] = 12  # the oil cell reserved or consumed it
    assert ab._chemical_capability_started(Client(), "nauvis", "player", "pipe")


def test_a_rung_never_stocked_is_still_missing(monkeypatch):
    monkeypatch.setattr(ab, "_chemical_uses_rotating_batch", lambda *a: True)
    monkeypatch.setattr(ab.live_base, "available_items", lambda *a: {"pumpjack": 0})
    ab._CHEMICAL_RUNGS_STOCKED.clear()
    assert not ab._chemical_capability_started(Client(), "nauvis", "player", "pumpjack")
