# Path: training/store.py
# Purpose: Persist scenarios, experience, policies, evaluations, and LLM timings in SQLite.

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from training.canonical import policy_hash
from training.contracts import validate_scenario, validate_transition


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
  scenario_id TEXT PRIMARY KEY, version TEXT NOT NULL, family TEXT NOT NULL,
  seed INTEGER NOT NULL, split TEXT NOT NULL, payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
  policy_id TEXT PRIMARY KEY, algorithm TEXT NOT NULL, generation INTEGER NOT NULL,
  parent_policy_id TEXT, config_json TEXT NOT NULL, state_json TEXT NOT NULL,
  policy_hash TEXT NOT NULL, created_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
  episode_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL, policy_id TEXT NOT NULL,
  worker_id TEXT NOT NULL, selection_seed INTEGER NOT NULL, status TEXT NOT NULL,
  started_tick INTEGER, ended_tick INTEGER, summary_json TEXT,
  started_utc TEXT NOT NULL, ended_utc TEXT,
  FOREIGN KEY(scenario_id) REFERENCES scenarios(scenario_id),
  FOREIGN KEY(policy_id) REFERENCES policies(policy_id)
);
CREATE TABLE IF NOT EXISTS transitions (
  episode_id TEXT NOT NULL, step_index INTEGER NOT NULL, chosen_action_id TEXT NOT NULL,
  reward_total REAL NOT NULL, payload_json TEXT NOT NULL,
  PRIMARY KEY(episode_id, step_index),
  FOREIGN KEY(episode_id) REFERENCES episodes(episode_id)
);
CREATE TABLE IF NOT EXISTS evaluations (
  evaluation_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, split TEXT NOT NULL,
  scenario_set_hash TEXT NOT NULL, fitness_json TEXT NOT NULL, frozen INTEGER NOT NULL,
  created_utc TEXT NOT NULL, FOREIGN KEY(policy_id) REFERENCES policies(policy_id)
);
CREATE TABLE IF NOT EXISTS champions (
  family TEXT PRIMARY KEY, policy_id TEXT NOT NULL, evaluation_id TEXT NOT NULL,
  promoted_utc TEXT NOT NULL, FOREIGN KEY(policy_id) REFERENCES policies(policy_id),
  FOREIGN KEY(evaluation_id) REFERENCES evaluations(evaluation_id)
);
CREATE TABLE IF NOT EXISTS research_proposals (
  proposal_id TEXT PRIMARY KEY, parent_policy_id TEXT NOT NULL, model TEXT NOT NULL,
  status TEXT NOT NULL, proposal_json TEXT NOT NULL, created_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_runtime_samples (
  sample_id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL,
  model_hash TEXT, context_size INTEGER NOT NULL, n_cpu_moe INTEGER,
  prompt_tokens INTEGER, completion_tokens INTEGER, elapsed_ms REAL NOT NULL,
  peak_ram_mb REAL, peak_vram_mb REAL, created_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_guidance (
  guidance_id TEXT PRIMARY KEY, focus TEXT NOT NULL, message TEXT NOT NULL,
  created_utc TEXT NOT NULL, expires_generation INTEGER, active INTEGER NOT NULL
);
"""

_GUIDANCE_FOCUS = {"throughput", "reliability", "efficiency", "exploration", "general"}


def _json(payload: Mapping) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrainingStore:
    """The supervisor-owned durable writer for all training evidence."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        with self.connection:
            self.connection.executescript(_SCHEMA)
            self.connection.execute("PRAGMA user_version=3")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "TrainingStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def save_scenario(self, payload: Mapping, split: str, payload_hash: str) -> None:
        validate_scenario(payload)
        if payload_hash != payload["scenario_hash"]:
            raise ValueError("payload_hash must equal the embedded scenario_hash")
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO scenarios VALUES (?,?,?,?,?,?,?)",
                (payload["scenario_id"], payload["version"], payload["family"],
                 payload["seed"], split, _json(payload), payload_hash),
            )

    def save_policy(
        self, policy_id: str, algorithm: str, generation: int,
        config: Mapping, state: Mapping, parent_policy_id: str | None = None,
    ) -> None:
        snapshot = dict(state) if state else dict(config)
        digest = policy_hash(snapshot)
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO policies VALUES (?,?,?,?,?,?,?,?)",
                (policy_id, algorithm, generation, parent_policy_id,
                 _json(config), _json(state), digest, _now()),
            )

    def start_episode(
        self, episode_id: str, scenario_id: str, policy_id: str,
        worker_id: str, selection_seed: int, status: str = "running",
    ) -> None:
        if status not in {"queued", "running"}:
            raise ValueError("new episode status must be queued or running")
        with self.connection:
            self.connection.execute(
                "INSERT INTO episodes VALUES (?,?,?,?,?,?,NULL,NULL,NULL,?,NULL)",
                (episode_id, scenario_id, policy_id, worker_id, selection_seed, status, _now()),
            )

    def finish_episode(
        self, episode_id: str, status: str, started_tick: int,
        ended_tick: int, summary: Mapping,
    ) -> None:
        if status not in {"completed", "failed", "timed_out"}:
            raise ValueError("invalid terminal episode status")
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE episodes SET status=?,started_tick=?,ended_tick=?,summary_json=?,ended_utc=? "
                "WHERE episode_id=?",
                (status, started_tick, ended_tick, _json(summary), _now(), episode_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown episode: {episode_id}")

    def mark_episode_running(self, episode_id: str) -> None:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE episodes SET status='running' WHERE episode_id=? AND status='queued'",
                (episode_id,),
            )
            if cursor.rowcount != 1:
                row = self.connection.execute(
                    "SELECT status FROM episodes WHERE episode_id=?", (episode_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"unknown episode: {episode_id}")

    def save_transition(self, payload: Mapping, step_index: int = 0) -> None:
        validate_transition(payload)
        identity = self.connection.execute(
            "SELECT scenarios.payload_hash,policies.policy_hash FROM episodes "
            "JOIN scenarios USING(scenario_id) JOIN policies USING(policy_id) "
            "WHERE episode_id=?", (payload["episode_id"],),
        ).fetchone()
        if identity is None:
            raise KeyError(f"unknown episode: {payload['episode_id']}")
        if payload["scenario_hash"] != identity[0] or payload["policy_hash"] != identity[1]:
            raise ValueError("transition identity hashes do not match the episode snapshot")
        with self.connection:
            self.connection.execute(
                "INSERT INTO transitions VALUES (?,?,?,?,?)",
                (payload["episode_id"], step_index, payload["chosen_action_id"],
                 payload["reward"]["total"], _json(payload)),
            )

    def save_evaluation(
        self, evaluation_id: str, policy_id: str, split: str,
        scenario_set_hash: str, fitness: Mapping, frozen: bool,
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO evaluations VALUES (?,?,?,?,?,?,?)",
                (evaluation_id, policy_id, split, scenario_set_hash,
                 _json(fitness), int(frozen), _now()),
            )

    def promote(self, family: str, policy_id: str, evaluation_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO champions VALUES (?,?,?,?)",
                (family, policy_id, evaluation_id, _now()),
            )

    def save_research_proposal(
        self, proposal_id: str, parent_policy_id: str, model: str,
        status: str, proposal: Mapping,
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO research_proposals VALUES (?,?,?,?,?,?)",
                (proposal_id, parent_policy_id, model, status, _json(proposal), _now()),
            )

    def save_llm_runtime_sample(self, sample: Mapping) -> None:
        fields = (
            "model", "model_hash", "context_size", "n_cpu_moe", "prompt_tokens",
            "completion_tokens", "elapsed_ms", "peak_ram_mb", "peak_vram_mb",
        )
        values = [sample.get(field) for field in fields]
        with self.connection:
            self.connection.execute(
                "INSERT INTO llm_runtime_samples "
                "(model,model_hash,context_size,n_cpu_moe,prompt_tokens,completion_tokens,"
                "elapsed_ms,peak_ram_mb,peak_vram_mb,created_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (*values, _now()),
            )

    def save_guidance(
        self, guidance_id: str, focus: str, message: str,
        expires_generation: int | None = None,
    ) -> None:
        normalized = message.strip()
        if focus not in _GUIDANCE_FOCUS:
            raise ValueError(f"unknown guidance focus: {focus}")
        if not normalized or len(normalized) > 1_000:
            raise ValueError("guidance message must contain 1 through 1000 characters")
        if expires_generation is not None and expires_generation < 0:
            raise ValueError("expires_generation cannot be negative")
        with self.connection:
            self.connection.execute(
                "INSERT INTO research_guidance VALUES (?,?,?,?,?,1)",
                (guidance_id, focus, normalized, _now(), expires_generation),
            )

    def active_guidance(self, generation: int | None = None, limit: int = 10) -> list[dict]:
        if limit < 1 or limit > 100:
            raise ValueError("guidance limit must be between 1 and 100")
        rows = self.connection.execute(
            "SELECT * FROM research_guidance WHERE active=1 "
            "AND (? IS NULL OR expires_generation IS NULL OR expires_generation>=?) "
            "ORDER BY created_utc DESC LIMIT ?", (generation, generation, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def dismiss_guidance(self, guidance_id: str) -> None:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE research_guidance SET active=0 WHERE guidance_id=?", (guidance_id,),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown guidance: {guidance_id}")

    def rows(self, table: str) -> list[sqlite3.Row]:
        allowed = {
            "scenarios", "policies", "episodes", "transitions", "evaluations",
            "champions", "research_proposals", "llm_runtime_samples", "research_guidance",
        }
        if table not in allowed:
            raise ValueError("table is not queryable through the training store")
        return list(self.connection.execute(f"SELECT * FROM {table}"))
