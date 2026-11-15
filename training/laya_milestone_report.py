# Path: training/laya_milestone_report.py | Purpose: one {earned, holds_now, inherited} milestone block for every reader, built from the checkpoint-evidence contract, with a labelled fallback for journals that predate it.
"""Milestone reporting source of truth.

earned     passed after an observed failed->passed pair of adjacent polls (never at the first observation).
holds_now  passes on the last successful poll (current state, not history).
inherited  already passing on the first observation; not earned by the run.

``source`` says where the block came from: ``contract`` (checkpoint-evidence accumulator), ``legacy_polls``
(the same rule replayed over recorded ``strict_current`` rows, for journals without ``_contract``) or
``legacy_latch`` (only the old "ever passed" acceptance latch exists). The runner's goal ladder
(``phases_reached``) is a proxy and is never a source here.
"""
from __future__ import annotations

from orchestrator.checkpoint_milestones import MILESTONE_IDS


def _ordered(ids):
    return sorted(set(ids), key=lambda c: MILESTONE_IDS.index(c) if c in MILESTONE_IDS else len(MILESTONE_IDS))


def _passed(predicates):
    if not isinstance(predicates, dict):
        return None
    return _ordered(c for c, v in predicates.items()
                    if (v.get("status") if isinstance(v, dict) else v) in ("passed", True))


def from_contract(contract, loaded_tick=None):
    """Block from a ``_contract`` evidence dict, or None when it is absent or not the accumulator's shape."""
    if not isinstance(contract, dict) or "observed_events" not in contract:
        return None
    earned = [e["checkpoint"] for e in contract.get("observed_events") or ()
              if isinstance(e, dict) and e.get("kind") == "predicate_transition" and isinstance(e.get("checkpoint"), str)
              and (type(loaded_tick) is not int or (type(e.get("tick")) is int and e["tick"] > loaded_tick))]
    inherited = contract.get("inherited_start")
    return {"source": "contract", "earned": _ordered(earned),
            "holds_now": _passed(contract.get("present")),
            "inherited": _passed(inherited.get("predicates")) if isinstance(inherited, dict) else None}


def from_polls(rows, loaded_tick=None):
    """Same rule as the accumulator over recorded ``strict_current`` rows (statuses True/False/None)."""
    polls = [r for r in rows if isinstance(r, dict) and isinstance(r.get("statuses"), dict) and r["statuses"]
             and type(r.get("tick")) is int and any(v is not None for v in r["statuses"].values())]
    if not polls:
        return None
    first, earned, previous = polls[0]["statuses"], set(), None
    for row in polls:
        if previous is not None and row["tick"] > previous["tick"] and (type(loaded_tick) is not int or row["tick"] > loaded_tick):
            earned.update(c for c, v in row["statuses"].items() if v is True and previous["statuses"].get(c) is False)
        previous = row
    return {"source": "legacy_polls", "earned": _ordered(earned),
            "holds_now": _ordered(c for c, v in polls[-1]["statuses"].items() if v is True),
            "inherited": _ordered(c for c, v in first.items() if v is True)}


def from_latch(ever, final=None):
    """Last resort: only the old latch exists. Earned and inherited cannot be told apart."""
    return {"source": "legacy_latch", "earned": _ordered(ever or ()),
            "holds_now": None if final is None else _ordered(final), "inherited": None}


def from_journal_rows(rows, loaded_tick=None, ever=None, final=None):
    """Prefer the last ``_contract``; else replay polls; else the latch."""
    polls = [r for r in rows if isinstance(r, dict) and r.get("event") == "strict_current"]
    for row in reversed(polls):
        evidence = row.get("predicate_evidence")
        block = from_contract(evidence.get("_contract") if isinstance(evidence, dict) else None, loaded_tick)
        if block is not None:
            return block
    return from_polls(polls, loaded_tick) or from_latch(ever, final)


def text(block):
    """Compact log text: ``earned=C2,C3 holds_now=C0 inherited=C0`` (``unknown`` when a part is not known)."""
    def part(ids):
        return "unknown" if ids is None else ",".join(ids) or "none"
    if not block:
        return "earned=unknown holds_now=unknown inherited=unknown source=none"
    return (f"earned={part(block['earned'])} holds_now={part(block['holds_now'])} "
            f"inherited={part(block['inherited'])} source={block['source']}")
