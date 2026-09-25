# Path: training/laya_runner.py
# Purpose: Run an opt-in Laya-primary loop on one disposable Factorio episode.

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.laya_operations import sha256_hash
from orchestrator.laya_state import state_hash
from training.contracts import validate_scenario
from training.isolation import assert_training_identity
from training.laya_exploration import ExplorationDirector, ExplorationGate
from training.laya_failure import FailureCause, FailurePacket
from training.laya_toolkit import BoundOption, LayaToolkit, LayaToolkitError, StaleLayaOption
from training.laya_workflow import LayaWorkflowDirector, LayaWorkflowError


REQUEST_VERSION = "factorio-decision-v2"
CAPABILITY_FILE = "laya_director_capabilities.json"
REQUIRED_DECISIONS = frozenset({
    "choose_operation", "select_candidate", "wait_or_act", "recovery",
})
_ID_CHARS = re.compile(r"[^A-Za-z0-9._:-]+")


class LayaRunnerError(RuntimeError):
    """The disposable Laya-primary run cannot safely continue."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(value: str, fallback: str) -> str:
    normalized = _ID_CHARS.sub("-", str(value).strip()).strip("-")[:128]
    return normalized or fallback


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def load_checkpoint_capabilities(checkpoint: str | Path, scenario_family: str) -> dict[str, Any]:
    """Fail closed unless a checkpoint declares the full runner request contract."""
    root = Path(checkpoint)
    path = root / CAPABILITY_FILE
    if not path.is_file():
        raise LayaRunnerError(
            f"checkpoint is not approved for primary direction; missing {CAPABILITY_FILE}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LayaRunnerError(f"invalid checkpoint capability manifest: {error}") from error
    if payload.get("request_version") != REQUEST_VERSION:
        raise LayaRunnerError(
            f"checkpoint request version must be {REQUEST_VERSION!r}"
        )
    decisions = set(payload.get("decision_kinds") or ())
    missing = sorted(REQUIRED_DECISIONS - decisions)
    if missing:
        raise LayaRunnerError("checkpoint lacks director decisions: " + ", ".join(missing))
    families = set(payload.get("scenario_families") or ())
    if scenario_family not in families:
        raise LayaRunnerError(
            f"checkpoint does not declare scenario family {scenario_family!r}"
        )
    return {
        **checkpoint_model_ref(root, model_id=payload.get("model_id")),
        "request_version": REQUEST_VERSION,
        "manifest_hash": _file_hash(path),
        "manifest": payload,
    }


def checkpoint_model_ref(checkpoint: str | Path, *, model_id: Any = None) -> dict[str, str]:
    """Identify checkpoint weights by content hash; asserts no capability."""
    root = Path(checkpoint)
    weights = root / "model.safetensors"
    if not weights.is_file():
        weights = root / "adapter_model.safetensors"
    if not weights.is_file():
        raise LayaRunnerError(f"checkpoint has no model weights: {root}")
    return {
        "model_id": _identifier(str(model_id or root.name), "laya-director"),
        "model_hash": _file_hash(weights),
    }


class LayaRunJournal:
    """Append-only evidence and restart memory for one stable trajectory."""

    def __init__(self, path: str | Path, *, resume: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[dict[str, Any]] = []
        if self.path.exists():
            if not resume:
                raise LayaRunnerError(f"run journal already exists: {self.path}")
            self.events = self._read()
            if any(event.get("type") == "termination" for event in self.events):
                raise LayaRunnerError("a terminated trajectory cannot be resumed")
            # An interrupted action may already have reached Factorio. Until
            # receipt reconciliation exists, never repeat it on restart.
            completed = {event.get("transaction_id") for event in self.events
                         if event.get("type") == "tool_result"}
            if any(event.get("type") == "transaction_started" and
                   event.get("transaction_id") not in completed for event in self.events):
                raise LayaRunnerError("unresolved transaction requires reconciliation; automatic resume refused")
            if not any(event.get("type") == "provisioned" for event in self.events):
                raise LayaRunnerError("provisioning receipt missing; automatic resume refused")
        elif resume:
            raise LayaRunnerError(f"resume journal does not exist: {self.path}")

    def _read(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LayaRunnerError(
                        f"journal line {line_number} is not valid JSON"
                    ) from error
                if event.get("seq") != len(events) + 1:
                    raise LayaRunnerError("journal sequence is not contiguous")
                events.append(event)
        if not events or events[0].get("type") != "run_started":
            raise LayaRunnerError("journal has no run_started event")
        return events

    def append(self, event_type: str, **payload: Any) -> dict[str, Any]:
        event = {"seq": len(self.events) + 1, "ts": _now(), "type": event_type, **payload}
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.events.append(event)
        return event

    @property
    def executed_plan_hashes(self) -> set[str]:
        return {
            str(event["plan_hash"])
            for event in self.events
            if event.get("type") == "tool_result"
            and event.get("kind") == "execute"
            and event.get("plan_hash")
        }

    def recent_history(self) -> list[dict[str, Any]]:
        decisions = [event for event in self.events if event.get("type") == "decision_result"][-8:]
        outcomes = {
            str(event["decision_id"]): str(event["outcome"])
            for event in self.events if event.get("type") == "decision_outcome"
        }
        return [{
            "decision_id": str(event["decision_id"]),
            "tick": max(0, int(event.get("tick", 0))),
            "summary": str(event.get("summary") or event.get("selected_option_id"))[:512],
            "outcome": outcomes.get(str(event["decision_id"]), "pending"),
        } for event in decisions]


@dataclass(frozen=True, slots=True)
class LayaRunnerConfig:
    worker_id: str
    run_id: str
    evidence_dir: Path
    max_decisions: int = 40
    max_no_progress: int = 12
    max_candidate_retries: int = 1
    resume: bool = False
    repository_revision: str = "working-tree"

    def __post_init__(self) -> None:
        if not self.worker_id:
            raise ValueError("an explicit disposable worker_id is required")
        if not 1 <= self.max_decisions <= 1000:
            raise ValueError("max_decisions must be between 1 and 1000")
        if not 1 <= self.max_no_progress <= self.max_decisions:
            raise ValueError("max_no_progress must be positive and no larger than max_decisions")
        if not 0 <= self.max_candidate_retries <= 8:
            raise ValueError("max_candidate_retries must be between 0 and 8")


class LayaPrimaryRunner:
    """Observe, ask Laya, invoke one bound tool, measure, and repeat.

    This runner is intentionally small in capability. It supports the existing
    mining-delivery and furnace-refining training mechanics, never production,
    and contains no deterministic or bandit policy fallback.
    """

    def __init__(
        self,
        bridge: Any,
        scenario: Mapping[str, Any],
        director: LayaWorkflowDirector,
        config: LayaRunnerConfig,
        *,
        model_ref: Mapping[str, Any],
        knowledge_context: Mapping[str, Any] | None = None,
        exploration: ExplorationGate | None = None,
        sleeper=time.sleep,
    ) -> None:
        validate_scenario(scenario)
        environment = scenario["environment"]
        assert_training_identity(environment["surface_name"], environment["force_name"])
        if scenario.get("family") not in {"mining_delivery", "furnace_refining"}:
            raise LayaRunnerError("initial Laya-primary runner supports mining and furnace training only")
        self.bridge = bridge
        self.scenario = json.loads(json.dumps(scenario))
        self.director = director
        self.config = config
        self.model_ref = {
            "model_id": _identifier(str(model_ref.get("model_id") or "laya-director"), "laya-director"),
            "model_hash": str(model_ref.get("model_hash") or sha256_hash({"model": "unknown"})),
        }
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.model_ref["model_hash"]):
            raise LayaRunnerError("runner requires an exact checkpoint model hash")
        # The two authorities stay distinct: exploration never carries or
        # substitutes for a trained capability manifest, and vice versa.
        if exploration is not None:
            if "manifest" in model_ref or "manifest_hash" in model_ref:
                raise LayaRunnerError("exploration runs must not carry a checkpoint capability manifest")
            if not isinstance(director, ExplorationDirector) or director.gate != exploration:
                raise LayaRunnerError("exploration runs require the matching ExplorationDirector")
            self.checkpoint_capabilities = None
            self.authority = exploration.record()
        else:
            if isinstance(director, ExplorationDirector):
                raise LayaRunnerError("an ExplorationDirector requires the explicit exploration gate")
            self.checkpoint_capabilities = self._verified_capabilities(model_ref, scenario)
            self.authority = {
                "kind": "trained_capabilities",
                "manifest_hash": self.checkpoint_capabilities["manifest_hash"],
                "runtime": "disposable_training",
                "production_authority": False,
            }
        self.knowledge = dict(knowledge_context or {})
        self.run_id = _identifier(config.run_id, "laya-run")
        self.episode_id = _identifier(f"laya-{self.run_id}", "laya-episode")
        self.trajectory_id = _identifier(
            "trajectory-" + sha256_hash({
                "run_id": self.run_id, "scenario_hash": scenario["scenario_hash"],
            })[7:23],
            "laya-trajectory",
        )
        self.journal = LayaRunJournal(
            config.evidence_dir / "runs" / f"{self.run_id}.jsonl", resume=config.resume,
        )
        self.toolkit = LayaToolkit(bridge, self.scenario, sleeper=sleeper)
        self.toolkit.bind_episode(self.episode_id)
        self.failures_dir = config.evidence_dir / "failures"
        self.failures_dir.mkdir(parents=True, exist_ok=True)
        self.initial_observation: Mapping[str, Any] | None = None
        self.current_transaction: dict[str, Any] | None = None
        self.active_failure: dict[str, Any] | None = None
        self.failed_candidate: Mapping[str, Any] | None = None
        self.retry_count = 0
        self.decision_count = sum(1 for event in self.journal.events if event.get("type") == "decision")
        self.executed_plan_hashes = self.journal.executed_plan_hashes
        self.last_progress: tuple[Any, ...] | None = None
        self.no_progress = 0
        self._last_decision_id = ""
        if config.resume:
            started = self.journal.events[0]
            if started.get("scenario_hash") != scenario["scenario_hash"]:
                raise LayaRunnerError("resume scenario does not match journal scenario hash")
            if started.get("worker_id") != config.worker_id:
                raise LayaRunnerError("resume worker identity does not match journal")
            if started.get("model") != self.model_ref or started.get("checkpoint_capabilities") != self.checkpoint_capabilities:
                raise LayaRunnerError("resume checkpoint does not match journal")
            if started.get("authority") != self.authority:
                raise LayaRunnerError("resume authority does not match journal")
            if started.get("knowledge_context") != self.knowledge:
                raise LayaRunnerError("resume guideline context does not match journal")
            # Recovery state cannot be reconstructed from an action receipt
            # alone. Preserve the stopped evidence instead of clearing retries.
            if any(event.get("type") == "failure_packet" for event in self.journal.events):
                raise LayaRunnerError("failure-bearing trajectory requires reconciliation before resume")
            first_observation = next(
                (event.get("observation") for event in self.journal.events
                 if event.get("type") == "observation"),
                None,
            )
            if isinstance(first_observation, Mapping):
                self.initial_observation = dict(first_observation)

    @staticmethod
    def _verified_capabilities(
        model_ref: Mapping[str, Any], scenario: Mapping[str, Any],
    ) -> dict[str, Any]:
        manifest = model_ref.get("manifest")
        if not isinstance(manifest, Mapping):
            raise LayaRunnerError("runner requires a verified checkpoint capability manifest")
        manifest_hash = str(model_ref.get("manifest_hash") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest_hash):
            raise LayaRunnerError("runner requires the checkpoint capability manifest hash")
        capabilities = {
            "request_version": str(model_ref.get("request_version") or REQUEST_VERSION),
            "manifest_hash": manifest_hash,
            "decision_kinds": list(manifest.get("decision_kinds") or ()),
            "scenario_families": list(manifest.get("scenario_families") or ()),
        }
        if capabilities["request_version"] != REQUEST_VERSION:
            raise LayaRunnerError("injected checkpoint request version does not match the runner")
        missing_decisions = REQUIRED_DECISIONS - set(capabilities["decision_kinds"])
        if missing_decisions:
            raise LayaRunnerError(
                "injected checkpoint lacks director decisions: "
                + ", ".join(sorted(missing_decisions))
            )
        if str(scenario["family"]) not in set(capabilities["scenario_families"]):
            raise LayaRunnerError("injected checkpoint does not cover this scenario family")
        return capabilities

    @property
    def execution_authority(self) -> str:
        return "exploration" if self.authority["kind"] == "exploration" else "primary"

    def run(self) -> dict[str, Any]:
        provisioned = self.config.resume
        termination: dict[str, Any] | None = None
        report: Mapping[str, Any] | None = None
        try:
            if not self.config.resume:
                self.journal.append(
                    "run_started",
                    run_id=self.run_id,
                    episode_id=self.episode_id,
                    trajectory_id=self.trajectory_id,
                    worker_id=self.config.worker_id,
                    runtime="disposable_training",
                    scenario_id=self.scenario["scenario_id"],
                    scenario_hash=self.scenario["scenario_hash"],
                    scenario=self.scenario,
                    knowledge_context=self.knowledge,
                    model=self.model_ref,
                    request_version=REQUEST_VERSION,
                    checkpoint_capabilities=self.checkpoint_capabilities,
                    authority=self.authority,
                    capability_scope={
                        "scenario_families": ["mining_delivery", "furnace_refining"],
                        "decision_kinds": sorted(REQUIRED_DECISIONS),
                        "runtime": "disposable_training",
                        "trained": self.checkpoint_capabilities is not None,
                        "production_authority": False,
                    },
                )
                provision = self.bridge.provision(self.episode_id, self.scenario)
                provisioned = True
                self.journal.append("provisioned", report=provision)
            report = self.bridge.observe(self.episode_id)
            if self.initial_observation is None:
                self.initial_observation = dict(report)
            while termination is None:
                self._record_observation(report)
                status = str(report.get("status") or "ready")
                if status == "completed":
                    termination = self._terminate("completed", report=report)
                    break
                if status not in {"ready", "running"}:
                    self._failure_packet(
                        FailureCause.ENVIRONMENT,
                        str((report.get("failure") or {}).get("kind") or "unknown"),
                        f"training environment reached terminal status {status!r}",
                        report=report,
                    )
                    termination = self._terminate("failed", report=report)
                    break
                if self.decision_count >= self.config.max_decisions:
                    self._failure_packet(
                        FailureCause.POLICY, "timeout",
                        "Laya exhausted the bounded decision budget without completion",
                        report=report,
                    )
                    termination = self._terminate("timed_out", report=report)
                    break
                if self.no_progress >= self.config.max_no_progress:
                    self._failure_packet(
                        FailureCause.POLICY, "timeout",
                        "Laya made no measured progress within the bounded retry window",
                        report=report,
                    )
                    termination = self._terminate("timed_out", report=report)
                    break
                try:
                    report, termination = self._step(report)
                except LayaWorkflowError as error:
                    self._failure_packet(
                        FailureCause.POLICY, "strategy", str(error), report=report,
                        evidence={"decision_boundary": "model_inference"},
                    )
                    termination = self._terminate("escalated", report=report, reason=str(error))
                except StaleLayaOption as error:
                    self._failure_packet(
                        FailureCause.VALIDATOR, "contract", str(error), report=report,
                        evidence={"decision_boundary": "pre_execution_revalidation"},
                    )
                    self._activate_failure("contract", str(error), report)
                    report = self.bridge.observe(self.episode_id)
                except LayaToolkitError as error:
                    self._failure_packet(
                        FailureCause.TOOL, "execution", str(error), report=report,
                        evidence={"decision_boundary": "tool_handler"},
                    )
                    self._activate_failure("execution", str(error), report)
                    report = self.bridge.observe(self.episode_id)
                except Exception as error:
                    self._failure_packet(
                        FailureCause.ENVIRONMENT, "unknown", str(error), report=report,
                        evidence={"exception_type": type(error).__name__},
                    )
                    termination = self._terminate("failed", report=report, reason=str(error))
            return termination
        except Exception as error:
            fallback = dict(report or self._fallback_report())
            try:
                self._failure_packet(
                    FailureCause.ENVIRONMENT,
                    "unknown",
                    f"runner lifecycle failed: {error}",
                    report=fallback,
                    evidence={
                        "exception_type": type(error).__name__,
                        "decision_boundary": "runner_lifecycle",
                    },
                )
            except Exception as packet_error:
                self.journal.append(
                    "failure_packet_error",
                    error_type=type(packet_error).__name__, error=str(packet_error),
                    original_error_type=type(error).__name__, original_error=str(error),
                )
            return self._terminate("failed", report=fallback, reason=str(error))
        finally:
            if provisioned:
                try:
                    recycled = self.bridge.recycle(self.episode_id)
                    self.journal.append("cleanup", status="recycled", report=recycled)
                except Exception as error:
                    self.journal.append(
                        "cleanup", status="failed", error_type=type(error).__name__, error=str(error),
                    )
                    if termination is not None:
                        termination["objective_status"] = termination["status"]
                        termination["status"] = "cleanup_failed"
                        termination["cleanup_error"] = str(error)

    def _fallback_report(self) -> dict[str, Any]:
        environment = self.scenario["environment"]
        objective = self.scenario["objective"]
        return {
            "status": "failed", "tick": 0, "elapsed_ticks": 0,
            "scenario_id": self.scenario["scenario_id"],
            "surface": environment["surface_name"], "force": environment["force_name"],
            "objective": {
                "target_rate_per_tick": objective.get("target_rate_per_tick"),
                "sustain_ticks": objective.get("sustain_ticks", 0),
                "stage_index": 1,
            },
            "metrics": {},
            "failure": {"kind": "unknown", "reason": "runner lifecycle failed"},
        }

    def _step(self, report: Mapping[str, Any]) -> tuple[Mapping[str, Any], dict[str, Any] | None]:
        if self.active_failure is not None:
            options = self.toolkit.recovery_options(
                report,
                failed_candidate=self.failed_candidate,
                allow_retry=self.retry_count < self.config.max_candidate_retries,
                allow_reselect=(
                    self.active_failure.get("kind") == "contract"
                    and self.retry_count < self.config.max_candidate_retries
                ),
            )
            bound = self._decide("recovery", report, options)
            if bound.option.option_id.startswith("recovery.retry.") or bound.option.option_id == "recovery.reselect":
                self.retry_count += 1
            return self._invoke(bound, report)

        fresh_candidates = self._fresh_candidates(report)
        if self.executed_plan_hashes:
            options = self.toolkit.wait_or_act_options(report, can_act=bool(fresh_candidates))
            bound = self._decide("wait_or_act", report, options)
            result_report, termination = self._invoke(bound, report)
            if termination is not None or bound.option.option_id != "act.choose_operation":
                return result_report, termination
            report = result_report

        options = self.toolkit.operation_options(report)
        bound = self._decide("choose_operation", report, options)
        result_report, termination = self._invoke(bound, report)
        if termination is not None or bound.option.option_id.startswith(("wait.", "escalate.")):
            return result_report, termination
        candidates = self._fresh_candidates(result_report)
        if not candidates:
            message = "no fresh unexecuted candidates remain"
            self._activate_failure("capability", message, result_report)
            self._failure_packet(
                FailureCause.TOOL, "capability", message, report=result_report,
                evidence={"decision_boundary": "candidate_compilation"},
            )
            return result_report, None
        selected = self._decide("select_candidate", result_report, candidates)
        return self._invoke(selected, result_report)

    def _fresh_candidates(self, report: Mapping[str, Any]) -> tuple[BoundOption, ...]:
        return tuple(
            option for option in self.toolkit.candidate_options(report)
            if option.option.plan_hash not in self.executed_plan_hashes
        )

    def _decide(
        self, decision_kind: str, report: Mapping[str, Any], options: Sequence[BoundOption],
    ) -> BoundOption:
        if self.decision_count >= self.config.max_decisions:
            raise LayaWorkflowError("decision budget exhausted")
        state = self._state(report, options)
        decision_id = f"decision-{self.decision_count + 1:04d}"
        self.journal.append(
            "decision",
            decision_id=decision_id,
            decision_kind=decision_kind,
            tick=int(report.get("tick") or 0),
            state=state,
            options=[option.state_option() for option in options],
        )
        decision = self.director.decide(
            decision_kind, state, [option.state_option() for option in options],
        )
        self.decision_count += 1
        self._last_decision_id = decision_id
        bound = self.toolkit.resolve_selection(decision, options)
        self.journal.append(
            "decision_result",
            decision_id=decision_id,
            decision_kind=decision_kind,
            tick=int(report.get("tick") or 0),
            selected_option_id=bound.option.option_id,
            confidence=float(decision["confidence"]),
            response=decision["response"],
            selection=decision.get("selection"),
            summary=bound.option.summary,
            outcome="pending",
        )
        return bound

    def _invoke(
        self, bound: BoundOption, report: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], dict[str, Any] | None]:
        transaction_id = _identifier(
            f"txn-{self.decision_count}-{bound.option.option_id}", "laya-transaction",
        )
        self.current_transaction = {
            "transaction_id": transaction_id,
            "operation_id": bound.option.option_id,
            "status": "executing",
            "scope": dict(bound.option.scope),
            "started_tick": max(0, int(report.get("tick") or 0)),
            "expected_effects": [{
                "metric": "objective_progress", "direction": "increase", "target": None,
                "unit": "scenario_metric",
            }],
        }
        self.journal.append(
            "transaction_started", transaction_id=transaction_id,
            decision_id=self._last_decision_id, transaction=self.current_transaction,
            option=bound.state_option(),
            candidate=dict(bound.candidate) if bound.candidate is not None else None,
        )
        try:
            result = bound.handler()
        except (LayaToolkitError, StaleLayaOption):
            raise
        except Exception as error:
            if bound.candidate is not None:
                raise LayaToolkitError(f"candidate tool handler failed: {error}") from error
            raise
        if result.kind == "escalate":
            self.current_transaction["status"] = "escalated"
            self._record_decision_outcome("escalated")
            return report, self._terminate("escalated", report=report, reason=result.detail)
        next_report = dict(result.report or report)
        execution = dict(result.execution or {})
        if result.kind == "execute":
            next_report = dict(self.bridge.observe(self.episode_id))
            self.executed_plan_hashes.add(str(bound.option.plan_hash))
            self.current_transaction["status"] = "measuring"
            failed = int(execution.get("failed_placements", 0))
            if failed:
                self.current_transaction["status"] = "failed"
                message = f"candidate execution reported {failed} failed placement(s)"
                self.failed_candidate = bound.candidate
                self._activate_failure("execution", message, next_report)
                self._failure_packet(
                    FailureCause.TOOL, "execution", message, report=next_report,
                    evidence={"execution": execution, "option_id": bound.option.option_id},
                )
            else:
                self.active_failure = None
                self.failed_candidate = None
                self.retry_count = 0
        elif result.kind == "wait":
            self.current_transaction["status"] = "measuring"
        elif result.kind == "reselect":
            # Only a stale selection offers this; the next step re-enters
            # choose_operation against a fresh catalog.
            self.active_failure = None
            self.failed_candidate = None
            self.current_transaction["status"] = "completed"
        else:
            self.current_transaction["status"] = "completed"
        self.journal.append(
            "tool_result",
            transaction_id=transaction_id,
            option_id=bound.option.option_id,
            plan_hash=bound.option.plan_hash,
            kind=result.kind,
            detail=result.detail,
            execution=execution,
            observation=next_report,
        )
        self._record_decision_outcome(
            "failed" if self.active_failure is not None else (
                "pending" if result.kind in {"wait", "compile_candidates", "choose_operation"}
                else "completed"
            )
        )
        return next_report, None

    def _record_decision_outcome(self, outcome: str) -> None:
        if self._last_decision_id:
            self.journal.append(
                "decision_outcome", decision_id=self._last_decision_id, outcome=outcome,
            )

    def _record_observation(self, report: Mapping[str, Any]) -> None:
        self.journal.append(
            "observation", tick=int(report.get("tick") or 0), observation=dict(report),
        )
        self._measure_progress(report)

    @staticmethod
    def _progress_signature(report: Mapping[str, Any]) -> tuple[Any, ...]:
        metrics = report.get("metrics") or {}
        objective = report.get("objective") or {}
        return (
            report.get("status"), objective.get("stage_index"),
            metrics.get("delivered_items"), metrics.get("rate_per_tick"),
            metrics.get("sustained_ticks"), metrics.get("built_entities"),
        )

    def _measure_progress(self, report: Mapping[str, Any]) -> None:
        signature = self._progress_signature(report)
        if self.last_progress is None or signature != self.last_progress:
            self.no_progress = 0
            self.last_progress = signature
        else:
            self.no_progress += 1

    def _activate_failure(self, kind: str, message: str, report: Mapping[str, Any]) -> None:
        self.active_failure = {
            "failure_id": f"failure-{len(self.journal.events) + 1}",
            "kind": kind if kind in {
                "none", "safety", "identity", "budget", "fixture", "capability", "execution",
                "strategy", "power_unconnected", "timeout", "collision", "fluid_purity",
                "authorization", "scope", "contract", "unknown",
            } else "unknown",
            "state": "active",
            "message": str(message)[:512] or "unknown failure",
            "first_seen_tick": max(0, int(report.get("tick") or 0)),
            "active": True,
        }

    def _state(
        self, report: Mapping[str, Any], options: Sequence[BoundOption],
    ) -> dict[str, Any]:
        metrics = report.get("metrics") or {}
        objective_report = report.get("objective") or {}
        objective = self.scenario["objective"]
        target = float(objective_report.get("target_rate_per_tick", objective["target_rate_per_tick"]))
        current = float(metrics.get("rate_per_tick", 0.0))
        rules = list(self.knowledge.get("guidelines") or self.knowledge.get("rules") or ())[:8]
        if not rules:
            rules = [{
                "rule_id": "rule.runtime.isolation", "kind": "hard",
                "statement": "Operate only on the declared disposable training surface and force.",
            }]
        guideline_ref = self.knowledge.get("provenance_ref") or self.knowledge.get("provenance") or {
            "id": "guidelines.runner-default", "version": "1.0.0",
            "hash": sha256_hash({"rules": rules}),
        }
        registry_payload = [definition.tool_id for definition in self.toolkit.registry.definitions]
        tick = max(0, int(report.get("tick") or 0))
        state = {
            "version": "1.0.0",
            "run": {
                "run_id": self.run_id, "episode_id": self.episode_id,
                "tick": tick,
                "elapsed_ticks": max(0, int(report.get("elapsed_ticks") or 0)),
                "bootstrap_profile": str(self.scenario.get("bootstrap_profile") or "training"),
                "surface": str(report.get("surface") or self.scenario["environment"]["surface_name"]),
                "force": str(report.get("force") or self.scenario["environment"]["force_name"]),
                "runtime": "disposable_training",
            },
            "objective": {
                "family": str(self.scenario["family"]),
                "description": f"Meet the measured {self.scenario['family'].replace('_', ' ')} target.",
                "target": target, "unit": "items_per_tick",
            },
            "goal_stack": [{
                "goal_id": f"goal.{self.scenario['family']}",
                "kind": str(self.scenario["family"]),
                "objective": f"Reach and sustain {target:g} items per tick.",
                "status": "blocked" if self.active_failure else "active",
                "gap": max(0.0, target - current), "age_ticks": tick,
                "retry_count": self.retry_count,
                "last_failure_kind": (self.active_failure or {}).get("kind", "none"),
            }],
            "acceptance_gap": {
                "metric": "delivered_rate_per_tick", "current": current, "target": target,
                "remaining": max(0.0, target - current), "unit": "items_per_tick",
                "sustained_ticks": max(0, int(metrics.get("sustained_ticks") or 0)),
                "required_sustained_ticks": max(0, int(objective_report.get("sustain_ticks", objective.get("sustain_ticks", 0)))),
            },
            "current_transaction": self.current_transaction,
            "state_digest": {
                "power": {
                    "generation_kw": None, "firm_generation_kw": None, "accumulator_mj": None,
                    "unpowered_consumers": 0 if metrics.get("power_connected") is True else None,
                    "poles": int(metrics["electric_pole_count"]) if "electric_pole_count" in metrics else None,
                },
                "logistics": {
                    "robots": None, "providers_available": None,
                    "pending_ghosts": None, "ghost_blockages_top3": [],
                },
                "stock": {
                    "total": None, "accessible": None, "allocated": None, "top_shortages": [],
                },
                "production": {
                    "rate_now": current, "rate_target": target,
                    "sustained_ticks": max(0, int(metrics.get("sustained_ticks") or 0)),
                    "bottleneck_hint": str((report.get("failure") or {}).get("kind") or "none"),
                },
                "resource": {
                    "remaining": float(metrics["resource_remaining"]) if "resource_remaining" in metrics else None,
                    "patch_bounds_hint": None,
                },
                "space": {
                    "free_area_tiles": None, "reserved_area_tiles": None,
                    "occupied_footprint_tiles": int(metrics["occupied_footprint_tiles"]) if "occupied_footprint_tiles" in metrics else None,
                    "candidate_site_count": len(options),
                },
            },
            "recent_history": self.journal.recent_history()[-2:],
            "active_failures": [self.active_failure] if self.active_failure else [],
            "guidelines": rules,
            "options": [option.state_option() for option in options],
            "provenance": {
                "renderer_version": REQUEST_VERSION,
                "state_snapshot_hash": state_hash({"report": dict(report)}),
                "guideline_pack": dict(guideline_ref),
                "tool_registry": {
                    "id": "registry.laya-training-v1", "version": "1.0.0",
                    "hash": sha256_hash({"tools": registry_payload}),
                },
                "model": dict(self.model_ref),
            },
            "budget": {
                "materials_remaining": None,
                "construction_budget_remaining": None,
                "action_count_remaining": max(0, self.config.max_decisions - self.decision_count),
                "elapsed_ticks_remaining": None,
            },
        }
        return state

    def _failure_packet(
        self,
        cause: FailureCause,
        failure_kind: str,
        summary: str,
        *,
        report: Mapping[str, Any],
        evidence: Mapping[str, Any] | None = None,
    ) -> Path:
        packet = FailurePacket.create(
            cause=cause,
            failure_kind=_identifier(failure_kind, "unknown"),
            summary=str(summary)[:2048] or "unknown Laya runner failure",
            objective={
                "family": self.scenario["family"],
                "target": self.scenario["objective"].get("target_rate_per_tick"),
                "gap": max(0.0, float(self.scenario["objective"].get("target_rate_per_tick", 0.0)) - float((report.get("metrics") or {}).get("rate_per_tick", 0.0))),
            },
            replay_request={
                "runtime": "disposable_training", "scenario": self.scenario,
                "seed": int(self.scenario["seed"]),
                "initial_state": dict(self.initial_observation or report),
                "decision_context": {
                    "current_transaction": self.current_transaction,
                    "recent_history": self.journal.recent_history(),
                },
                "variations": [{"variation_id": "baseline"}],
                "limits": {"max_attempts": 8, "max_ticks": 60 * 60},
            },
            provenance={
                "run_id": self.run_id, "episode_id": self.episode_id,
                "trajectory_id": self.trajectory_id,
                "scenario_id": self.scenario["scenario_id"],
                "scenario_seed": int(self.scenario["seed"]),
                "scenario_hash": self.scenario["scenario_hash"],
                "repository_revision": self.config.repository_revision,
                "guideline_pack": self.knowledge.get("provenance_ref") or self.knowledge.get("provenance"),
                "tool_registry": {"id": "registry.laya-training-v1", "version": "1.0.0"},
                "model": {"id": self.model_ref["model_id"], "version": REQUEST_VERSION, "hash": self.model_ref["model_hash"]},
            },
            evidence={
                "decision_trace": self.journal.recent_history(),
                "execution": dict((evidence or {}).get("execution") or {}),
                "outcome": {"report": dict(report), **{key: value for key, value in (evidence or {}).items() if key != "execution"}},
            },
        )
        path = self.failures_dir / f"{packet.packet_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(packet.serialized() + "\n", encoding="utf-8")
        os.replace(temporary, path)
        self.journal.append(
            "failure_packet", packet_id=packet.packet_id, cause=packet.cause.value,
            failure_kind=packet.failure_kind, path=str(path), packet_hash=packet.packet_hash,
        )
        return path

    def _terminate(
        self,
        status: str,
        *,
        report: Mapping[str, Any],
        reason: str = "",
    ) -> dict[str, Any]:
        event = self.journal.append(
            "termination", status=status, reason=reason,
            final_observation=dict(report), decisions=self.decision_count,
            trajectory_id=self.trajectory_id,
        )
        return {
            "status": status,
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "trajectory_id": self.trajectory_id,
            "decisions": self.decision_count,
            "journal": str(self.journal.path),
            "termination_seq": event["seq"],
            "execution_authority": self.execution_authority,
            "capability_scope": {
                "scenario_families": ["mining_delivery", "furnace_refining"],
                "runtime": "disposable_training",
                "production_authority": False,
            },
        }


__all__ = [
    "CAPABILITY_FILE", "LayaPrimaryRunner", "LayaRunJournal", "LayaRunnerConfig",
    "LayaRunnerError", "REQUEST_VERSION", "load_checkpoint_capabilities",
]
