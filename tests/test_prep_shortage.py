# Path: tests/test_prep_shortage.py
# Purpose: Prove a prep pass that runs out of drills queues them with the mall instead of ending the run, since prep is the first thing that ever asks for a whole drill phase at once.

from __future__ import annotations

from orchestrator.parts_mall import MaterialShortage, add_demands

# End-to-end shortage handoff is exercised by
# test_prep_extraction.test_submitted_foundation_shortage_is_queued_for_the_mall.
# Counting exception clauses cannot establish that behavior.


def test_demands_keep_the_largest_target() -> None:
    targets = {"electric-mining-drill": 6}
    shortage = MaterialShortage(
        "mining_iron-ore", {"electric-mining-drill": 14}, {"electric-mining-drill": 8},
    )
    add_demands(targets, shortage)

    assert targets["electric-mining-drill"] == 14
    assert "need 14, short 6" in str(shortage), "the message the live run printed"
