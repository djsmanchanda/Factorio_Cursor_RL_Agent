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

from training.research.controller import AutoresearchController, build_research_packet
from training.research.llama_client import LlamaCppClient
from training.research.proposals import POLICY_RANGES
from training.store import TrainingStore


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded local autoresearch proposal.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-hash")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=Path("data/training/experience.db"))
    parser.add_argument("--parent-policy-id", required=True)
    parser.add_argument("--context-size", type=int, default=32768)
    parser.add_argument("--n-cpu-moe", type=int)
    args = parser.parse_args(argv)
    evidence = _read(args.evidence)
    scores = [float(value) for value in evidence.get("scores", [])]
    packet = build_research_packet(
        _read(args.policy), evidence.get("experiments", []),
        evidence.get("failure_counts", {}), _allowed_changes(),
    )
    client = LlamaCppClient(args.endpoint, args.model)
    started = time.perf_counter()
    proposal = AutoresearchController(client).propose(scores, packet)
    elapsed_ms = (time.perf_counter() - started) * 1000
    with TrainingStore(args.database) as store:
        store.save_llm_runtime_sample({
            "model": args.model, "model_hash": args.model_hash,
            "context_size": args.context_size, "n_cpu_moe": args.n_cpu_moe,
            "elapsed_ms": elapsed_ms,
        })
        if proposal is not None:
            proposal_id = f"proposal-{uuid.uuid4().hex}"
            store.save_research_proposal(
                proposal_id, args.parent_policy_id, args.model, "proposed", proposal.to_dict(),
            )
    result = {"status": "proposed", "proposal": proposal.to_dict()} if proposal else {
        "status": "skipped", "reason": "recent scores have not plateaued",
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
