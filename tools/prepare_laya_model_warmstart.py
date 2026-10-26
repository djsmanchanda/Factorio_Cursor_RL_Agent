# Path: tools/prepare_laya_model_warmstart.py | Purpose: Certify a conservative C35–37 weak-imitation release with grouped splits and complete inputs.
"""Local-postcondition supervision only; no episode-return or counterfactual labels."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from training.laya_model_policy import QUESTION, RENDERER, canonical, digest, render


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def records(source):
    p = Path(source["path"])
    raw = subprocess.run(["zstd", "-dcq", str(p)], check=True, capture_output=True).stdout if source["compression"] == "zstd" else p.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source["decompressed_sha256"] or len(raw) != source["decompressed_bytes"]:
        raise ValueError(f"source integrity mismatch: {p}")
    lines = raw.splitlines(keepends=True)
    if len(lines) != source["records"]:
        raise ValueError("source record count mismatch")
    return [(json.loads(line), hashlib.sha256(line).hexdigest()) for line in lines]


def relevant(state, option):
    """Relevance is observed demand, not a claim that the action is best."""
    obs = state.get("observation_digest", {})
    board = obs.get("factory_status", {})
    product = option.get("product")
    f = option.get("features") or {}
    reasons = []
    for n in board.get("needs", []):
        if n.get("item") == product and (n.get("required") or 0) > 0 and n.get("condition") in {
            "unbuilt", "under_capacity", "input_starved", "recipe_unavailable"}:
            reasons.append("direct_unmet_need")
    if (board.get("construction_shortfall", {}).get(product) or 0) > 0:
        reasons.append("construction_shortfall")
    if isinstance(f.get("shortage"), (int, float)) and f["shortage"] > 0:
        reasons.append("selected_positive_shortage")
    if isinstance(f.get("prerequisite_for"), list) and any(isinstance(x, str) and x for x in f["prerequisite_for"]):
        reasons.append("named_prerequisite")
    return sorted(set(reasons))


def eligible(record, faulted):
    option = next(o for o in record["options"] if o["option_id"] == record["choice"]["option_id"])
    out = record["outcome"]
    if faulted:
        return None, "unresolved_fault_suffix"
    if out.get("ok") is not True or out.get("code") not in {"submitted", "producing"}:
        return None, "no_qualified_submission_or_producer_receipt"
    if (option.get("features") or {}).get("unchanged_failure") is True:
        return None, "unchanged_failure"
    reasons = relevant(record["state"], option)
    if not reasons:
        return None, "relevance_unknown"
    return (option, reasons), None


def validate_record(r, previous):
    if r.get("version") != "laya-decision-record-v1":
        raise ValueError("unknown record version")
    ids = [o["option_id"] for o in r["options"]]
    if len(ids) != len(set(ids)) or r["choice"]["option_id"] not in ids:
        raise ValueError("invalid menu/choice identity")
    if previous and (r["run_id"] != previous["run_id"] or r["index"] != previous["index"]+1 or r["tick"] < previous["tick"]):
        raise ValueError("nonconsecutive trajectory")
    distribution = r["distribution"]
    from training.laya_model_policy import probabilities
    values = {d["option_id"]: d["probability"] for d in distribution}
    if len(values) != len(distribution):
        raise ValueError("duplicate propensity ID")
    probabilities(values, ids)
    if abs(values[r["choice"]["option_id"]] - r["choice"]["probability"]) > 1e-9:
        raise ValueError("behavior propensity mismatch")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-index", type=Path, required=True)
    ap.add_argument("--tokenizer", required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_index_sha = file_sha(args.source_index)
    renderer_sha = file_sha(ROOT / "training/laya_model_policy.py")
    index = json.loads(args.source_index.read_text())
    if len(index["jobs"]) != 360 or index["counts"]["records"] != 109446:
        raise ValueError("unexpected planned C35–37 denominator")
    from kev.api import SystemOneRequest, to_record
    from kev.data import augment, materialize
    from kev.model import encode, load_tokenizer
    tok = load_tokenizer(args.tokenizer)
    contracts = {f"{c['cohort']}/{arm}": value for c in index["cohorts"] for arm, value in c["contracts"].items()}
    # Both arms and all retries for a seed share one fixed within-map partition.
    splits = {}
    for c in index["cohorts"]:
        seeds = sorted({j["seed"] for j in index["jobs"] if j["cohort"] == c["cohort"]}, key=lambda s: digest([c["source_save_sha256"], s]))
        if len(seeds) != 60:
            raise ValueError("unexpected cohort seed groups")
        for i, seed in enumerate(seeds):
            splits[seed] = "train" if i < 42 else "calibration" if i < 51 else "dev"
    counts, candidates, ledger = Counter(), [], []
    exclusion = (out / "exclusions.jsonl").open("w")
    for job in index["jobs"]:
        if contracts[job["contract_ref"]]["unverified_files"]:
            raise ValueError(f"unverified historical receipt contract: {job['contract_ref']}")
        sources = job["sources"]
        if not sources or any(s["integrity_issues"] for s in sources):
            raise ValueError(f"unresolved source: {job['episode_key']}")
        if len({s["decompressed_sha256"] for s in sources}) != 1:
            raise ValueError("conflicting source aliases")
        rows = records(sources[0])
        if len(rows) != job["terminal_metadata"]["decisions"]:
            raise ValueError("job decision denominator mismatch")
        faulted, previous, selected = False, None, []
        for r, record_sha in rows:
            validate_record(r, previous)
            previous = r
            counts["records"] += 1
            outcome = r["outcome"]
            faulted |= outcome.get("classification") in {"bug", "tool", "safety", "environment"} or outcome.get("code") == "untyped_stuck"
            result, reason = eligible(r, faulted)
            key = {"episode_key": job["episode_key"], "index": r["index"], "record_sha256": record_sha}
            if reason:
                counts["excluded:"+reason] += 1
                exclusion.write(canonical({**key, "reason": reason})+"\n")
                continue
            option, reasons = result
            request = render(r["state"], {"options": r["options"]})
            rec, _ = to_record(SystemOneRequest.model_validate(request))
            enc = encode(tok, rec, max_state=65536, max_branch=73728, strict=True)
            if enc["state_tokens"] > 7552 or len(enc["ids"]) > 8192:
                counts["excluded:complete_input_over_8192"] += 1
                exclusion.write(canonical({**key, "reason": "complete_input_over_8192", "state_tokens": enc["state_tokens"], "packed_tokens": len(enc["ids"])})+"\n")
                continue
            q = request["questions"][QUESTION]
            q.update(label=option["option_id"], src="factorio-local-receipt-weak-v1", target={i: float(i == option["option_id"]) for i in q["criteria"]})
            # Exercise the actual training permutation/materialization path.
            permuted = materialize(augment(request, random.Random(0), p_none=0, p_none_distract=0, p_distract=0))["questions"][0]
            if set(permuted["keys"]) != set(q["criteria"]) or permuted["keys"][permuted["target"].index(1.)] != q["label"]:
                raise ValueError("target-ID round trip failed")
            input_hash = digest({"state": request["state"], "criteria": q["criteria"], "instructions": q["instructions"]})
            selected.append({"request": request, "split": splits[job["seed"]], "input_sha256": input_hash,
                "provenance": {**key, "source_file_sha256": sources[0]["decompressed_sha256"],
                    "source_path": sources[0]["path"], "seed": job["seed"], "arm": job["arm"], "cohort": job["cohort"],
                    "run_id": r["run_id"], "tick": r["tick"], "phase": r["phase"], "choice": r["choice"],
                    "outcome": outcome, "contract_ref": job["contract_ref"], "relevance": reasons,
                    "state_tokens": enc["state_tokens"], "packed_tokens": len(enc["ids"]), "menu_size": len(r["options"])}})
        # Bound any one trajectory's influence; selection is fixed before training.
        selected.sort(key=lambda x: digest([x["input_sha256"], x["provenance"]["record_sha256"]]))
        candidates.extend(selected[:32])
        counts["excluded:trajectory_cap"] += max(0, len(selected)-32)
        ledger.append({**job, "eligible_before_cap": len(selected), "split": splits[job["seed"]]})
    exclusion.close()
    memberships = defaultdict(set)
    for r in candidates:
        memberships[r["input_sha256"]].add(r["split"])
    seen, shards = set(), {s: [] for s in ("train", "calibration", "dev")}
    provenance = (out / "provenance.jsonl").open("w")
    for r in candidates:
        h = r["input_sha256"]
        if len(memberships[h]) > 1:
            counts["excluded:cross_split_shared_input"] += 1
            continue
        if h in seen:
            counts["excluded:duplicate_input"] += 1
            continue
        seen.add(h)
        shards[r["split"]].append(r["request"])
        provenance.write(canonical({"input_sha256": h, "split": r["split"], **r["provenance"]})+"\n")
        counts["eligible:"+r["split"]] += 1
        counts["eligible_phase:"+r["provenance"]["phase"]] += 1
        counts["eligible_menu:"+str(r["provenance"]["menu_size"])] += 1
    provenance.close()
    for split, rows in shards.items():
        if not rows:
            raise ValueError("empty grouped split")
        (out / f"{split}.jsonl").write_text("".join(canonical(r)+"\n" for r in rows))
    (out / "episode-ledger.json").write_text(canonical(ledger)+"\n")
    files = {p.name: file_sha(p) for p in out.iterdir() if p.is_file()}
    if source_index_sha != file_sha(args.source_index) or renderer_sha != file_sha(ROOT / "training/laya_model_policy.py"):
        raise ValueError("source index or renderer changed during export")
    manifest = {"version": "laya-receipt-weak-release-v1", "complete": True, "train_ready": True,
        "learning": "supervised weak imitation of relevant local submission/producer receipts; not optimality or RL",
        "planned_jobs": len(ledger), "counts": dict(counts), "renderer": RENDERER,
        "source_index_sha256": source_index_sha, "script_sha256": file_sha(__file__),
        "renderer_sha256": renderer_sha, "files": files,
        "excluded_cohorts": ["C38", "C39"], "max_packed_tokens": 8192, "max_state_tokens": 7552,
        "split": "42/9/9 seed groups per cohort, SHA-sorted source-save+seed; both arms together; shared inputs quarantined",
        "limitations": ["one map", "selection bias", "no useful-wait targets in v1", "fault suffixes excluded", "max32 rows per trajectory", "episode returns unused"]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(json.dumps({"output": str(out), "planned_jobs": len(ledger), "counts": counts}))


if __name__ == "__main__":
    main()
