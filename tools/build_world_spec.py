# Path: tools/build_world_spec.py
# Purpose: Export canonical planner-world and electronics survey contracts offline.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.world_generation import PlannerWorldSpec


def main() -> int:
    parser = argparse.ArgumentParser(description="Export deterministic 500x500 planner WorldSpec")
    parser.add_argument("--output", type=Path, help="Write the complete WorldSpec JSON")
    parser.add_argument("--electronics-output", type=Path, help="Write ElectronicsWorldSpec JSON")
    args = parser.parse_args()
    world = PlannerWorldSpec.canonical()
    if args.output:
        args.output.write_text(json.dumps(world.payload, indent=2) + "\n", encoding="utf-8")
    if args.electronics_output:
        args.electronics_output.write_text(
            json.dumps(world.electronics_payload(), indent=2) + "\n", encoding="utf-8"
        )
    if not args.output and not args.electronics_output:
        print(json.dumps(world.payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())