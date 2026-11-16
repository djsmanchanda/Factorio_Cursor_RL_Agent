# Path: training/laya_milestone_report.py | Purpose: one {earned, holds_now, inherited} milestone block for every reader, built from the checkpoint-evidence contract, with a labelled fallback for journals that predate it.
"""Milestone reporting source of truth.

earned     passed after an observed failed->passed pair of adjacent polls (never at the first observation, never across an
           unusable or missing poll). ``events`` (additive) gives each earned id its transition tick; ``origin_tick`` is the
           origin those ticks are measured from (``origin_basis`` names it).
holds_now  passes on the last successful poll (current state, not history).
inherited  already passing on the first observation; not earned by the run.

``predicate_versions`` (additive) maps each milestone to the predicate version that produced the block (c5-v1 and
c5-v2 measure different things, so a reader must not pool them); None when the journal does not record versions.

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


def _event(cid, tick, from_tick, version=None):
    event = {"id": cid, "tick": tick, "from_tick": from_tick}
    if isinstance(version, str):
        event["predicate_version"] = version
    return event


def _versions(predicates):
    """``{milestone: predicate version}`` from contract ``present`` or a poll's ``predicate_evidence``."""
    if not isinstance(predicates, dict):
        return None
    found = {c: v.get("version", v.get("predicate_version")) for c, v in predicates.items()
             if not str(c).startswith("_") and isinstance(v, dict)
             and isinstance(v.get("version", v.get("predicate_version")), str)}
    return {c: found[c] for c in _ordered(found)} or None


def _by_id(events):
    """One event per milestone (the first), in milestone order."""
    first = {}
    for event in events:
        first.setdefault(event["id"], event)
    return [first[c] for c in _ordered(first)]


def from_contract(contract, loaded_tick=None):
    """Block from a ``_contract`` evidence dict, or None when it is absent or not the accumulator's shape.

    ``events`` (additive) carries each earned milestone's observed transition tick; ``origin_tick`` is the run's
    loaded tick, the origin that tick is measured from (None when the runner did not record it).
    """
    if not isinstance(contract, dict) or "observed_events" not in contract:
        return None
    events = [_event(e["checkpoint"], e["tick"] if type(e.get("tick")) is int else None,
                     e["from_tick"] if type(e.get("from_tick")) is int else None, e.get("predicate_version"))
              for e in contract.get("observed_events") or ()
              if isinstance(e, dict) and e.get("kind") == "predicate_transition" and isinstance(e.get("checkpoint"), str)
              and (type(loaded_tick) is not int or (type(e.get("tick")) is int and e["tick"] > loaded_tick))]
    events = _by_id(events)
    inherited = contract.get("inherited_start")
    return {"source": "contract", "earned": [e["id"] for e in events],
            "holds_now": _passed(contract.get("present")),
            "inherited": _passed(inherited.get("predicates")) if isinstance(inherited, dict) else None,
            "events": events, "origin_tick": loaded_tick if type(loaded_tick) is int else None,
            "origin_basis": "loaded_tick" if type(loaded_tick) is int else None,
            "predicate_versions": _versions(contract.get("present"))}


def _usable(row):
    """A poll that observed at least one milestone state at a known tick."""
    return (isinstance(row, dict) and isinstance(row.get("statuses"), dict) and type(row.get("tick")) is int
            and any(v is not None for v in row["statuses"].values()))


def from_polls(rows, loaded_tick=None):
    """Same rule as the accumulator over recorded ``strict_current`` rows (statuses True/False/None).

    The ordered stream is replayed with its unusable polls (empty, all-unknown, tick-less) kept as boundaries: such a
    poll clears transition eligibility, so a pass is credited only against the adjacent, strictly earlier poll that
    recorded the milestone as False. A clock regression or repeated tick credits nothing but the next advancing poll
    may compare against it, as the accumulator does. ``holds_now`` and ``inherited`` come from the last and first
    usable polls and do not depend on eligibility.
    """
    rows = [r for r in rows if isinstance(r, dict)]
    usable = [r for r in rows if _usable(r)]
    if not usable:
        return None
    first, last = usable[0], usable[-1]
    events, previous = {}, None
    for row in rows:
        if not _usable(row):
            previous = None  # observation gap: no transition may span it
            continue
        if (previous is not None and row["tick"] > previous["tick"]
                and (type(loaded_tick) is not int or row["tick"] > loaded_tick)):
            for cid, value in row["statuses"].items():
                if value is True and previous["statuses"].get(cid) is False and cid not in events:
                    events[cid] = _event(cid, row["tick"], previous["tick"])
        previous = row
    events = _by_id(events.values())
    origin, basis = ((loaded_tick, "loaded_tick") if type(loaded_tick) is int
                     else (first["loaded_tick"], "loaded_tick") if type(first.get("loaded_tick")) is int
                     else (first["tick"], "first_poll"))
    return {"source": "legacy_polls", "earned": [e["id"] for e in events],
            "holds_now": _ordered(c for c, v in last["statuses"].items() if v is True),
            "inherited": _ordered(c for c, v in first["statuses"].items() if v is True),
            "events": events, "origin_tick": origin, "origin_basis": basis}


def event_ticks(block, cid):
    """``(ticks since the run origin, origin basis)`` of ``cid``'s first earned event, or ``(None, None)`` when unknown."""
    if not isinstance(block, dict) or type(block.get("origin_tick")) is not int:
        return None, None
    for event in block.get("events") or ():
        if isinstance(event, dict) and event.get("id") == cid and type(event.get("tick")) is int:
            return event["tick"] - block["origin_tick"], block.get("origin_basis")
    return None, None


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
            block["predicate_versions"] = block["predicate_versions"] or _versions(evidence)
            return block
    block = from_polls(polls, loaded_tick) or from_latch(ever, final)
    versions = next((_versions(r.get("predicate_evidence")) for r in reversed(polls)
                     if _versions(r.get("predicate_evidence"))), None)
    if versions is not None:
        block["predicate_versions"] = versions
    return block


def text(block):
    """Compact log text: ``earned=C2,C3 holds_now=C0 inherited=C0`` (``unknown`` when a part is not known)."""
    def part(ids):
        return "unknown" if ids is None else ",".join(ids) or "none"
    if not block:
        return "earned=unknown holds_now=unknown inherited=unknown source=none"
    return (f"earned={part(block['earned'])} holds_now={part(block['holds_now'])} "
            f"inherited={part(block['inherited'])} source={block['source']}")
