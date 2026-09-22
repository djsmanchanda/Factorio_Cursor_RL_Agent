#!/usr/bin/env python3
# Path: tools/run_laya_director.py
# Purpose: Launch the explicitly opted-in Laya-primary disposable runner.

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.factorio_bridge import FactorioTrainingBridge
from training.laya_runner import (
    LayaPrimaryRunner,
    LayaRunnerConfig,
    load_checkpoint_capabilities,
)
from training.laya_runtime import LayaModelPredictor
from training.laya_workflow import LayaWorkflowDirector
from training.scheduler import load_worker_specs


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run the bounded Laya-primary director on one disposable training worker.",
    )
    result.add_argument(
        "--mode", required=True, choices=("laya-primary-disposable",),
        help="Explicit opt-in; no production or deterministic fallback mode exists here.",
    )
    result.add_argument("--worker-id", required=True, help="Explicit disposable worker identity.")
    result.add_argument("--workers", type=Path, default=ROOT / "training-workers-linux.json", help="Configured disposable worker roster; endpoint must match this worker")
    result.add_argument("--scenario", required=True, type=Path)
    result.add_argument("--checkpoint", required=True, type=Path)
    result.add_argument("--base-model", type=Path)
    result.add_argument("--script-output", required=True, type=Path)
    result.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost", "::1"))
    result.add_argument("--port", required=True, type=int)
    result.add_argument("--password-env", default="FACTORIO_TRAINING_RCON_PASSWORD")
    result.add_argument("--evidence-dir", type=Path, default=ROOT / "data" / "laya-runs")
    result.add_argument("--run-id", default="")
    result.add_argument("--resume", action="store_true")
    result.add_argument("--max-decisions", type=int, default=40)
    result.add_argument("--max-no-progress", type=int, default=12)
    result.add_argument("--max-candidate-retries", type=int, default=1)
    result.add_argument("--confidence-threshold", type=float, default=0.70)
    result.add_argument("--device")
    return result


def main(argv: list[str] | None = None) -> int:
    from orchestrator.laya_knowledge import load_guideline_context

    args = parser().parse_args(argv)
    workers = [worker for worker in load_worker_specs(args.workers) if worker.worker_id == args.worker_id]
    if len(workers) != 1:
        parser().error("worker-id must resolve to exactly one configured disposable worker")
    worker = workers[0]
    if (worker.host, worker.rcon_port, worker.script_output.resolve()) != (args.host, args.port, args.script_output.resolve()):
        parser().error("endpoint and script-output must match the configured disposable worker")
    if args.resume and not args.run_id:
        parser().error("--resume requires the original --run-id")
    try:
        scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        parser().error(f"cannot read scenario: {error}")
    capabilities = load_checkpoint_capabilities(args.checkpoint, str(scenario.get("family", "")))
    password = os.environ.get(args.password_env)
    if not password:
        parser().error(f"RCON password environment variable {args.password_env!r} is not set")
    # The knowledge loader verifies source hashes against the curated catalog.
    # Its output is passed verbatim so the journal and failure packets retain
    # the exact guideline-pack provenance used for each decision.
    knowledge = load_guideline_context(
        ROOT,
        scopes=(
            str(scenario["family"]), "choose_operation", "select_candidate", "recovery",
        ),
        max_rules=8,
    )
    predictor = LayaModelPredictor(
        args.checkpoint, base_model_dir=args.base_model, device=args.device,
    )
    director = LayaWorkflowDirector(
        predictor,
        confidence_threshold=args.confidence_threshold,
        request_tokenizer=predictor.tokenizer,
    )
    bridge = FactorioTrainingBridge(
        args.script_output,
        host=args.host,
        port=args.port,
        password=password,
    )
    run_id = args.run_id or f"laya-{uuid.uuid4().hex[:16]}"
    try:
        bridge.handshake()
        result = LayaPrimaryRunner(
            bridge,
            scenario,
            director,
            LayaRunnerConfig(
                worker_id=args.worker_id,
                run_id=run_id,
                evidence_dir=args.evidence_dir,
                max_decisions=args.max_decisions,
                max_no_progress=args.max_no_progress,
                max_candidate_retries=args.max_candidate_retries,
                resume=args.resume,
            ),
            model_ref=capabilities,
            knowledge_context=knowledge,
        ).run()
    finally:
        bridge.close()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
