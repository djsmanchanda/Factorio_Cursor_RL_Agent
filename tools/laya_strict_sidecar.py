#!/usr/bin/env python3
# Path: tools/laya_strict_sidecar.py | Purpose: Measure any disposable world with the strict C0-C9 predicates, independent of the runner that drives it.
"""Runner-independent strict checkpoint measurement for A/B tracks.

Each track's runner keeps its own decision loop; this sidecar polls the same
world read-only over RCON and evaluates the strict predicates from *this* code
revision, so every arm is measured by identical code. The first successful
observation is the baseline. Output mirrors job-status rows (``strict_reached``,
``strict_current``, ``strict_current_at_end``) so ``tools/laya_evidence.py``
reads it unchanged.

Only manifests that declare ``production_authority: false`` are accepted.
Foundation districts (C2/C3) use the runner's bootstrap ledger when one exists;
without it they are a weaker census, so compare tracks on C4 and later first.

Usage: ``python tools/laya_strict_sidecar.py --world-root <root> --out <file.json>``
"""
from __future__ import annotations

import argparse
import hashlib
import json
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.checkpoint_milestones import DEFAULT_MILESTONES, MILESTONE_IDS, _machine_consumption, evaluate_milestone  # noqa: E402
from orchestrator.checkpoint_evidence import project_evidence, retained_samples, new_evidence_state, evidence_success, evidence_failure, state_projection  # noqa: E402

#: Full evidence is kept for the checkpoints a track comparison reads closely; others keep version and status.
DETAILED = {"C4", "C5", "C6", "C7", "C8", "C9"}


def instrument_identity() -> dict[str, str]:
    source = ROOT / "orchestrator" / "checkpoint_milestones.py"
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"revision": revision or "?", "predicate_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "evidence_sha256": hashlib.sha256((ROOT / "orchestrator" / "checkpoint_evidence.py").read_bytes()).hexdigest()}


def disposable_endpoint(world_root: Path) -> tuple[int, str]:
    """RCON port and password of an explicitly disposable world, or ValueError."""
    manifest = json.loads((world_root / "manifest.json").read_text())
    if manifest.get("production_authority") is not False:
        raise ValueError("strict sidecar only measures worlds whose manifest declares production_authority: false")
    return int(manifest["rcon_port"]), (world_root / "server" / "rcon-password").read_text().strip()


class Measurement:
    """Baseline, bounded history and the first tick each predicate passed."""

    def __init__(self, identity: dict[str, str]) -> None:
        import uuid
        self.identity, self.baseline, self.history = identity, None, []
        self.reached: dict[str, int | None] = {}
        self.rows: list[dict[str, Any]] = []
        self.evidence_state = new_evidence_state()
        # Cumulative per-recipe crafts for this measurement only (one generation).
        self.generation, self.crafts, self._consumers = uuid.uuid4().hex, {}, None

    def _count_crafts(self, snapshot) -> dict[str, Any] | None:
        """Crafts since the previous sample that had machine telemetry, accumulated for this generation.

        C6's ``machine_consumption`` is a sum over the retained window (at
        most 20 samples), so it can stay flat or fall while machines keep
        crafting. This counter only grows. It uses the same per-machine rule
        (a machine's first sample on a recipe is a baseline, so inherited
        lifetime counts and recipe changes add nothing); a sample without
        telemetry, or a failed poll, leaves the previous sample as the base.
        """
        if not isinstance(snapshot.ac_consumers, (list, tuple)):
            return None
        delta = {} if self._consumers is None else (_machine_consumption([self._consumers, snapshot]) or {})
        self._consumers = snapshot
        for recipe, count in delta.items():
            self.crafts[recipe] = self.crafts.get(recipe, 0) + count
        return {"generation": self.generation, "cumulative": dict(self.crafts), "since_previous": delta}

    def add(self, snapshot) -> dict[str, Any]:
        crafts = self._count_crafts(snapshot)
        self.baseline = self.baseline or snapshot
        self.history = retained_samples([*self.history, snapshot])[-20:]
        # Historical predicates use object identity to decide whether the last
        # sample is already in history; evaluate the retained representative.
        snapshot = self.history[-1]
        evidence_success(self.evidence_state, self.baseline, self.history)
        results = {m.checkpoint_id: evaluate_milestone(m.checkpoint_id, snapshot, baseline=self.baseline,
                                                       history=self.history, milestones=DEFAULT_MILESTONES)
                   for m in DEFAULT_MILESTONES}
        for checkpoint, result in results.items():
            if result.passed and checkpoint not in self.reached:
                self.reached[checkpoint] = snapshot.tick
        row = {"event": "strict_current", "at": f"sample {len(self.rows)}", "tick": snapshot.tick,
               "statuses": {cid: (True if r.passed else False if r.failed else None) for cid, r in results.items()},
               "predicate_evidence": {cid: {"predicate_version": r.predicate_version, "status": r.status.value,
                                            **({"evidence": dict(r.evidence)} if cid in DETAILED else {})}
                                      for cid, r in results.items()},
               "machine_crafts": crafts}
        row["predicate_evidence"]["_contract"] = state_projection(self.baseline, self.history, self.evidence_state)
        self.rows.append(row)
        return row

    def failure(self) -> None:
        evidence_failure(self.evidence_state)

    def summary(self, stop: str) -> dict[str, Any]:
        last = self.rows[-1]["statuses"] if self.rows and self.evidence_state["current_available"] else {}
        return {"instrument": self.identity, "runtime_components": getattr(self, "runtime_components", {"state": "unknown", "cause": "legacy measurement"}), "milestones": list(MILESTONE_IDS), "stop": stop,
                "baseline_tick": None if self.baseline is None else self.baseline.tick,
                "strict_reached": dict(self.reached), "strict_current_at_end": last,
                "accepted_ever": sorted(self.reached, key=MILESTONE_IDS.index), "strict_current": self.rows, "evidence": state_projection(self.baseline, self.history, self.evidence_state)}


def run(observe: Callable[[], Any], out: Path, *, interval: float, connect_timeout: float, max_failures: int,
        stopped: Callable[[], bool], sleep: Callable[[float], None] = time.sleep,
        identity: dict[str, str] | None = None, world_root: Path | None = None) -> dict[str, Any]:
    """Sample until stopped or the world goes away; rewrite ``out`` atomically after every sample."""
    from orchestrator.runtime_component_manifest import build_manifest, install_manifest
    ref = install_manifest(out.with_name(out.name + ".components.json"),
                           build_manifest(entrypoint="tools.laya_strict_sidecar.run", world_root=world_root),
                           legacy=out.exists())
    measurement, failures, waited, stop = Measurement(identity or instrument_identity()), 0, 0.0, "stopped"
    measurement.runtime_components = ref
    while not stopped():
        try:
            measurement.add(observe())
            failures = 0
            _write(out, measurement.summary("running"))
        except Exception as error:  # noqa: BLE001 -- an unreachable or busy world is a missed sample, not a result
            measurement.failure()
            _write(out, measurement.summary("running"))
            failures += 1
            if measurement.rows and failures >= max_failures:
                stop = f"world_unreachable: {type(error).__name__}"
                break
            if not measurement.rows and waited >= connect_timeout:
                stop = f"never_observed: {type(error).__name__}"
                break
        sleep(interval)
        waited += interval
    summary = measurement.summary(stop)
    _write(out, summary)
    return summary


def _write(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, default=str))
    tmp.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--world-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=60.0, help="Wall seconds between samples")
    parser.add_argument("--connect-timeout", type=float, default=1800.0)
    parser.add_argument("--max-failures", type=int, default=5)
    args = parser.parse_args(argv)
    from orchestrator.checkpoint_observer import LiveCheckpointObservationAdapter
    from tools.rcon_client import RconClient
    port, password = disposable_endpoint(args.world_root)
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))

    def observe():
        client = RconClient("127.0.0.1", port, password, timeout=30.0)
        try:
            return LiveCheckpointObservationAdapter(client, surface="nauvis", force="player").observe().snapshot
        finally:
            client.close()

    summary = run(observe, args.out, interval=args.interval, connect_timeout=args.connect_timeout,
                  max_failures=args.max_failures, stopped=lambda: stop["flag"], world_root=args.world_root)
    print(json.dumps({"stop": summary["stop"], "samples": len(summary["strict_current"]),
                      "strict_reached": summary["strict_reached"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
