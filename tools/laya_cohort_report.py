# Path: tools/laya_cohort_report.py | Purpose: Report strict C2 reliability with complete denominators, uncertainty and observed blockers.
from collections import Counter
import math


def terminal(job):
    return job["status"] not in {"queued", "preparing", "running"} and not job["status"].startswith("not_started")


def passed(job):
    return "C2" in (job.get("accepted_ever", job.get("accepted")) or [])


def wilson(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p, n = successes / total, float(total)
    center = p + z * z / (2 * n)
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    scale = 1 + z * z / n
    return [(center - radius) / scale, (center + radius) / scale]


def metrics(jobs, arms):
    rows = {}
    for arm in arms:
        planned = [j for j in jobs if j["arm"] == arm["id"]]
        ended = [j for j in planned if terminal(j)]
        misses = [j for j in ended if not passed(j)]
        checks, blockers, faults = Counter(), Counter(), Counter()
        known_checks = 0
        for job in misses:
            sample = job.get("c2_latest_checks")
            if sample is not None:
                known_checks += 1
                checks.update(k for k, v in sample.items() if v is False)
            # Job counts: a repeated failure in one job never becomes several independent runs.
            blockers.update((job.get("recent_typed_outcomes") or {}).keys())
        for job in ended:
            faults.update({k: v for k, v in (job.get("typed_outcomes") or {}).items()
                           if k in {"tool_error", "untyped_stuck", "infrastructure_collision", "power_route_unavailable"}})
        wins = sum(passed(j) for j in ended)
        rows[arm["id"]] = {
            "planned": len(planned), "statuses": dict(Counter(j["status"] for j in planned)),
            "terminal": len(ended), "strict_c2_ever": wins,
            "strict_c2_final": sum("C2" in (j.get("accepted_final") or []) for j in ended),
            "final_observed": sum(j.get("accepted_final") is not None for j in ended),
            "acceptance_unknown": sum(j.get("accepted_ever", j.get("accepted")) is None for j in ended),
            "c2_rate_terminal": wins / len(ended) if ended else None,
            "c2_wilson_95": wilson(wins, len(ended)),
            "missing_c2": len(misses), "missing_c2_checks_observed": known_checks,
            "c2_failed_checks_jobs": dict(checks), "recent_blocker_jobs": dict(blockers),
            "tool_fault_events": dict(faults),
            "maintenance_failure_events": sum(j.get("maintenance_failure_events", 0) for j in ended),
            "malformed_journal_rows": sum(j.get("malformed_journal_rows", 0) for j in ended),
            "revision": arm.get("revision"),
        }
    pairs = []
    for old, new in zip(arms, arms[1:]):
        a = {j["seed"]: j for j in jobs if j["arm"] == old["id"] and terminal(j)}
        b = {j["seed"]: j for j in jobs if j["arm"] == new["id"] and terminal(j)}
        shared = a.keys() & b.keys()
        pairs.append({"old": old["id"], "new": new["id"], "terminal_pairs": len(shared),
                      "new_only": sum(passed(b[s]) and not passed(a[s]) for s in shared),
                      "old_only": sum(passed(a[s]) and not passed(b[s]) for s in shared)})
    return {"version": "laya-cohort-report-v1", "predicate": "c2-v1", "arms": rows, "adjacent_pairs": pairs}


def markdown(report):
    lines = ["Strict C2 (`c2-v1`): two ready foundations with delivered output and both metal starters absent.", "",
             "| Arm | Strict C2 / terminal | Observed rate (95% interval) | Confirmed / planned | Final C2 (samples) | Unknown acceptance |",
             "|---|---:|---|---:|---:|---:|"]
    for arm, row in report["arms"].items():
        interval = row["c2_wilson_95"]
        rate = (f"{row['c2_rate_terminal']:.1%} ({interval[0]:.1%}–{interval[1]:.1%})" if interval else "pending")
        lines.append(f"| {arm} | {row['strict_c2_ever']}/{row['terminal']} | {rate} | "
                     f"{row['strict_c2_ever']}/{row['planned']} | {row['strict_c2_final']} "
                     f"(n={row['final_observed']}) | {row['acceptance_unknown']} |")
    lines += ["Terminal denominators include crashes, preparation errors, wall caps and interrupted attempts. "
              "Queued/running/unstarted jobs remain in planned counts; they are not completed failures. "
              "The interval describes run variation and does not control host timing or prove terrain generalization.", ""]
    for arm, row in report["arms"].items():
        lines += [f"- {arm}: all statuses {row['statuses']}. Missing C2 checks observed in "
                  f"{row['missing_c2_checks_observed']}/{row['missing_c2']} terminal non-passes; "
                  f"failed checks by job {row['c2_failed_checks_jobs']}. "
                  f"Recent typed blockers by job {row['recent_blocker_jobs']}. "
                  f"Tool fault events {row['tool_fault_events']}; maintenance failures {row['maintenance_failure_events']}; "
                  f"malformed journal rows {row['malformed_journal_rows']}."]
    lines += ["", "Blockers are the last 20 non-OK outcomes per job, not proven causes. "
              "Checks use the latest journal sample; missing evidence stays unknown. "
              "Routine typed supply waits are separate from tool faults.", ""]
    for pair in report["adjacent_pairs"]:
        lines.append(f"- Strict C2 {pair['new']} vs {pair['old']}: {pair['terminal_pairs']} terminal seed pairs; "
                     f"new-only {pair['new_only']}, old-only {pair['old_only']}. Timing remains uncontrolled.")
    return lines


def main():
    import argparse
    import json
    from pathlib import Path
    from tools.run_laya_batch import summarize

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="Separate output directory; frozen cohort evidence is read-only")
    parser.add_argument("--refresh-evidence", action="store_true")
    args = parser.parse_args()
    if args.output_dir.resolve() == args.root.resolve() or args.output_dir.resolve().is_relative_to(args.root.resolve()):
        parser.error("write refreshed reports outside the preserved cohort")
    config = json.loads((args.root / "batch.json").read_text())
    jobs = json.loads((args.root / "job-status.json").read_text())["jobs"]
    if args.refresh_evidence:
        for job in jobs:
            if terminal(job) and job.get("world") and (Path(job["world"]) / "logs/laya-runner.log").exists():
                job.update(summarize(Path(job["world"])))
    report = {"name": config["name"], **metrics(jobs, config["arms"])}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.output_dir / "report.md").write_text("\n".join([f"# {config['name']}", "", *markdown(report)]) + "\n")
    print(args.output_dir / "report.md")


if __name__ == "__main__":
    main()
