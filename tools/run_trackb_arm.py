#!/usr/bin/env python3
# Path: tools/run_trackb_arm.py | Purpose: Run Track B's clean runner under the cohort scheduler's prepare/run/stop contract, measured by the shared strict sidecar.
"""Track B as a cohort arm.

The cohort scheduler calls an arm's runner with one fixed contract
(``prepare``/``run``/``stop`` plus ``--root``, ports, ``--seed``,
``--max-game-ticks``, ``--speed``, ``--variant``). Track B's own runner is
``tools/run_laya_clean.py`` (guided policy, proxy phases, no strict
predicates). This adapter translates the contract, runs B's runner as a child
(so the scheduler's ``scoped_stop`` still sees ``run_laya_clean.py`` and the
world in the runner's command line), and measures the world with
``tools/laya_strict_sidecar.py``. After the run it appends the sidecar's rows to
the world journal as ``strict_current`` and ``acceptance`` events tagged
``"source": "strict_sidecar"``, which the scheduler's ``collect_result`` reads
exactly as it reads Track A's in-run rows.

Unsupported scheduler options (restart bundles, strict/research targets,
checkpoint capture) fail loudly rather than being silently ignored.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
DEFAULT_VARIANT = "docs/rl/laya_variants/patience48.json"
UNSUPPORTED = ("--restart-checkpoint", "--strict-target", "--research-target", "--checkpoint-catalog",
               "--capture-checkpoints", "--checkpoint-bank", "--validate-reload-only")


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["prepare", "run", "stop"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--actions", default="deterministic", help="Accepted for the scheduler contract; B chooses its own")
    parser.add_argument("--game-port", type=int)
    parser.add_argument("--rcon-port", type=int)
    parser.add_argument("--mods-source", type=Path)
    parser.add_argument("--source-save", type=Path)
    parser.add_argument("--no-helper", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-game-ticks", type=int)
    parser.add_argument("--speed", type=float, default=2.0)
    parser.add_argument("--variant", default=DEFAULT_VARIANT)
    parser.add_argument("--sidecar-interval", type=float, default=60.0)
    for flag in UNSUPPORTED:
        parser.add_argument(flag, nargs="*", dest=flag.strip("-").replace("-", "_"))
    args = parser.parse_args(argv)
    used = [flag for flag in UNSUPPORTED if getattr(args, flag.strip("-").replace("-", "_")) is not None]
    if used:
        parser.error(f"Track B arm does not support {', '.join(used)}")
    return args


def clean_runner(arguments):
    return [sys.executable, str(CODE / "tools/run_laya_clean.py"), *arguments]


def journal_rows(summary: dict, run_id: str | None) -> list[dict]:
    """Sidecar samples as the strict_current/acceptance events collect_result reads."""
    rows, reached = [], set()
    baseline = summary.get("baseline_tick")
    for sample in summary.get("strict_current") or []:
        statuses = sample.get("statuses") or {}
        reached |= {m for m, passed in statuses.items() if passed is True}
        common = {"source": "strict_sidecar", "run_id": run_id, "at": sample.get("at"), "tick": sample.get("tick"),
                  "loaded_tick": baseline}
        rows.append({"event": "strict_current", **common, "statuses": statuses,
                     "predicate_evidence": sample.get("predicate_evidence")})
        rows.append({"event": "acceptance", **common,
                     "statuses": {m: "passed" if m in reached else ("unknown" if s is None else "failed")
                                  for m, s in statuses.items()}})
    return rows


def run(args) -> int:
    root = args.root.resolve()
    evidence = root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    out = evidence / "strict-sidecar.json"
    sidecar = subprocess.Popen([sys.executable, str(CODE / "tools/laya_strict_sidecar.py"), "--world-root", str(root),
                                "--out", str(out), "--interval", str(args.sidecar_interval)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    forward = ["run", "--root", str(root), "--decisions", "0", "--speed", f"{args.speed:g}", "--seed", str(args.seed),
               "--variant", str(args.variant)]
    if args.max_game_ticks:
        forward += ["--max-game-ticks", str(args.max_game_ticks)]
    child = subprocess.Popen(clean_runner(forward))
    signal.signal(signal.SIGTERM, lambda *_: child.send_signal(signal.SIGTERM))
    code = child.wait()
    time.sleep(args.sidecar_interval + 15)  # one more sample of the final state while the server is still up
    sidecar.send_signal(signal.SIGTERM)
    try:
        sidecar.wait(timeout=120)
    except subprocess.TimeoutExpired:
        sidecar.kill()
    summary = json.loads(out.read_text()) if out.exists() else {"strict_current": [], "stop": "sidecar_missing"}
    run_id = next((json.loads(line).get("run_id") for line in reversed(
        (evidence / "clean-decisions.jsonl").read_text().splitlines()) if '"run_id"' in line), None) \
        if (evidence / "clean-decisions.jsonl").exists() else None
    with (evidence / "clean-decisions.jsonl").open("a") as journal:
        for row in journal_rows(summary, run_id):
            journal.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return code


def main(argv=None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.action == "prepare":
        runtime = os.environ.get("LAYA_CHECKPOINT_RUNTIME_ROOT")
        forward = ["prepare", "--root", str(args.root), "--game-port", str(args.game_port),
                   "--rcon-port", str(args.rcon_port), "--source-save", str(args.source_save)]
        forward += ["--mods-source", str(args.mods_source)] if args.mods_source else []
        forward += ["--runtime-root", runtime] if runtime else []
        return subprocess.call(clean_runner(forward))
    if args.action == "stop":
        return subprocess.call(clean_runner(["stop", "--root", str(args.root)]))
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
