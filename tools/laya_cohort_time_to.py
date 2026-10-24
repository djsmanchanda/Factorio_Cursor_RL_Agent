# Path: tools/laya_cohort_time_to.py | Purpose: Time to each milestone (game minutes from the run's first decision), per arm and per matched seed pair.
"""Usage: time_to.py <cohort root> [--window-ticks 540000]

Strict C1-C6: the first fresh acceptance sample that passed (samples run every 10 decisions and at each
proxy phase change, so resolution is about ten decisions). Proxy C0-C6: the runner's PHASE REACHED tick.
Laya C6: the tick at which the explicit-choice receipt first passed.

Unreached milestones are censored at the window. Per arm the report gives reached/completed, the median
among reached runs, and RMST, the mean time with unreached runs counted at the window cap. RMST rewards
reaching sooner and reaching at all, so a lower RMST is faster progress. Pairs compare capped times seed
by seed (newer faster : older faster), and the median paired difference in minutes (negative = newer faster).
"""
import collections
import json
import statistics
import sys
from pathlib import Path

TPM = 3600  # ticks per game minute
MILESTONES = [f"strict {c}" for c in ("C1", "C2", "C3", "C4", "C5", "C6")] + ["Laya C6"] + \
             [f"proxy {c}" for c in ("C1", "C2", "C3", "C4", "C6")]


def times(job, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    path = Path(job["world"]) / "evidence/clean-decisions.jsonl"
    out = {}
    if not path.exists():
        return None
    start, by_index, phase_tick = None, {}, {}
    rows = []
    for line in path.open():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    for r in rows:
        if r.get("event") == "decision" and r.get("tick") is not None:
            by_index[r["index"]] = r["tick"]
            start = r["tick"] if start is None else min(start, r["tick"])
        elif r.get("event") == "phase_reached" and r.get("tick") is not None:
            phase_tick.setdefault(r["phase"], r["tick"])
    if start is None:
        return None
    for r in rows:
        if r.get("event") == "acceptance":
            at = str(r.get("at") or "")
            tick = r.get("tick")
            if tick is None:
                observed = {v.get("tick") for v in (r.get("foundations") or {}).values()
                            if isinstance(v, dict) and v.get("tick") is not None}
                if len(observed) == 1:
                    tick = observed.pop()
            if tick is not None:
                pass
            elif at.startswith("decision "):
                tick = by_index.get(int(at.split()[1]))
            elif at.startswith("proxy "):
                tick = phase_tick.get(at.split()[1])
            else:
                tick = start if at == "start" else None
            statuses = r.get("fresh") if "fresh" in r else r.get("statuses")
            for c, status in (statuses or {}).items():
                if status != "passed":
                    continue
                if tick is None:
                    if f"strict {c}" not in out:
                        diagnostics.setdefault("reached_time_unknown", set()).add(f"strict {c}")
                    continue
                observed_tick = tick
                if c == "C5":
                    receipt = (r.get("evidence") or {}).get("C5") or {}
                    if receipt.get("predicate_version") == "c5-v2" and receipt.get("qualified_tick") is not None:
                        observed_tick = receipt["qualified_tick"]
                if observed_tick < start:
                    diagnostics.setdefault("before_first_decision", set()).add(f"strict {c}")
                    observed_tick = start
                out.setdefault(f"strict {c}", (observed_tick - start) / TPM)
                diagnostics.setdefault("reached_time_unknown", set()).discard(f"strict {c}")
                if at == "end":
                    diagnostics.setdefault("end_observed", set()).add(f"strict {c}")
        elif r.get("event") == "laya_acceptance" and r.get("status") == "passed" and "Laya C6" not in out:
            tick = r.get("last_tick") or (r.get("observed") or {}).get("tick")
            if tick is not None:
                out["Laya C6"] = (tick - start) / TPM
    for c, tick in phase_tick.items():
        out[f"proxy {c}"] = (tick - start) / TPM
    return out


def main():
    root = Path(sys.argv[1])
    window = int(sys.argv[sys.argv.index("--window-ticks") + 1]) if "--window-ticks" in sys.argv else 540000
    cap = window / TPM
    cfg = json.loads((root / "batch.json").read_text())
    arms = [a["id"] for a in cfg["arms"]]
    jobs = json.loads((root / "job-status.json").read_text())["jobs"]

    per = {}
    unknown = {}
    for j in jobs:
        if j["status"] == "completed" and j.get("world"):
            detail = {}
            t = times(j, detail)
            if detail.get("reached_time_unknown"):
                print(f"WARNING {j['id']}: reached with unknown timing: {sorted(detail['reached_time_unknown'])}; timing summary excludes these endpoints")
            unknown[j["id"]] = detail.get("reached_time_unknown", set())
            if t is not None:
                per[j["id"]] = (j["arm"], j["seed"], t)

    print(f"{cfg['name']}: completed with timing {len(per)}/{len(jobs)} planned; window {cap:.0f} game min; minutes from first decision")
    print("milestone      | " + " | ".join(f"{a}: reached, median, RMST" for a in arms))
    for m in MILESTONES:
        cells = []
        for a in arms:
            vals = [t.get(m) for jid, (arm, _, t) in per.items() if arm == a and m not in unknown[jid]]
            reached = [v for v in vals if v is not None]
            rmst = statistics.mean([min(v, cap) if v is not None else cap for v in vals]) if vals else float("nan")
            med = f"{statistics.median(reached):.0f}" if reached else "-"
            cells.append(f"{len(reached)}/{len(vals)}, {med}, {rmst:.0f}")
        print(f"{m:14} | " + " | ".join(cells))
    seeds = collections.defaultdict(dict)
    for jid, (arm, seed, t) in per.items():
        seeds[seed][arm] = (t, unknown[jid])
    for old, new in zip(arms, arms[1:]):
        print(f"pairs {new} vs {old} (newer faster : older faster, median paired diff min; capped at window):")
        for m in MILESTONES:
            diffs, faster, slower = [], 0, 0
            for s, by in seeds.items():
                if old in by and new in by:
                    if m in by[old][1] or m in by[new][1]:
                        continue
                    a, b = by[old][0].get(m), by[new][0].get(m)
                    if a is None and b is None:
                        continue
                    ca, cb = min(a, cap) if a is not None else cap, min(b, cap) if b is not None else cap
                    diffs.append(cb - ca)
                    faster += cb < ca
                    slower += cb > ca
            if diffs:
                print(f"  {m:14} n={len(diffs)} {faster}:{slower} median {statistics.median(diffs):+.0f}")


if __name__ == "__main__":
    main()
