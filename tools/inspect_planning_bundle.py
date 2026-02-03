# Path: tools/inspect_planning_bundle.py
# Purpose: Run phase orchestrator and print a planning bundle.

import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator

from planners.city_planner.capability_resolver import resolve_capabilities
from planners.city_planner.planning_gate import decide_planning
from planners.city_planner.phase_orchestrator import build_planning_bundle


def load_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(f"ERROR: File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: Invalid JSON in {path}: {exc}") from exc


def _validate_request(request: dict, schema_path: Path) -> None:
    with schema_path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(request))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise SystemExit("Planning request validation FAILED:\n" + "\n".join(messages))


def main() -> int:
    if len(sys.argv) not in {3, 4}:
        print(
            "Usage: python tools/inspect_planning_bundle.py <plan_skeleton.json> <context.json> [metrics.json]"
        )
        return 2

    skeleton_path = Path(sys.argv[1]).resolve()
    context_path = Path(sys.argv[2]).resolve()
    metrics_path = Path(sys.argv[3]).resolve() if len(sys.argv) == 4 else None

    skeleton = load_json(skeleton_path)
    context = load_json(context_path)
    metrics = load_json(metrics_path) if metrics_path else None

    required_capabilities = []
    for phase in skeleton.get("phases", []):
        required_capabilities.extend(phase.get("requires_capabilities", []))

    request = {
        "intent": skeleton.get("intent"),
        "scope": skeleton.get("scope"),
        "required_capabilities": sorted(set(required_capabilities)),
    }

    request_schema_path = Path(__file__).resolve().parents[1] / "schemas" / "planning_request.schema.json"
    _validate_request(request, request_schema_path)

    capability_resolution = resolve_capabilities(request, context)
    gate_decision = decide_planning(capability_resolution.to_dict())
    if gate_decision.status != "ready":
        print("Planning gate not ready:")
        print(f"  status: {gate_decision.status}")
        print(f"  reason: {gate_decision.reason}")
        if gate_decision.required_actions:
            print("  required_actions:")
            for action in gate_decision.required_actions:
                print(f"    - {action}")
        return 1

    bundle = build_planning_bundle(skeleton, capability_resolution.to_dict(), metrics)
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
