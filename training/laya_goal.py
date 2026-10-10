# Path: training/laya_goal.py | Purpose: Define Laya's natural goal contract (verified research target) and the useful-state signature that separates real progress from a complete stall.

"""Goal contract for a natural Laya run.

The user goal is a completed research technology (default
``mining-productivity-4``).  Rate proxies -- science-pack rates, reaching the
C6 rate ladder, or a *queued* research target -- are observations, never the
goal.  Only the live ``target_completed`` flag for the requested level is a
verified success (``factorio_mod/research.lua``).

The module also defines the useful-state signature used to declare a *measured
complete stall*: a run stalls only when its relevant, productive state is
unchanged across the whole declared observation window.  Relevant state is
ghost/construction progress, delivery or crafting of required inputs, research
progress, funded work and loans -- not arbitrary surplus output.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

GOAL_VERSION = "laya-goal-v1"
DEFAULT_TECHNOLOGY = "mining-productivity-4"


def research_goal_satisfied(status: Mapping | None) -> bool:
    """Whether the requested technology level is verifiably completed.

    ``status`` is the ``research_status`` payload.  Success is exactly the
    technology's ``target_completed`` flag: a queued target, a running research
    or a measured science-pack rate is not success.
    """
    if not isinstance(status, Mapping):
        return False
    technology = status.get("technology")
    if not isinstance(technology, Mapping):
        return False
    return technology.get("target_completed") is True


def goal_evidence(status: Mapping | None) -> dict:
    """Bounded, honest evidence of the goal's live state for status and journals."""
    status = status if isinstance(status, Mapping) else {}
    technology = status.get("technology")
    technology = technology if isinstance(technology, Mapping) else {}
    return {
        "version": GOAL_VERSION,
        "technology": technology.get("canonical_name") or technology.get("requested_name")
        or status.get("current_target"),
        "state": technology.get("state"),
        "target_completed": technology.get("target_completed") is True,
        "verified": research_goal_satisfied(status),
        "researched": technology.get("researched") is True,
        "current_level": technology.get("current_level"),
        "requested_level": technology.get("requested_level"),
        "current_research": status.get("current_research"),
        "research_progress": status.get("research_progress"),
        "research_queue": list(status.get("research_queue") or ()),
        "science_packs": dict(technology.get("science_packs") or {}),
    }


def funded_signature(projects: Mapping | None) -> tuple[tuple[str, int], ...]:
    """Committed construction plans (material ledger): project id -> reserved item count.

    Only ``constructing`` plans count; a queued or ready earmark is not a funded
    submission.  A growing funded reservation is real progress.
    """
    funded = []
    for project_id, project in (projects or {}).items():
        if not isinstance(project, Mapping) or project.get("state") != "constructing":
            continue
        reserved = project.get("reserved") or {}
        funded.append((str(project_id), sum(int(count) for count in reserved.values())))
    return tuple(sorted(funded))


def loan_signature(loans: Sequence[Mapping] | None) -> tuple[tuple[str, str, int], ...] | None:
    """Live mall loans as ``(lender, making, target)``; ``None`` when unknown, not empty."""
    if loans is None:
        return None
    def fields(loan):
        # A loan can manufacture several precursor steps before its final item
        # arrives. Observe its own counter and remaining bill, not just identity.
        return (str(loan.get("lender")), str(loan.get("making")), int(loan.get("target") or 0),
                str(loan.get("step_recipe") or ""), loan.get("counter"), loan.get("remaining_crafts"),
                tuple(sorted((loan.get("remaining_bill") or {}).items())),
                tuple(sorted((loan.get("missing_inputs") or {}).items())))
    return tuple(sorted((fields(loan) for loan in loans), key=lambda row: row[:4]))


def useful_state(facts: Mapping | None, *, relevant_items: Sequence[str] = (), stock: Mapping[str, int] | None = None,
                 goal_status: Mapping | None = None, economics: Mapping | None = None) -> dict:
    """Counters that represent relevant, productive change for a goal run.

    Equality of two signatures means nothing useful changed in between.  Only
    required/goal items' crafting and delivery are counted, so mining arbitrary
    surplus cannot keep a run alive forever.
    """
    facts = facts if isinstance(facts, Mapping) else {}
    relevant = {str(item) for item in (relevant_items or ())}
    production = facts.get("production") or {}
    research = goal_status if isinstance(goal_status, Mapping) else {}
    return {
        "pending_ghosts": int(facts.get("pending_ghosts", 0)),
        "crafted": {str(item): int(count) for item, count in production.items() if str(item) in relevant},
        "delivered": {item: int((stock or {}).get(item, 0)) for item in sorted(relevant)},
        "research_progress": research.get("research_progress"),
        "research_queue": list(research.get("research_queue") or ()),
        "current_research": research.get("current_research"),
        "loans": loan_signature((economics or {}).get("loans")),
        "funded": funded_signature((economics or {}).get("projects")),
    }


def observation_window(settings) -> int | None:
    """The declared stall observation window in game ticks; ``None`` disables the guard."""
    return getattr(settings, "stall_observation_ticks", None)
