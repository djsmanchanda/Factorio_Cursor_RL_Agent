# Path: training/laya_toolkit.py
# Purpose: Bind Laya's disposable choices to validated training mechanics.

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from orchestrator.laya_default_registry import training_registry
from orchestrator.laya_operations import OperationCompiler, OperationOption, sha256_hash
from planners.plan_validation import actions, entity_footprint_tiles, validate_build_plan
from planners.sandbox_infrastructure import build_layout_authorization
from training.candidates import furnace_refining_candidates, mining_delivery_candidates
from training.canonical import plan_hash
from training.contracts import validate_scenario
from training.isolation import assert_training_identity


class LayaToolkitError(RuntimeError):
    """A compiled choice cannot be safely realized by the training toolkit."""


class StaleLayaOption(LayaToolkitError):
    """The selected option no longer describes the freshly observed world."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    kind: str
    report: Mapping[str, Any] | None = None
    execution: Mapping[str, Any] | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class BoundOption:
    """One model-facing option and the code-owned handler it invokes."""

    option: OperationOption
    handler: Callable[[], ToolResult]
    observation_fingerprint: str
    candidate: Mapping[str, Any] | None = None

    def state_option(self) -> dict[str, Any]:
        return self.option.to_state_option()


def _scope(scenario: Mapping[str, Any]) -> dict[str, Any]:
    environment = scenario["environment"]
    bounds = scenario["constraints"]["allowed_build_area"]
    return {
        "surface": str(environment["surface_name"]),
        "force": str(environment["force_name"]),
        "bounds": {
            "x_min": int(bounds["x_min"]),
            "y_min": int(bounds["y_min"]),
            "x_max_exclusive": int(bounds["x_max_exclusive"]),
            "y_max_exclusive": int(bounds["y_max_exclusive"]),
        },
    }


def _decision_fingerprint(report: Mapping[str, Any]) -> str:
    objective = report.get("objective") or {}
    status = report.get("status")
    return sha256_hash({
        "scenario_id": report.get("scenario_id"),
        "surface": report.get("surface"),
        "force": report.get("force"),
        # The lab moves ready -> running by itself; both are the same active
        # phase (episode_world.lua), so that transition must not stale a choice.
        "status": "active" if status in {"ready", "running"} else status,
        "objective": {
            "target_rate_per_tick": objective.get("target_rate_per_tick"),
            "stage_index": objective.get("stage_index"),
            "destination_fixture_ids": objective.get("destination_fixture_ids"),
        },
    })


def _scenario_at_observation(
    scenario: Mapping[str, Any], report: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a staged objective into a fresh candidate request."""
    current = copy.deepcopy(dict(scenario))
    observed = report.get("objective") or {}
    if "target_rate_per_tick" in observed:
        current["objective"]["target_rate_per_tick"] = float(observed["target_rate_per_tick"])
    destinations = observed.get("destination_fixture_ids") or ()
    if destinations:
        current["objective"]["destination_fixture_id"] = str(destinations[0])
        current["objective"]["destination_fixture_ids"] = list(destinations)
    return current


class LayaToolkit:
    """Compile legal alternatives and execute only an exact, fresh selection.

    Candidate generators may realize geometry, while this class retains policy
    neutrality: it never ranks candidates or picks one on the model's behalf.
    """

    WAIT_SECONDS = (1.0, 5.0, 15.0)

    def __init__(self, bridge: Any, scenario: Mapping[str, Any], *, sleeper: Callable[[float], None]):
        validate_scenario(scenario)
        self.bridge = bridge
        self.scenario = copy.deepcopy(dict(scenario))
        self.sleeper = sleeper
        self.registry = training_registry()
        self.compiler = OperationCompiler(self.registry)
        self.scope = _scope(scenario)
        assert_training_identity(self.scope["surface"], self.scope["force"])

    @property
    def family_tool_id(self) -> str:
        family = str(self.scenario["family"])
        if family not in {"mining_delivery", "furnace_refining"}:
            raise LayaToolkitError(f"unsupported Laya-primary scenario family: {family}")
        return f"training.{family}"

    def _compile_one(self, raw: Mapping[str, Any]) -> OperationOption:
        return self.compiler.compile((raw,))[0]

    def _wait_bound(self, seconds: float, fingerprint: str, *, prefix: str = "wait") -> BoundOption:
        option = self._compile_one({
            "tool_id": "training.wait",
            "option_id": f"{prefix}.{int(seconds)}s",
            "summary": f"Wait {int(seconds)} seconds, then observe the same transaction again.",
            "features": {"wait_seconds": seconds},
            "scope": self.scope,
            "parameters": {"seconds": seconds, "observation": fingerprint},
        })

        def handle() -> ToolResult:
            if seconds not in self.WAIT_SECONDS:
                raise LayaToolkitError("wait duration is outside the bounded toolkit")
            self.sleeper(seconds)
            return ToolResult(kind="wait", report=self.bridge.observe(self._episode_id), detail=f"waited {seconds:g}s")

        return BoundOption(option, handle, fingerprint)

    def _escalate_bound(self, fingerprint: str, *, prefix: str = "escalate") -> BoundOption:
        option = self._compile_one({
            "tool_id": "training.escalate",
            "option_id": f"{prefix}.stop",
            "summary": "Stop this disposable trajectory and preserve its evidence for review.",
            "features": {"safe_stop": 1.0},
            "scope": self.scope,
            "parameters": {"observation": fingerprint},
        })
        return BoundOption(
            option,
            lambda: ToolResult(kind="escalate", detail="model selected safe escalation"),
            fingerprint,
        )

    def bind_episode(self, episode_id: str) -> None:
        if not episode_id:
            raise ValueError("episode_id is required")
        self._episode_id = episode_id

    def operation_options(self, report: Mapping[str, Any]) -> tuple[BoundOption, ...]:
        fingerprint = _decision_fingerprint(report)
        family = str(self.scenario["family"])
        option = self._compile_one({
            "tool_id": self.family_tool_id,
            "option_id": f"operation.{family}",
            "summary": f"Compile fresh validated {family.replace('_', ' ')} candidates.",
            "features": {"candidate_count": float(len(self._candidates(report)))},
            "scope": self.scope,
            "parameters": {"observation": fingerprint},
        })
        compile_result = BoundOption(
            option,
            lambda: ToolResult(kind="compile_candidates", report=report),
            fingerprint,
        )
        return (
            compile_result,
            *(self._wait_bound(seconds, fingerprint) for seconds in self.WAIT_SECONDS),
            self._escalate_bound(fingerprint),
        )

    def wait_or_act_options(
        self, report: Mapping[str, Any], *, can_act: bool,
    ) -> tuple[BoundOption, ...]:
        fingerprint = _decision_fingerprint(report)
        options = [self._wait_bound(seconds, fingerprint, prefix="wait") for seconds in self.WAIT_SECONDS]
        if can_act:
            option = self._compile_one({
                "tool_id": self.family_tool_id,
                "option_id": "act.choose_operation",
                "summary": "Act on a fresh, currently unexecuted candidate set.",
                "features": {"fresh_action_available": 1.0},
                "scope": self.scope,
                "parameters": {"observation": fingerprint, "next": "choose_operation"},
            })
            options.append(BoundOption(
                option, lambda: ToolResult(kind="choose_operation", report=report), fingerprint,
            ))
        options.append(self._escalate_bound(fingerprint))
        return tuple(options)

    def recovery_options(
        self,
        report: Mapping[str, Any],
        *,
        failed_candidate: Mapping[str, Any] | None,
        allow_retry: bool,
        allow_reselect: bool = False,
    ) -> tuple[BoundOption, ...]:
        fingerprint = _decision_fingerprint(report)
        options: list[BoundOption] = [
            self._wait_bound(seconds, fingerprint, prefix="recovery.wait")
            for seconds in self.WAIT_SECONDS
        ]
        if allow_retry and failed_candidate is not None:
            fresh = {str(item["action_id"]): item for item in self._candidates(report)}
            candidate = fresh.get(str(failed_candidate.get("action_id")))
            if candidate is not None and candidate["plan_hash"] == failed_candidate.get("plan_hash"):
                options.append(self._candidate_bound(
                    candidate, report, option_id=f"recovery.retry.{candidate['action_id']}",
                ))
        if allow_reselect:
            option = self._compile_one({
                "tool_id": self.family_tool_id,
                "option_id": "recovery.reselect",
                "summary": "Discard the stale selection and choose again from a fresh legal catalog.",
                "features": {"fresh_catalog": 1.0},
                "scope": self.scope,
                "parameters": {"observation": fingerprint, "next": "choose_operation"},
            })
            options.append(BoundOption(
                option, lambda: ToolResult(kind="reselect", report=report), fingerprint,
            ))
        options.append(self._escalate_bound(fingerprint, prefix="recovery.escalate"))
        return tuple(options)

    def candidate_options(self, report: Mapping[str, Any]) -> tuple[BoundOption, ...]:
        return tuple(self._candidate_bound(candidate, report) for candidate in self._candidates(report))

    def _candidates(self, report: Mapping[str, Any]) -> list[dict[str, Any]]:
        scenario = _scenario_at_observation(self.scenario, report)
        family = str(self.scenario["family"])
        if family == "furnace_refining":
            candidates = furnace_refining_candidates(scenario, validate_contract=False)
        elif family == "mining_delivery":
            destinations = tuple((report.get("objective") or {}).get("destination_fixture_ids") or ())
            candidates = mining_delivery_candidates(
                scenario,
                target_rate_per_tick=scenario["objective"]["target_rate_per_tick"],
                sink_fixture_ids=destinations or None,
                validate_contract=False,
            )
        else:
            raise LayaToolkitError(f"unsupported Laya-primary scenario family: {family}")
        remaining = self._remaining_budget(report)
        return [candidate for candidate in candidates if self._fundable(candidate["plan"], remaining)]

    def _remaining_budget(self, report: Mapping[str, Any]) -> dict[str, int]:
        # Mirrors the lab's committed_counts: already-built non-fixture entities
        # consume the scenario budget, so a second full layout may be illegal.
        built = (report.get("metrics") or {}).get("built_entities") or {}
        budget = self.scenario["construction_budget"]
        return {entity: int(limit) - int(built.get(entity, 0)) for entity, limit in budget.items()}

    @staticmethod
    def _fundable(plan: Mapping[str, Any], remaining: Mapping[str, int]) -> bool:
        # Conservative: plan actions that coincide with existing entities are
        # counted as new, which can only prune, never admit, an illegal plan.
        counts: dict[str, int] = {}
        for action in actions(dict(plan)):
            entity = str(action["entity"])
            counts[entity] = counts.get(entity, 0) + 1
        return all(count <= remaining.get(entity, 0) for entity, count in counts.items())

    def _candidate_bound(
        self,
        candidate: Mapping[str, Any],
        report: Mapping[str, Any],
        *,
        option_id: str | None = None,
    ) -> BoundOption:
        fingerprint = _decision_fingerprint(report)
        raw = {
            "tool_id": self.family_tool_id,
            "option_id": option_id or str(candidate["action_id"]),
            "summary": self._candidate_summary(candidate),
            "features": dict(candidate["features"]),
            "scope": self.scope,
            "plan_hash": str(candidate["plan_hash"]),
            "parameters": {
                "action_id": str(candidate["action_id"]),
                "observation": fingerprint,
            },
        }
        option = self._compile_one(raw)
        frozen = copy.deepcopy(dict(candidate))
        return BoundOption(
            option,
            lambda: self._execute_fresh(frozen, option, fingerprint),
            fingerprint,
            frozen,
        )

    @staticmethod
    def _candidate_summary(candidate: Mapping[str, Any]) -> str:
        # Numeric features are already complete in the model state. Repeating
        # them in the option head wastes the small decision context.
        return "Build the validated layout described by this option's features."

    def _execute_fresh(
        self,
        selected: Mapping[str, Any],
        selected_option: OperationOption,
        selected_fingerprint: str,
    ) -> ToolResult:
        fresh_report = self.bridge.observe(self._episode_id)
        if _decision_fingerprint(fresh_report) != selected_fingerprint:
            raise StaleLayaOption("objective, status, or scope changed after Laya selected the option")
        fresh_by_id = {str(item["action_id"]): item for item in self._candidates(fresh_report)}
        candidate = fresh_by_id.get(str(selected["action_id"]))
        if candidate is None:
            raise StaleLayaOption("selected candidate is absent from the fresh legal catalog")
        if candidate["plan_hash"] != selected.get("plan_hash"):
            raise StaleLayaOption("selected candidate plan hash changed before execution")
        rebound = self._candidate_bound(
            candidate,
            fresh_report,
            option_id=selected_option.option_id,
        ).option
        for field in ("option_id", "signature", "plan_hash", "scope", "tool_id"):
            if getattr(rebound, field) != getattr(selected_option, field):
                raise StaleLayaOption(f"selected candidate {field} changed before execution")
        plan = copy.deepcopy(dict(candidate["plan"]))
        if plan_hash(plan) != selected_option.plan_hash:
            raise LayaToolkitError("candidate plan content does not match its recorded plan hash")
        validate_build_plan(plan)
        if plan.get("surface") != self.scope["surface"] or plan.get("force") != self.scope["force"]:
            raise LayaToolkitError("candidate plan escaped the authorized training scope")
        self._validate_scenario_plan(plan)
        assert_training_identity(str(plan["surface"]), str(plan["force"]))
        authorization = build_layout_authorization([plan])
        execution = self.bridge.execute(self._episode_id, authorization, plan)
        return ToolResult(kind="execute", report=fresh_report, execution=execution)

    def _validate_scenario_plan(self, plan: Mapping[str, Any]) -> None:
        """Recheck the scenario's hard area, entity, budget, and fixture limits."""
        bounds = self.scope["bounds"]
        allowed = set(self.scenario["constraints"]["allowed_entities"])
        budget = self.scenario["construction_budget"]
        counts: dict[str, int] = {}
        for action in actions(dict(plan)):
            entity = str(action["entity"])
            if entity not in allowed:
                raise LayaToolkitError(f"candidate uses entity outside scenario scope: {entity}")
            counts[entity] = counts.get(entity, 0) + 1
            tiles = entity_footprint_tiles(action)
            if any(
                x < bounds["x_min"] or x >= bounds["x_max_exclusive"]
                or y < bounds["y_min"] or y >= bounds["y_max_exclusive"]
                for x, y in tiles
            ):
                raise LayaToolkitError("candidate footprint leaves the allowed build area")
        for entity, count in counts.items():
            if count > int(budget.get(entity, 0)):
                raise LayaToolkitError(
                    f"candidate requires {count} {entity}, above scenario budget {budget.get(entity, 0)}"
                )

    @staticmethod
    def resolve_selection(
        decision: Mapping[str, Any], options: Sequence[BoundOption],
    ) -> BoundOption:
        selected = decision.get("selected_option") or {}
        option_id = str(selected.get("option_id", ""))
        matches = [bound for bound in options if bound.option.option_id == option_id]
        if len(matches) != 1:
            raise LayaToolkitError("Laya selection does not identify exactly one bound handler")
        bound = matches[0]
        expected = bound.state_option()
        for field in ("option_id", "signature", "plan_hash", "scope", "tool_id"):
            if selected.get(field) != expected.get(field):
                raise LayaToolkitError(f"Laya selection changed bound field {field}")
        return bound


__all__ = [
    "BoundOption", "LayaToolkit", "LayaToolkitError", "StaleLayaOption", "ToolResult",
]
