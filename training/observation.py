# Path: training/observation.py
# Purpose: Build a read-only dashboard snapshot from durable and live training evidence.

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from training.telemetry import read_live_workers


def _rows(
    connection: sqlite3.Connection, sql: str, parameters: tuple = (),
    *, optional_missing: bool = False,
) -> list[dict]:
    try:
        return [dict(row) for row in connection.execute(sql, parameters).fetchall()]
    except sqlite3.OperationalError as exc:
        if optional_missing and "no such table" in str(exc).lower():
            return []
        raise


def _open_readonly(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    return connection


def _json_object(value: str | None) -> dict:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _bottleneck(payload: Mapping) -> list[str]:
    result, metrics = payload.get("result") or {}, payload.get("metrics") or {}
    categories = []
    failure = result.get("failure_kind")
    if failure and failure != "none":
        categories.append(str(failure))
    if int(metrics.get("placements_failed", 0)) > 0:
        categories.append("placement_failure")
    if int(metrics.get("delivered_items", 0)) == 0:
        categories.append("no_delivery")
    initial = float(metrics.get("initial_rate_per_tick", 0))
    final = float(metrics.get("final_rate_per_tick", 0))
    if result.get("status") != "completed" and final <= initial:
        categories.append("no_throughput_gain")
    placed_drills = int(metrics.get("placed_mining_drills", 0))
    productive_ratio = float(metrics.get("productive_mining_drill_ratio", 0.0))
    if placed_drills > 0 and productive_ratio < 0.8:
        categories.append("low_productive_capacity")
    if float(metrics.get("route_excess_tiles", 0.0)) > 0:
        categories.append("route_excess")
    return categories or ["none"]


def _transition_evidence(rows: Iterable[Mapping]) -> tuple[list[dict], list[dict]]:
    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    recent = []
    for row in rows:
        payload = _json_object(row.get("payload_json"))
        for category in _bottleneck(payload):
            if category != "none":
                counts[category] += 1
                examples.setdefault(category, str((payload.get("result") or {}).get("reason", "")))
        recent.append({
            "episode_id": payload.get("episode_id"),
            "scenario_id": payload.get("scenario_id"),
            "status": (payload.get("result") or {}).get("status"),
            "failure_kind": (payload.get("result") or {}).get("failure_kind"),
            "chosen_action_id": payload.get("chosen_action_id"),
            "reward_total": (payload.get("reward") or {}).get("total"),
            "reward_components": {
                key: value for key, value in (payload.get("reward") or {}).items()
                if key != "total"
            },
            "efficiency": {
                "productive_mining_drill_ratio": (payload.get("metrics") or {}).get(
                    "productive_mining_drill_ratio"
                ),
                "route_efficiency": (payload.get("metrics") or {}).get("route_efficiency"),
                "route_excess_tiles": (payload.get("metrics") or {}).get("route_excess_tiles"),
                "occupied_footprint_tiles": (payload.get("metrics") or {}).get(
                    "occupied_footprint_tiles"
                ),
                "electric_pole_count": (payload.get("metrics") or {}).get(
                    "electric_pole_count"
                ),
            },
            "elapsed_ticks": max(0, int(payload.get("ended_tick", 0)) - int(payload.get("started_tick", 0))),
            "worker_id": row.get("worker_id"), "ended_utc": row.get("ended_utc"),
        })
    bottlenecks = [
        {"kind": kind, "count": count, "example": examples.get(kind, "")}
        for kind, count in counts.most_common()
    ]
    return recent, bottlenecks


def _age_seconds(timestamp: str | None) -> float | None:
    if not timestamp:
        return None
    try:
        updated = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - updated).total_seconds())


def _live_worker(payload: Mapping) -> dict:
    report = payload.get("report") if isinstance(payload.get("report"), Mapping) else {}
    metrics = report.get("metrics") if isinstance(report.get("metrics"), Mapping) else {}
    objective = report.get("objective") if isinstance(report.get("objective"), Mapping) else {}
    rate = float(metrics.get("rate_per_tick", 0.0))
    target = float(objective.get("target_rate_per_tick", 0.0))
    sustain = int(metrics.get("sustained_ticks", 0))
    sustain_target = int(objective.get("sustain_ticks", 0))
    return {
        "worker_id": payload.get("worker_id"), "phase": payload.get("phase"),
        "episode_id": payload.get("episode_id"), "scenario_id": payload.get("scenario_id"),
        "policy_id": payload.get("policy_id"), "status": report.get("status", payload.get("phase")),
        "surface": report.get("surface"), "tick": report.get("tick"),
        "elapsed_ticks": report.get("elapsed_ticks"),
        "rate_per_tick": rate, "target_rate_per_tick": target,
        "rate_ratio": rate / target if target > 0 else 0.0,
        "sustained_ticks": sustain, "sustain_target_ticks": sustain_target,
        "sustain_ratio": sustain / sustain_target if sustain_target > 0 else 0.0,
        "chosen_action_id": payload.get("chosen_action_id"), "error": payload.get("error"),
        "updated_utc": payload.get("updated_utc"),
        "age_seconds": _age_seconds(payload.get("updated_utc")),
    }


def _database_snapshot(connection: sqlite3.Connection) -> dict:
    status_rows = _rows(connection, "SELECT status,COUNT(*) count FROM episodes GROUP BY status")
    statuses = {row["status"]: row["count"] for row in status_rows}
    policies = _rows(
        connection, "SELECT policy_id,generation,algorithm,parent_policy_id,created_utc "
        "FROM policies ORDER BY generation DESC,created_utc DESC LIMIT 12",
    )
    transitions = _rows(
        connection, "SELECT t.payload_json,e.worker_id,e.ended_utc FROM transitions t "
        "JOIN episodes e USING(episode_id) ORDER BY e.ended_utc DESC LIMIT 200",
    )
    recent, bottlenecks = _transition_evidence(transitions)
    rewards = [float(item["reward_total"]) for item in recent if item["reward_total"] is not None]
    completed = int(statuses.get("completed", 0))
    terminal = completed + int(statuses.get("failed", 0)) + int(statuses.get("timed_out", 0))
    return {
        "statuses": statuses, "policies": policies, "recent_episodes": recent[:30],
        "bottlenecks": bottlenecks, "summary": {
            "episodes": sum(statuses.values()), "terminal_episodes": terminal,
            "completion_rate": completed / terminal if terminal else 0.0,
            "mean_reward": sum(rewards) / len(rewards) if rewards else 0.0,
            "generation": max((int(row["generation"]) for row in policies), default=0),
        },
        "proposals": _rows(
            connection, "SELECT proposal_id,parent_policy_id,model,status,proposal_json,created_utc "
            "FROM research_proposals ORDER BY created_utc DESC LIMIT 20",
        ),
        "guidance": _rows(
            connection, "SELECT * FROM research_guidance ORDER BY created_utc DESC LIMIT 20",
            optional_missing=True,
        ),
        "llm_runtime": _rows(
            connection, "SELECT model,model_hash,n_cpu_moe,COUNT(*) samples,"
            "AVG(elapsed_ms) mean_elapsed_ms,AVG(prompt_tokens) mean_prompt_tokens,"
            "AVG(completion_tokens) mean_completion_tokens,MAX(peak_ram_mb) peak_ram_mb,"
            "MAX(peak_vram_mb) peak_vram_mb FROM llm_runtime_samples "
            "GROUP BY model,model_hash,n_cpu_moe ORDER BY samples DESC",
        ),
    }


def _autoresearch_state(payload: Mapping | None) -> dict:
    if not payload:
        return {}
    return {
        "phase": payload.get("phase"), "model": payload.get("model"),
        "proposal_id": payload.get("proposal_id"), "reason": payload.get("reason"),
        "guidance_count": payload.get("guidance_count"),
        "score_count": payload.get("score_count"), "error": payload.get("error"),
        "error_type": payload.get("error_type"), "updated_utc": payload.get("updated_utc"),
        "age_seconds": _age_seconds(payload.get("updated_utc")),
    }


def _controller_snapshot(live_directory: Path | str) -> dict:
    """Read the controller's bounded capacity history without touching training state."""
    path = Path(live_directory) / "adaptive-controller.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    history = []
    for item in payload.get("ups_history", []):
        if not isinstance(item, Mapping):
            continue
        try:
            history.append({
                "stage": int(item["stage"]),
                "slots": int(item["slots"]),
                "mean_ups": float(item["mean_ups"]),
                "safe_ups_p95": float(item["safe_ups_p95"]),
                "safe_ups_p98": float(item["safe_ups_p98"]),
                "tick_time_p98_ms": float(item.get("tick_time_p98_ms", 0.0)),
                "timestamp_utc": str(item.get("timestamp_utc", "")),
            })
        except (KeyError, TypeError, ValueError):
            continue
    servers = []
    for item in payload.get("servers", []):
        if not isinstance(item, Mapping):
            continue
        try:
            servers.append({
                "instance_id": str(item["instance_id"]),
                "configured_slots": int(item["configured_slots"]),
                "active_slots": int(item["active_slots"]),
                "decision": item.get("decision"),
                "mean_ups": float(item["mean_ups"]) if item.get("mean_ups") is not None else None,
                "safe_ups_p95": float(item["safe_ups_p95"]) if item.get("safe_ups_p95") is not None else None,
                "safe_ups_p98": float(item["safe_ups_p98"]) if item.get("safe_ups_p98") is not None else None,
                "tick_time_p98_ms": float(item["tick_time_p98_ms"]) if item.get("tick_time_p98_ms") is not None else None,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "phase": payload.get("phase"),
        "stage": payload.get("stage"),
        "slots": payload.get("slots"),
        "generation": payload.get("generation"),
        "policy_cohort": payload.get("policy_cohort"),
        "policy_terminal_episodes": payload.get("policy_terminal_episodes"),
        "policy_episode_target": payload.get("policy_episode_target"),
        "mean_ups": payload.get("mean_ups"),
        "safe_ups_p95": payload.get("safe_ups_p95"),
        "safe_ups_p98": payload.get("safe_ups_p98"),
        "minimum_ups_p95": payload.get("minimum_ups_p95", 50.0),
        "minimum_ups_p98": payload.get("minimum_ups_p98", 45.0),
        "stability_window_seconds": payload.get("stability_window_seconds", 300.0),
        "servers": servers,
        "ups_history": history[-120:],
    }


def _empty_durable() -> dict:
    return {
        "statuses": {}, "policies": [], "recent_episodes": [], "bottlenecks": [],
        "summary": {"episodes": 0, "terminal_episodes": 0, "completion_rate": 0.0,
                    "mean_reward": 0.0, "generation": 0},
        "proposals": [], "guidance": [], "llm_runtime": [],
    }


def build_training_snapshot(database: Path | str, live_directory: Path | str) -> dict:
    """Return one stable JSON dashboard document without modifying training state."""
    database_path, connection = Path(database), None
    database_error = None
    try:
        connection = _open_readonly(database_path)
        durable = _database_snapshot(connection) if connection else _empty_durable()
    except sqlite3.Error as exc:
        durable = _empty_durable()
        database_error = f"{type(exc).__name__}: {exc}"
    finally:
        if connection:
            connection.close()
    live = read_live_workers(live_directory)
    workers = [_live_worker(item) for item in live if item.get("kind") != "autoresearch"]
    research = next((item for item in live if item.get("kind") == "autoresearch"), None)
    terminal_phases = {"finished", "failed", "worker_failed"}
    return {
        "version": "1.2.0", "generated_utc": datetime.now(timezone.utc).isoformat(),
        "database_present": database_path.is_file(), "database_error": database_error,
        "live_workers": workers, "autoresearch_live": _autoresearch_state(research),
        "adaptive_controller": _controller_snapshot(live_directory),
        "active_workers": sum(
            worker.get("age_seconds") is not None and worker["age_seconds"] < 30
            and worker.get("phase") not in terminal_phases for worker in workers
        ),
        **durable,
    }
