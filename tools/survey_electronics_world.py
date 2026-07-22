# Path: tools/survey_electronics_world.py
# Purpose: Compile a snapshot or raw world observation into a validated electronics WorldSpec.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.resource_survey import allocate_electronics_world


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan-only deterministic resource survey and electronics site allocation"
    )
    parser.add_argument("input", type=Path, help="Snapshot JSON or world_observation JSON")
    parser.add_argument("--output", type=Path, help="Write ElectronicsWorldSpec JSON")
    parser.add_argument("--surface", choices=["planner-sandbox"], default="planner-sandbox")
    parser.add_argument(
        "--block-bounds", nargs=4, type=float, required=True,
        metavar=("X1", "Y1", "X2", "Y2"),
        help="Explicit local macro-block bounds; resources outside require CityPlanner rail",
    )
    parser.add_argument(
        "--single-macro-block", action="store_true", required=True,
        help="Acknowledge that these bounds are one self-contained local block",
    )
    parser.add_argument("--tick", type=int, default=0)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if "world_observation" in payload:
        snapshot = payload
    else:
        snapshot = {
            "tick": args.tick,
            "surface": args.surface,
            "entities": [],
            "world_observation": payload,
        }
    x1, y1, x2, y2 = args.block_bounds
    result = allocate_electronics_world(
        snapshot, block_bounds={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
    )
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
