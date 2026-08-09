# Path: tools/run_autoresearch.py
# Purpose: Request and persist one bounded local-model policy experiment.

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.canonical import policy_hash
from training.research.controller import (
    AutoresearchController,
    build_research_packet,
    has_plateau,
)
from training.research.llama_client import LlamaCppClient
from training.research.proposals import POLICY_RANGES, PolicyProposal
from training.store import TrainingStore
from training.telemetry import WorkerTelemetry, publish_best_effort


_GUIDANCE_LIMIT = 5


def _read(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected one JSON object: {path}")
    return payload


def _allowed_changes() -> dict:
    return {
        name: {"minimum": low, "maximum": high, "type": kind.__name__}
        for name, (low, high, kind) in POLICY_RANGES.items()
    }


def _evidence_generation(evidence: dict) -> int | None:
    generation = evidence.get("generation")
    if generation is None:
        return None
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ValueError("evidence generation must be a non-negative integer")
    return generation


def _policy_payload(document: dict) -> dict:
    nested = document.get("policy")
    if nested is None:
        return document
    if not isinstance(nested, dict):
        raise ValueError("policy checkpoint field must be one JSON object")
    return nested


def _verify_parent_policy(database: Path, parent_policy_id: str, payload: dict) -> None:
    with TrainingStore(database) as store:
        row = store.connection.execute(
            "SELECT policy_hash FROM policies WHERE policy_id=?", (parent_policy_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"parent policy is not stored: {parent_policy_id}")
    if row[0] != policy_hash(payload):
        raise ValueError("policy checkpoint does not match the stored parent policy")

def _load_active_guidance(
    database: Path, parent_policy_id: str, evidence: dict,
) -> list[dict]:
    with TrainingStore(database) as store:
        row = store.connection.execute(
            "SELECT generation FROM policies WHERE policy_id=?", (parent_policy_id,),
        ).fetchone()
        generation = int(row[0]) if row is not None else _evidence_generation(evidence)
        if generation is not None:
            return store.active_guidance(generation, limit=_GUIDANCE_LIMIT)
        guidance = store.active_guidance(limit=100)
        permanent = [item for item in guidance if item["expires_generation"] is None]
        return permanent[:_GUIDANCE_LIMIT]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one bounded local autoresearch proposal.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-hash")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=Path("data/training/experience.db"))
    parser.add_argument("--live-directory", type=Path, default=Path("data/training/live"))
    parser.add_argument("--parent-policy-id", required=True)
    parser.add_argument("--context-size", type=int, default=32768)
    parser.add_argument("--n-cpu-moe", type=int)
    return parser


def _event(telemetry: WorkerTelemetry | None, phase: str, **fields: object) -> None:
    publish_best_effort(telemetry, {"kind": "autoresearch", "phase": phase, **fields})


def _save_result(
    args: argparse.Namespace, proposal: PolicyProposal | None, elapsed_ms: float,
    usage: dict | None = None,
) -> str | None:
    proposal_id, usage = None, usage or {}
    with TrainingStore(args.database) as store:
        store.save_llm_runtime_sample({
            "model": args.model, "model_hash": args.model_hash,
            "context_size": args.context_size, "n_cpu_moe": args.n_cpu_moe,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "elapsed_ms": elapsed_ms,
        })
        if proposal is not None:
            proposal_id = f"proposal-{uuid.uuid4().hex}"
            store.save_research_proposal(
                proposal_id, args.parent_policy_id, args.model, "proposed", proposal.to_dict(),
            )
    return proposal_id

def _run(args: argparse.Namespace, telemetry: WorkerTelemetry | None) -> dict:
    evidence = _read(args.evidence)
    scores = [float(value) for value in evidence.get("scores", [])]
    policy = _policy_payload(_read(args.policy))
    _verify_parent_policy(args.database, args.parent_policy_id, policy)
    guidance = _load_active_guidance(args.database, args.parent_policy_id, evidence)
    packet = build_research_packet(
        policy, evidence.get("experiments", []),
        evidence.get("failure_counts", {}), _allowed_changes(), guidance,
    )
    _event(
        telemetry, "evidence_loaded", score_count=len(scores),
        guidance_count=len(packet["research_guidance"]),
    )
    if not has_plateau(scores):
        reason = "recent scores have not plateaued"
        _event(telemetry, "skipped", reason=reason)
        return {"status": "skipped", "reason": reason}
    _event(telemetry, "requesting_model", model=args.model)
    client = LlamaCppClient(args.endpoint, args.model)
    started = time.perf_counter()
    try:
        proposal = AutoresearchController(client).propose(scores, packet)
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        _save_result(args, None, elapsed_ms, getattr(client, "last_usage", {}))
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    proposal_id = _save_result(
        args, proposal, elapsed_ms, getattr(client, "last_usage", {}),
    )
    _event(telemetry, "proposal_recorded", proposal_id=proposal_id, model=args.model)
    return {"status": "proposed", "proposal": proposal.to_dict()}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        telemetry = WorkerTelemetry(args.live_directory, "autoresearch")
    except OSError:
        telemetry = None
    try:
        result = _run(args, telemetry)
    except Exception as exc:
        _event(
            telemetry, "failed", error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        raise
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
