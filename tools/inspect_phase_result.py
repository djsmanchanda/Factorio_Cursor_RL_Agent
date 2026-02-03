# Path: tools/inspect_phase_result.py
# Purpose: Evaluate and display a phase result from a plan skeleton.

import json
import sys
from pathlib import Path

from planners.city_planner.phases.transport_strategy_phase import evaluate_transport_strategy


def load_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(f"ERROR: File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: Invalid JSON in {path}: {exc}") from exc


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tools/inspect_phase_result.py <plan_skeleton.json>")
        return 2

    skeleton_path = Path(sys.argv[1]).resolve()
    skeleton = load_json(skeleton_path)

    phases = skeleton.get("phases", [])
    phase_entry = next((phase for phase in phases if phase.get("phase") == "transport_strategy_selection"), None)
    if not phase_entry:
        print("ERROR: transport_strategy_selection phase not found in skeleton")
        return 1

    capability_resolution = {
        "intent": skeleton.get("intent"),
        "scope": skeleton.get("scope"),
        "available": phase_entry.get("requires_capabilities", []),
        "blocked": [],
    }

    result = evaluate_transport_strategy(phase_entry, capability_resolution, [])

    print("Phase Result")
    print(f"Phase: {result.phase}")
    print(f"Decision: {result.decision}")

    if result.alternatives:
        print("Alternatives:")
        for item in result.alternatives:
            print(f"  - {item}")

    if result.rationale:
        print("Rationale:")
        for item in result.rationale:
            print(f"  - {item}")

    if result.constraints:
        print("Constraints:")
        for item in result.constraints:
            print(f"  - {item}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
