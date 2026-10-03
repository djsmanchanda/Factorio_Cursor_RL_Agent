# Path: training/laya_mission.py
# Purpose: The mod_playground mission ladder as data: phase targets for Laya's status board, mirroring the deterministic milestones.

"""Mission ladder for the clean Laya runner.

Each phase names the deterministic checkpoint it corresponds to
(`orchestrator/checkpoint_milestones.py`), the sustained item rates that
define it for the status board, and the guideline scopes that apply.  Rates
are editable mission data, not decision rules: Laya still chooses what to
build to reach them.  A phase is reached when every target is measured as
satisfied; mapping onto the deterministic C-predicates is separate evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

MISSION_ID = "mod-playground-mining-productivity-4"

PHASES: tuple[dict, ...] = (
    # User priority 2026-10-03: iron and copper starters before the mall or stone.
    {"phase_id": "C0", "name": "iron_copper_starter",
     "targets": {"iron-plate": 0.5, "copper-plate": 0.5},
     "scopes": ("mission.goal", "mission.power", "mission.plan", "mission.mine", "mission.smelt", "mission.transport")},
    {"phase_id": "C1", "name": "starter_mall",
     "targets": {"iron-gear-wheel": 0.25, "copper-cable": 0.5, "stone": 0.25},
     "scopes": ("mission.goal", "mission.power", "mission.opening", "mission.mine", "mission.mall", "mission.transport")},
    {"phase_id": "C2", "name": "iron_copper_rollout",
     "targets": {"iron-plate": 3.0, "copper-plate": 3.0},
     "scopes": ("mission.goal", "mission.power", "mission.opening", "mission.mine", "mission.smelt", "mission.transport")},
    {"phase_id": "C3", "name": "stone_rollout",
     "targets": {"stone-brick": 1.5},
     "scopes": ("mission.goal", "mission.power", "mission.mine", "mission.smelt", "mission.transport")},
    {"phase_id": "C4", "name": "first_plastic", "targets": {"plastic-bar": 0.5},
     "scopes": ("mission.goal", "mission.power", "mission.build", "mission.transport")},
    {"phase_id": "C6", "name": "advanced_circuits", "targets": {"advanced-circuit": 0.25},
     "scopes": ("mission.goal", "mission.power", "mission.build", "mission.assemble", "mission.transport")},
    {"phase_id": "S1", "name": "science_packs",
     "targets": {"automation-science-pack": 0.5, "logistic-science-pack": 0.5,
                 "chemical-science-pack": 0.25, "production-science-pack": 0.25},
     "scopes": ("mission.goal", "mission.power", "mission.research", "mission.assemble", "mission.transport")},
)


def phase(phase_id: str) -> dict:
    return next(p for p in PHASES if p["phase_id"] == phase_id)


def current_phase(reached: Sequence[str]) -> dict | None:
    """First phase not yet reached; ``None`` once the ladder is complete."""
    return next((p for p in PHASES if p["phase_id"] not in reached), None)


def phase_reached(board: Mapping) -> bool:
    """Every phase target measured as satisfied on the status board."""
    needs = {n["item"]: n for n in board["needs"]}
    return board["measured"] and all(needs[item]["condition"] == "satisfied" for item in board["targets"])
