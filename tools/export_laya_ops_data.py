# Path: tools/export_laya_ops_data.py | Purpose: Export seed-isolated logged-action outcomes without inventing counterfactual training labels.
"""Build an auditable ops value-learning corpus from frozen campaign evidence.

Each row contains the exact decision input, legal options, logged choice and
outcome, plus a separate terminal achievement label. Unselected actions have no
label. This is an ops-outcome corpus, not a laya_case ranking dataset; fitting a
ranking policy requires measured alternatives or a separately evaluated value
estimator. No simulation, inference or training is launched here.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from jsonschema import Draft7Validator

_VALIDATOR = Draft7Validator(json.loads((Path(__file__).resolve().parents[1] /
    "schemas/laya_ops_outcome.schema.json").read_text()))

SPLITS = ("train", "calibration", "held_out", "test")


def seed_split(save_sha: str, seed: int, held_out_sources: set[str]) -> str:
    """Keep every arm, repeat and decision for a copied-save/seed in one split."""
    if save_sha in held_out_sources:
        return "test"
    bucket = int(hashlib.sha256(f"ops-split-v1:{save_sha}:{seed}".encode()).hexdigest(), 16) % 100
    return "train" if bucket < 80 else "calibration" if bucket < 90 else "held_out" if bucket < 95 else "test"


def export(batches: list[Path], output: Path, *, world_root: Path | None = None,
           held_out_sources: set[str] | None = None) -> dict:
    """Retain all planned-job accounting, exporting completed observed decisions only."""
    held_out_sources = held_out_sources or set()
    if output.exists():
        raise ValueError("output already exists; use a new immutable export directory")
    if len({p.resolve() for p in batches}) != len(batches):
        raise ValueError("duplicate batch input")
    loaded = []
    for root in batches:
        freeze_bytes = (root / "freeze.json").read_bytes()
        freeze = json.loads(freeze_bytes)
        cfg = json.loads((root / "batch.json").read_text())
        status = json.loads((root / "job-status.json").read_text())
        save_sha = freeze["source_sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", save_sha):
            raise ValueError("missing source save SHA256")
        jobs = status["jobs"]
        expected = {(a["id"], seed) for a in cfg["arms"] for seed in cfg["seeds"]}
        actual = [(j["arm"], j["seed"]) for j in jobs]
        if len(actual) != len(set(actual)) or set(actual) != expected or freeze["planned_jobs"] != len(jobs):
            raise ValueError("planned-job accounting is incomplete or duplicated")
        arms = {a["id"]: a for a in freeze["arms"]}
        if any(not re.fullmatch(r"[0-9a-f]{40}", arms[a]["revision"]) for a, _ in expected):
            raise ValueError("missing frozen arm revision")
        loaded.append((root, cfg, freeze, hashlib.sha256(freeze_bytes).hexdigest(), jobs, arms))
    output.mkdir(parents=True)
    totals, excluded, sources, ledger = Counter(), Counter(), [], []
    handles = {split: (output / f"{split}.jsonl").open("w") for split in SPLITS}
    try:
        for root, cfg, freeze, freeze_sha, jobs, arms in loaded:
            sources.append({"batch": str(root.resolve()), "name": cfg["name"], "freeze_sha256": freeze_sha,
                            "source_sha256": freeze["source_sha256"], "planned_jobs": len(jobs),
                            "statuses": dict(Counter(j["status"] for j in jobs)), "arms": arms})
            for job in jobs:
                account = {"batch": cfg["name"], "job": job["id"], "status": job["status"], "rows": 0}
                ledger.append(account)
                if job["status"] != "completed":
                    excluded["job_" + job["status"]] += 1
                    continue
                world = (world_root / cfg["name"] / job["id"] if world_root else Path(job["world"]))
                path = world / "evidence/decision-states.jsonl"
                if not path.exists():
                    excluded["missing_decision_evidence"] += 1
                    account["evidence"] = "missing"
                    continue
                split = seed_split(freeze["source_sha256"], job["seed"], held_out_sources)
                seen = set()
                with path.open() as reader:
                    for line in reader:
                        try:
                            record = json.loads(line)
                            choice = record["choice"]["option_id"]
                            option_ids = [o["option_id"] for o in record["options"]]
                            identity = (record["run_id"], record["index"])
                            valid = (record["version"] == "laya-decision-record-v1"
                                     and isinstance(record["state"], dict) and bool(record["state"])
                                     and isinstance(record["outcome"], dict)
                                     and type(record["outcome"].get("ok")) is bool
                                     and len(set(option_ids)) == len(option_ids) and choice in option_ids
                                     and type(record["index"]) is int and record["index"] >= 0
                                     and isinstance(record["run_id"], str) and bool(record["run_id"]))
                            if not valid:
                                raise ValueError("incomplete decision record")
                        except (ValueError, KeyError, TypeError, AttributeError):
                            excluded["invalid_decision_record"] += 1
                            continue
                        if identity in seen:
                            excluded["duplicate_decision_record"] += 1
                            continue
                        seen.add(identity)
                        row = {"version": "laya-ops-outcome-v1", "split": split,
                               "state": record["state"], "options": record["options"],
                               "choice": record["choice"], "distribution": record.get("distribution"),
                               "observed_outcome": record["outcome"],
                               "terminal_outcome": {k: job.get(k) for k in
                                   ("runner_stop", "accepted_ever", "accepted_final", "laya_acceptance", "failures")},
                               "provenance": {"batch": cfg["name"], "job": job["id"], "seed": job["seed"],
                                   "arm": job["arm"], "run_id": record["run_id"], "index": record["index"],
                                   "tick": record.get("tick"), "revision": arms[job["arm"]]["revision"],
                                   "freeze_sha256": freeze_sha, "source_sha256": freeze["source_sha256"],
                                   "engine_sha256": freeze.get("engine_sha256"),
                                   "record_sha256": hashlib.sha256(line.encode()).hexdigest()}}
                        if not _VALIDATOR.is_valid(row):
                            excluded["invalid_outcome_row"] += 1
                            continue
                        handles[split].write(json.dumps(row, sort_keys=True) + "\n")
                        totals[split] += 1
                        account["rows"] += 1
    finally:
        for handle in handles.values():
            handle.close()
    manifest = {"version": "laya-ops-outcome-manifest-v1", "rows": dict(totals),
                "excluded": dict(excluded), "planned_jobs": ledger, "sources": sources,
                "split_key": "source-save-sha256 + policy-seed; shared across arms and repeats",
                "held_out_sources": sorted(held_out_sources),
                "generalization": "policy seed only; no unseen-terrain claim",
                "labels": "observed logged action and terminal trajectory; no counterfactual preference",
                "files": {s: hashlib.sha256((output / f"{s}.jsonl").read_bytes()).hexdigest() for s in SPLITS}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batches", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--world-root", type=Path,
                        help="local evidence mirror laid out as <root>/<batch-name>/<job-id>/evidence")
    parser.add_argument("--held-out-source-sha256", action="append", default=[],
                        help="reserve every seed/arm from this source save for the test split")
    args = parser.parse_args(argv)
    result = export(args.batches, args.output, world_root=args.world_root,
                    held_out_sources=set(args.held_out_source_sha256))
    print(json.dumps({"rows": result["rows"], "excluded": result["excluded"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
