# Path: tools/generate_training_scenarios.py
# Purpose: Export deterministic mining-delivery curricula for training workers.

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.scenarios.mining_delivery import generate_mining_delivery_curriculum


def _write_curriculum(output_dir: Path, scenarios: list[dict]) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for scenario in scenarios:
        path = output_dir / f"{scenario['scenario_id']}.json"
        path.write_text(json.dumps(scenario, indent=2) + "\n", encoding="utf-8")
        files.append(path.name)
    manifest = {
        "version": "1.2.0",
        "family": "mining_delivery",
        "count": len(scenarios),
        "files": files,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic mining-and-delivery training scenarios.",
    )
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--start-seed", type=int, default=0)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    scenarios = generate_mining_delivery_curriculum(args.count, args.start_seed)
    payload = _write_curriculum(args.output_dir, scenarios) if args.output_dir else scenarios
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
