# Path: tools/inspect_planning_bundle.py
# Purpose: Run phase orchestrator and print a planning bundle.

import json
import sys
from pathlib import Path

from planners.city_planner.phase_orchestrator import build_planning_bundle


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
        print("Usage: python tools/inspect_planning_bundle.py <plan_skeleton.json>")
        return 2

    skeleton_path = Path(sys.argv[1]).resolve()
    skeleton = load_json(skeleton_path)

    required_capabilities = []
    for phase in skeleton.get("phases", []):
        required_capabilities.extend(phase.get("requires_capabilities", []))

    capability_resolution = {
        "intent": skeleton.get("intent"),
        "scope": skeleton.get("scope"),
        "available": sorted(set(required_capabilities)),
        "blocked": [],
    }

    bundle = build_planning_bundle(skeleton, capability_resolution)
    bundle_dict = bundle.to_dict()

    print("Planning Bundle")
    print(f"Intent: {bundle_dict.get('intent')}")
    print(f"Scope: {bundle_dict.get('scope')}")

    print("")
    print("Phase Results:")
    for index, phase in enumerate(bundle_dict.get("phase_results", []), start=1):
        print(f"  {index}. {phase.get('phase')}")
        print(f"     decision: {phase.get('decision')}")
        rationale = phase.get("rationale", [])
        if rationale:
            print("     rationale:")
            for item in rationale:
                print(f"       - {item}")
        topology = phase.get("topology")
        if topology:
            print("     topology:")
            blocks = topology.get("blocks", {})
            if blocks:
                print("       blocks:")
                for block_name in sorted(blocks):
                    print(f"         - {block_name}: {blocks[block_name]}")
            dependencies = topology.get("dependencies", [])
            if dependencies:
                print("       dependencies:")
                for dep in dependencies:
                    if len(dep) == 2:
                        print(f"         - {dep[0]} -> {dep[1]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
