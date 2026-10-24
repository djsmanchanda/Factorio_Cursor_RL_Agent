# Path: tools/laya_release_counts.py | Purpose: Count journal refusals and runner-only loan-release failures without double-counting worlds.
import collections
import json
import sys
from pathlib import Path


def counts(root):
    result = collections.defaultdict(collections.Counter)
    for job in json.loads((Path(root) / "job-status.json").read_text())["jobs"]:
        if not job.get("world"):
            continue
        world, counter = Path(job["world"]), result[job["arm"]]
        counter["worlds"] += 1
        journal = world / "evidence/decision-states.jsonl"
        journal_hit = False
        if journal.exists():
            for line in journal.open():
                outcome = json.loads(line).get("outcome") or {}
                code, details = outcome.get("code"), outcome.get("details") or {}
                reasons = {b.get("reason") for b in details.get("blockers") or []} | set(details.get("reasons") or [])
                if code in ("infrastructure_collision", "mall_cell_lifecycle_conflict") and any(str(r).startswith("release_") for r in reasons):
                    counter[f"journal:{code}:{','.join(sorted(map(str, reasons)))}"] += 1
                    journal_hit = True
        else:
            counter["missing_journals"] += 1
        log = world / "logs/laya-runner.log"
        failures = []
        if log.exists():
            failures = [line for line in log.open(errors="replace") if "LOAN RELEASE FAILED:" in line]
        else:
            counter["missing_runner_logs"] += 1
        log_refusal = any("mall cell release refused" in line for line in failures)
        counter["runner_release_failure_events"] += len(failures)
        counter["worlds_with_journal_refusal"] += journal_hit
        counter["worlds_with_runner_release_failure"] += bool(failures)
        counter["worlds_with_refusal"] += journal_hit or log_refusal
        counter["worlds_with_any_release_issue"] += journal_hit or bool(failures)
        counter["worlds_in_both_sources"] += journal_hit and bool(failures)
    return {arm: dict(counter) for arm, counter in result.items()}


def main():
    print(json.dumps(counts(sys.argv[1]), sort_keys=True))


if __name__ == "__main__":
    main()
