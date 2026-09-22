# Path: tools/run_laya_shadow_batch.py
# Purpose: Run bounded disposable Laya-vs-baseline shadow decisions without granting Laya execution authority.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.candidates import mining_delivery_candidates
from training.episode import run_episode
from training.factorio_bridge import FactorioTrainingBridge
from training.laya_policy import LayaPolicy
from training.laya_guidance import LayaGuidanceStore
from training.laya_runtime import LayaModelPredictor
from training.laya_shadow import state_builder_for_scenario
from training.policies import DiagonalLinUCB
from training.scenarios.mining_delivery import generate_mining_delivery_curriculum
from training.scheduler import load_worker_specs


class _GpuLoadGate:
    def __init__(self, maximum: float | None, *, poll_seconds: float, wait_timeout: float) -> None:
        self.maximum = maximum
        self.poll_seconds = max(0.25, poll_seconds)
        self.wait_timeout = max(self.poll_seconds, wait_timeout)
        self._warned_missing = False

    def wait(self) -> None:
        if self.maximum is None:
            return
        if shutil.which("nvidia-smi") is None:
            raise RuntimeError("nvidia-smi is required for the GPU utilization guard")
        started = time.monotonic()
        while True:
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5, check=True,
                )
                utilization = float(result.stdout.strip().splitlines()[0].strip())
            except (OSError, subprocess.SubprocessError, ValueError, IndexError) as error:
                raise RuntimeError(f"GPU utilization guard failed: {error}") from error
            if utilization <= self.maximum:
                return
            if time.monotonic() - started >= self.wait_timeout:
                raise RuntimeError(
                    f"GPU utilization stayed at {utilization:.1f}% above the {self.maximum:.1f}% limit"
                )
            time.sleep(self.poll_seconds)


class _ThrottledPredictor:
    def __init__(self, predictor, gate: _GpuLoadGate, cooldown_seconds: float) -> None:
        self.predictor = predictor
        self.gate = gate
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        # LayaPolicy uses these attributes to measure the same request format
        # that the predictor will encode.  Keep the wrapper transparent to
        # budget and provenance code while retaining the GPU gate here.
        self.tokenizer = getattr(predictor, "tokenizer", None)
        self.request_tokenizer = self.tokenizer
        self.config = getattr(predictor, "config", None)
        self.max_len = getattr(predictor, "max_len", None)
        self.head_max_len = getattr(predictor, "head_max_len", None)

    def __getattr__(self, name):
        # Preserve access to predictor metadata added by the runtime without
        # making the throttling wrapper part of the model contract.
        return getattr(self.predictor, name)

    def predict(self, state, questions):
        self.gate.wait()
        result = self.predictor.predict(state, questions)
        if self.cooldown_seconds:
            time.sleep(self.cooldown_seconds)
        return result


def _artifact_paths(output: Path) -> dict[str, Path]:
    """Return the immutable artifact family for one shadow run."""
    return {
        "decisions": output,
        "transitions": Path(str(output) + ".transitions.jsonl"),
        "summary": output.with_suffix(output.suffix + ".summary.json"),
    }


def _assert_artifacts_available(paths: dict[str, Path]) -> None:
    """Fail closed rather than allowing a run to overwrite prior evidence."""
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing:
        raise FileExistsError(
            "shadow run artifacts already exist; choose a new --output path: "
            + ", ".join(existing)
        )


def _sha256_path(path: Path) -> str | None:
    """Hash a file or directory using stable relative names and bytes."""
    if not path.exists():
        return None
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return "sha256:" + digest.hexdigest()
    if not path.is_dir():
        return None
    files = sorted(item for item in path.rglob("*") if item.is_file())
    for item in files:
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with item.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _renderer_provenance() -> dict[str, str | None]:
    """Hash the code that renders and serves a shadow request."""
    paths = (
        ROOT / "training" / "laya_shadow.py",
        ROOT / "training" / "laya_policy.py",
        ROOT / "training" / "laya_runtime.py",
        ROOT / "orchestrator" / "laya_state.py",
        ROOT / "training" / "sequence_boundary.py",
        ROOT / "tools" / "run_laya_shadow_batch.py",
    )
    return {str(path.relative_to(ROOT)): _sha256_path(path) for path in paths if path.exists()}


def _write_json_line(stream, payload: dict) -> None:
    stream.write(json.dumps(payload, sort_keys=True) + "\n")
    stream.flush()


def _episode_artifact_record(
    *, scenario: dict, episode_id: str, decision_ids: list[str],
    transition: dict | None = None, error: str | None = None,
) -> dict:
    """Build one durable outcome record, including replay inputs on failure."""
    record = {
        "record_type": "episode_transition" if transition is not None else "episode_error",
        "record_id": f"{episode_id}:outcome",
        "episode_id": episode_id,
        "scenario_id": scenario["scenario_id"],
        "scenario_seed": scenario.get("seed"),
        "scenario_hash": scenario.get("scenario_hash"),
        "scenario_snapshot": dict(scenario),
        "decision_ids": list(decision_ids),
        "transition": transition,
    }
    if error is not None:
        record["error"] = error
    return record


def _baseline(path: Path) -> DiagonalLinUCB:
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return DiagonalLinUCB.from_dict(payload["policy"])
    from training.features import MINING_DELIVERY_FEATURES_V3

    return DiagonalLinUCB("shadow-baseline-initial", MINING_DELIVERY_FEATURES_V3)


def _should_continue(*, episodes: int, episode_limit: int, deadline: float | None) -> bool:
    """Bound count-based runs while allowing an explicit duration to own the horizon."""
    if deadline is not None:
        return time.monotonic() < deadline
    return episodes < episode_limit


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", required=True, type=Path)
    parser.add_argument("--rcon-secret-file", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--base-model", required=True, type=Path)
    parser.add_argument("--allow-legacy-sequence", action="store_true", help="Diagnostic shadow only: use the preserved old truncating representation.")
    parser.add_argument("--baseline-checkpoint", type=Path, default=Path("data/training/policy.json"))
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--attempts-per-scenario", type=int, default=1)
    parser.add_argument("--start-seed", type=int, default=0)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--live-directory", type=Path, default=Path("data/training/live"))
    parser.add_argument("--duration-hours", type=float, default=None)
    parser.add_argument("--max-gpu-utilization", type=float, default=75.0)
    parser.add_argument("--gpu-poll-seconds", type=float, default=2.0)
    parser.add_argument("--inference-cooldown-seconds", type=float, default=1.0)
    parser.add_argument("--gpu-wait-timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--guidance-path", type=Path,
        default=(Path(os.environ["LAYA_GUIDANCE_PATH"]) if os.environ.get("LAYA_GUIDANCE_PATH") else None),
        help="Optional Coach JSON/JSONL file or directory injected as high-level guidance.",
    )
    args = parser.parse_args(argv)
    if args.count < 1 or args.attempts_per_scenario < 1:
        parser.error("count and attempts-per-scenario must be positive")
    if args.duration_hours is not None and args.duration_hours <= 0:
        parser.error("duration-hours must be positive")
    if not 1.0 <= args.max_gpu_utilization <= 100.0:
        parser.error("max-gpu-utilization must be between 1 and 100")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    artifact_paths = _artifact_paths(args.output)
    _assert_artifacts_available(artifact_paths)
    workers = load_worker_specs(args.workers)
    if not workers:
        raise SystemExit("no disposable workers available")
    worker = workers[0]
    password = args.rcon_secret_file.read_text(encoding="utf-8").strip()
    scenarios = generate_mining_delivery_curriculum(args.count, args.start_seed)
    predictor = LayaModelPredictor(args.checkpoint, base_model_dir=args.base_model, allow_legacy_sequence=args.allow_legacy_sequence)
    provenance = {
        "sequence_version": getattr(predictor, "sequence_version", "unknown"),
        "legacy_diagnostic": args.allow_legacy_sequence,
        "checkpoint": {
            "path": str(args.checkpoint),
            "sha256": _sha256_path(args.checkpoint),
        },
        "base_model": {
            "path": str(args.base_model),
            "sha256": _sha256_path(args.base_model),
        },
        "baseline": {
            "path": str(args.baseline_checkpoint),
            "sha256": _sha256_path(args.baseline_checkpoint),
        },
        "guidance": None if args.guidance_path is None else {
            "path": str(args.guidance_path),
            "sha256": _sha256_path(args.guidance_path),
        },
        "renderer": _renderer_provenance(),
    }
    gate = _GpuLoadGate(
        args.max_gpu_utilization,
        poll_seconds=args.gpu_poll_seconds,
        wait_timeout=args.gpu_wait_timeout_seconds,
    )
    throttled_predictor = _ThrottledPredictor(predictor, gate, args.inference_cooldown_seconds)
    guidance_store = LayaGuidanceStore(args.guidance_path) if args.guidance_path else None
    baseline = _baseline(args.baseline_checkpoint)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.live_directory.mkdir(parents=True, exist_ok=True)
    _assert_artifacts_available(artifact_paths)
    counts: Counter[str] = Counter()
    episodes = 0
    transitions = 0
    started_at = time.time()
    deadline = None if args.duration_hours is None else time.monotonic() + args.duration_hours * 3600.0
    episode_limit = args.count * args.attempts_per_scenario
    bridge = FactorioTrainingBridge(
        worker.script_output, host=worker.host, port=worker.rcon_port, password=password,
    )
    model_hash = (provenance["checkpoint"] or {}).get("sha256")
    with ExitStack() as artifacts:
        output = artifacts.enter_context(artifact_paths["decisions"].open("x", encoding="utf-8"))
        transition_output = artifacts.enter_context(
            artifact_paths["transitions"].open("x", encoding="utf-8")
        )
        try:
            bridge.handshake()
            index = 0
            attempt = 0
            while _should_continue(
                episodes=episodes, episode_limit=episode_limit, deadline=deadline,
            ):
                scenario = scenarios[index % len(scenarios)]
                episode_id = f"laya-shadow-{scenario['scenario_id']}-a{attempt:04d}-n{episodes:06d}"
                if deadline is not None and time.monotonic() >= deadline:
                    break
                index += 1
                if index % len(scenarios) == 0:
                    attempt += 1
                shadow_policy = LayaPolicy(
                    throttled_predictor,
                    state_builder_for_scenario(scenario),
                    policy_id="laya-shadow",
                    model_id=str(args.checkpoint),
                    **({"model_hash": model_hash} if model_hash else {}),
                    guidance_store=guidance_store,
                )

                def progress(event: dict) -> None:
                    if event.get("phase") != "shadow_decision":
                        return
                    decision = dict(event.get("decision") or {})
                    decision_index = sum(
                        1 for item in pending_decisions if item.get("episode_id") == episode_id
                    ) + 1
                    decision_id = f"{episode_id}:decision:{decision_index:04d}"
                    decision.update({
                        "record_type": "decision",
                        "decision_id": decision_id,
                        "episode_id": episode_id,
                        "scenario_id": scenario["scenario_id"],
                        "attempt": attempt,
                        "stage_index": event.get("stage_index", 1),
                        "outcome_ref": {
                            "record_id": f"{episode_id}:outcome",
                            "sidecar": str(artifact_paths["transitions"]),
                            "status": "pending",
                        },
                    })
                    pending_decisions.append(decision)
                    _write_json_line(output, decision)
                    counts["decisions"] += 1
                    counts["agreements"] += int(bool(decision.get("agreement")))
                    counts["escalations"] += int(decision.get("status") == "escalated")
                    counts["laya_selected"] += int(decision.get("status") == "selected")

                try:
                    pending_decisions: list[dict] = []
                    transition = run_episode(
                        bridge,
                        scenario,
                        mining_delivery_candidates(scenario),
                        baseline,
                        episodes,
                        episode_id=episode_id,
                        shadow_policy=shadow_policy,
                        update_policy=False,
                        on_progress=progress,
                    )
                    transitions += int(transition is not None)
                    counts["completed"] += int(transition["result"]["status"] == "completed")
                    counts["failed"] += int(transition["result"]["status"] != "completed")
                    counts["baseline_completed"] += int(transition["result"]["status"] == "completed")
                    counts["baseline_failed"] += int(transition["result"]["status"] != "completed")
                    _write_json_line(transition_output, _episode_artifact_record(
                        scenario=scenario,
                        episode_id=episode_id,
                        decision_ids=[item["decision_id"] for item in pending_decisions],
                        transition=transition,
                    ))
                except Exception as error:  # keep later disposable episodes running
                    counts["episode_errors"] += 1
                    counts["baseline_errors"] += 1
                    _write_json_line(output, {
                        "record_type": "episode_error",
                        "episode_id": episode_id,
                        "scenario_id": scenario["scenario_id"],
                        "status": "episode_error",
                        "error": f"{type(error).__name__}: {error}",
                        "outcome_ref": {
                            "record_id": f"{episode_id}:outcome",
                            "sidecar": str(artifact_paths["transitions"]),
                            "status": "episode_error",
                        },
                    })
                    _write_json_line(transition_output, _episode_artifact_record(
                        scenario=scenario,
                        episode_id=episode_id,
                        decision_ids=[item["decision_id"] for item in pending_decisions],
                        error=f"{type(error).__name__}: {error}",
                    ))
                episodes += 1
        finally:
            bridge.close()
    summary = {
        "status": "ready",
        "authority": "disposable_shadow_only",
        "checkpoint": str(args.checkpoint),
        "episodes": episodes,
        "transitions": transitions,
        "counts": dict(counts),
        "started_unix": started_at,
        "finished_unix": time.time(),
        "duration_hours": args.duration_hours,
        "max_gpu_utilization": args.max_gpu_utilization,
        "guidance_path": None if args.guidance_path is None else str(args.guidance_path),
        "output": str(args.output),
        "artifacts": {name: str(path) for name, path in artifact_paths.items()},
        "provenance": provenance,
        "decision_coverage": {
            "decisions": counts.get("decisions", 0),
            "laya_selected": counts.get("laya_selected", 0),
            "escalations": counts.get("escalations", 0),
            "selected_rate": (
                counts.get("laya_selected", 0) / counts["decisions"]
                if counts.get("decisions", 0) else 0.0
            ),
        },
        "baseline_completion": {
            "episodes": episodes,
            "completed": counts.get("baseline_completed", 0),
            "failed": counts.get("baseline_failed", 0),
            "errors": counts.get("baseline_errors", 0),
            "completed_rate": (
                counts.get("baseline_completed", 0) / episodes if episodes else 0.0
            ),
        },
    }
    with artifact_paths["summary"].open("x", encoding="utf-8") as summary_output:
        summary_output.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
