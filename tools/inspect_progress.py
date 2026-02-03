# Path: tools/inspect_progress.py
# Purpose: Derive and display Progress State from inputs.

import sys
from pathlib import Path

from core.progress_state import build_progress_state


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: python tools/inspect_progress.py <snapshot.json> <metrics.json> <build_intent.json>")
        return 2

    snapshot_path = Path(sys.argv[1]).resolve()
    metrics_path = Path(sys.argv[2]).resolve()
    build_intent_path = Path(sys.argv[3]).resolve()

    try:
        progress = build_progress_state(snapshot_path, metrics_path, build_intent_path)
    except ValueError as exc:
        print(str(exc))
        return 1

    data = progress.to_dict()

    print("Progress State")
    print(f"Ultimate capacity: {data['ultimate_capacity']}")
    print(f"Current capacity: {data['current_capacity']}")
    print(f"Committed capacity: {data['committed_capacity']}")
    print(f"Active phase capacity: {data['active_phase_capacity']}")
    print("Completed phases:")
    for phase in data["completed_phases"]:
        print(f"  - {phase}")
    print("Rationale:")
    print(f"  {data['rationale']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
