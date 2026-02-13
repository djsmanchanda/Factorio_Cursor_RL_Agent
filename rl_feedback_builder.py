# Path: rl_feedback_builder.py
# Purpose: Build deterministic, read-only RL feedback telemetry from latest validated artifacts.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class RLFeedback:
    reward_signal: float
    progress_delta: float
    capacity_delta: float
    phase_progress: dict
    anomalies_detected: list[str]
    attribution_hint: str
    deterministic_timestamp: str

    def to_dict(self) -> dict:
        return {
            "reward_signal": float(self.reward_signal),
            "progress_delta": float(self.progress_delta),
            "capacity_delta": float(self.capacity_delta),
            "phase_progress": dict(self.phase_progress),
            "anomalies_detected": list(self.anomalies_detected),
            "attribution_hint": self.attribution_hint,
            "deterministic_timestamp": self.deterministic_timestamp,
        }


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_schema(payload: dict, schema_path: Path, label: str) -> None:
    schema = _load_json(schema_path)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(payload))
    if errors:
        messages = []
        for error in errors:
            loc = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {loc}: {error.message}")
        raise ValueError(f"{label} validation FAILED:\n" + "\n".join(messages))


def _sum_execution_successes(execution_report: Optional[dict]) -> tuple[int, int]:
    if not execution_report:
        return 0, 0

    success_count = 0
    failed_count = 0
    for action in execution_report.get("actions", []):
        if action.get("status") == "success":
            success_count += int(action.get("count", 1))
        elif action.get("status") == "failed":
            failed_count += 1
    return success_count, failed_count


def _deterministic_timestamp(metrics_summary: dict, construction_report: Optional[dict], execution_report: Optional[dict]) -> str:
    ticks = []
    if isinstance(metrics_summary.get("tick"), int):
        ticks.append(int(metrics_summary["tick"]))
    if construction_report and isinstance(construction_report.get("tick"), int):
        ticks.append(int(construction_report["tick"]))
    if execution_report and isinstance(execution_report.get("tick"), int):
        ticks.append(int(execution_report["tick"]))
    return f"tick:{max(ticks) if ticks else 0}"


def build_rl_feedback(
    progress_state: dict,
    metrics_summary: dict,
    construction_report: Optional[dict] = None,
    execution_report: Optional[dict] = None,
    progress_schema_path: Path | None = None,
    construction_schema_path: Path | None = None,
    execution_schema_path: Path | None = None,
    feedback_schema_path: Path | None = None,
) -> RLFeedback:
    repo_root = Path(__file__).resolve().parent
    if progress_schema_path is None:
        progress_schema_path = repo_root / "schemas" / "progress_state.schema.json"
    if construction_schema_path is None:
        construction_schema_path = repo_root / "schemas" / "construction_report.schema.json"
    if execution_schema_path is None:
        execution_schema_path = repo_root / "schemas" / "execution_report.schema.json"
    if feedback_schema_path is None:
        feedback_schema_path = repo_root / "rl_feedback.schema.json"

    _validate_schema(progress_state, progress_schema_path, "ProgressState")
    if construction_report is not None:
        _validate_schema(construction_report, construction_schema_path, "ConstructionReport")
    if execution_report is not None:
        _validate_schema(execution_report, execution_schema_path, "ExecutionReport")

    if "current_capacity" not in metrics_summary:
        raise ValueError("latest metrics summary must include current_capacity")

    current_capacity = int(progress_state["current_capacity"])
    committed_capacity = int(progress_state["committed_capacity"])
    active_phase = int(progress_state["active_phase_capacity"])
    metrics_current = int(metrics_summary["current_capacity"])

    started = int(construction_report["started"]) if construction_report else 0
    completed = int(construction_report["completed"]) if construction_report else 0
    blocked = bool(construction_report.get("blocked")) if construction_report else False

    exec_successes, exec_failures = _sum_execution_successes(execution_report)

    anomalies = []
    if blocked:
        anomalies.append("construction_blocked")
    if completed > started:
        anomalies.append("construction_completed_exceeds_started")
    if exec_failures > 0:
        anomalies.append("execution_failures_present")
    if current_capacity > active_phase:
        anomalies.append("progress_exceeds_active_phase")
    if current_capacity != metrics_current:
        anomalies.append("progress_metrics_mismatch")
    anomalies = sorted(anomalies)

    progress_delta = float(current_capacity - committed_capacity)
    capacity_delta = float(completed)
    completion_ratio = 0.0 if active_phase == 0 else round(float(current_capacity) / float(active_phase), 6)

    reward = 0.0
    reward += 0.10 * float(completed)
    reward += 0.05 * float(exec_successes)
    reward -= 0.20 * float(exec_failures)
    if blocked:
        reward -= 0.30
    reward -= 0.10 * float(len(anomalies))
    if completion_ratio >= 1.0:
        reward += 0.02
    reward = round(max(-1.0, min(1.0, reward)), 6)

    if anomalies:
        attribution = "anomaly_penalty"
    elif completed > 0:
        attribution = "construction_progress"
    elif exec_successes > 0:
        attribution = "execution_success"
    else:
        attribution = "no_observed_change"

    feedback = RLFeedback(
        reward_signal=reward,
        progress_delta=round(progress_delta, 6),
        capacity_delta=round(capacity_delta, 6),
        phase_progress={
            "active_phase_capacity": active_phase,
            "current_capacity": current_capacity,
            "completion_ratio": completion_ratio,
        },
        anomalies_detected=anomalies,
        attribution_hint=attribution,
        deterministic_timestamp=_deterministic_timestamp(metrics_summary, construction_report, execution_report),
    )

    payload = feedback.to_dict()
    _validate_schema(payload, feedback_schema_path, "RLFeedback")
    return feedback


def main() -> int:
    import sys

    if len(sys.argv) < 3 or len(sys.argv) > 5:
        print(
            "Usage: python rl_feedback_builder.py <progress_state.json> <metrics_summary.json> "
            "[construction_report.json] [execution_report.json]"
        )
        return 1

    progress_path = Path(sys.argv[1]).resolve()
    metrics_path = Path(sys.argv[2]).resolve()
    construction_path = Path(sys.argv[3]).resolve() if len(sys.argv) >= 4 else None
    execution_path = Path(sys.argv[4]).resolve() if len(sys.argv) >= 5 else None

    progress_state = _load_json(progress_path)
    metrics_summary = _load_json(metrics_path)
    construction_report = _load_json(construction_path) if construction_path else None
    execution_report = _load_json(execution_path) if execution_path else None

    feedback = build_rl_feedback(
        progress_state=progress_state,
        metrics_summary=metrics_summary,
        construction_report=construction_report,
        execution_report=execution_report,
    )
    print(json.dumps(feedback.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
