# Path: tests/test_research_scheduler.py
# Purpose: Verify pure, allow-list-bounded research candidate selection.

from __future__ import annotations

import pytest

from orchestrator.research_scheduler import rank_research_candidates


REPORT_IDENTITY = {
    "schema_version": "1.0.0",
    "tick": 100,
    "surface": "nauvis",
    "force": "player",
}


def _diagnosis(name: str = "research_not_selected") -> dict:
    return {"diagnosis": name, "input_report_identities": [dict(REPORT_IDENTITY)]}


def _state(**overrides) -> dict:
    state = {
        "enabled": True,
        "researched": False,
        "observed": True,
        "goal_unlock_value": 20.0,
        "prerequisite_value": 0.0,
        "required_science_packs": {"automation-science-pack": 10},
        "supply_feasibility": 1.0,
        "opportunity_cost": 0.0,
    }
    state.update(overrides)
    return state


def test_selects_highest_goal_derived_score_and_preserves_report_identity() -> None:
    decision = rank_research_candidates(
        ["automation", "logistics"],
        {
            "automation": _state(goal_unlock_value=20.0),
            "logistics": _state(goal_unlock_value=25.0),
        },
        _diagnosis(),
    )

    assert decision["status"] == "selected"
    assert decision["selected_technology"] == "logistics"
    assert decision["score_components"]["logistics"] == {
        "goal_unlock_value": 25.0,
        "prerequisite_value": 0.0,
        "required_science_pack_total": -10.0,
        "supply_feasibility": 1.0,
        "opportunity_cost": -0.0,
        "total": 16.0,
    }
    assert decision["input_report_identities"] == [REPORT_IDENTITY]


def test_tied_candidates_use_name_only_as_a_deterministic_tie_breaker() -> None:
    first = rank_research_candidates(
        ["beta", "alpha"], {"alpha": _state(), "beta": _state()}, _diagnosis()
    )
    second = rank_research_candidates(
        ["alpha", "beta"], {"beta": _state(), "alpha": _state()}, _diagnosis()
    )

    assert first == second
    assert first["selected_technology"] == "alpha"


def test_cannot_select_disabled_completed_unobserved_or_out_of_scope_technology() -> None:
    decision = rank_research_candidates(
        ["disabled-tech", "completed-tech", "unobserved-tech"],
        {
            "disabled-tech": _state(enabled=False),
            "completed-tech": _state(researched=True),
            "unobserved-tech": _state(observed=False),
            "outside-tech": _state(goal_unlock_value=9999.0),
        },
        _diagnosis(),
    )

    assert decision["status"] == "no_eligible_candidate"
    assert decision["selected_technology"] is None
    assert decision["rejections"] == {
        "completed-tech": ["already_researched"],
        "disabled-tech": ["disabled"],
        "outside-tech": ["outside_allow_list"],
        "unobserved-tech": ["unobserved"],
    }


def test_missing_force_state_is_rejected_instead_of_assumed_eligible() -> None:
    decision = rank_research_candidates(["automation"], {}, _diagnosis())

    assert decision["selected_technology"] is None
    assert decision["rejections"] == {"automation": ["missing_force_state"]}


def test_active_research_diagnosis_defers_selection_instead_of_switching_research() -> None:
    decision = rank_research_candidates(
        ["automation"], {"automation": _state()}, _diagnosis("research_progressing")
    )

    assert decision["selected_technology"] is None
    assert decision["rejections"] == {
        "automation": ["selection_deferred_by_diagnosis"],
    }


def test_state_report_identity_is_deduplicated_and_output_is_sorted() -> None:
    later_identity = dict(REPORT_IDENTITY, tick=200)
    decision = rank_research_candidates(
        ["automation"],
        {"automation": _state(input_report_identity=later_identity)},
        _diagnosis(),
    )

    assert decision["input_report_identities"] == [REPORT_IDENTITY, later_identity]


def test_rejects_unbounded_or_duplicate_candidate_sets() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        rank_research_candidates(["automation", "automation"], {}, _diagnosis())

    with pytest.raises(ValueError, match="finite"):
        rank_research_candidates((name for name in ["automation"]), {}, _diagnosis())
