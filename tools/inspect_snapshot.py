# Path: tools/inspect_snapshot.py
# Purpose: Inspect a snapshot by building a read-only factory graph and printing facts.

import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from planners.local_layout_planner import LocalLayoutPlanner


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tools/inspect_snapshot.py <snapshot.json>")
        return 2

    snapshot_path = Path(sys.argv[1]).resolve()
    planner = LocalLayoutPlanner()

    try:
        lines = planner.inspect_snapshot(snapshot_path)
    except ValueError as exc:
        print(str(exc))
        return 1

    for line in lines:
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
