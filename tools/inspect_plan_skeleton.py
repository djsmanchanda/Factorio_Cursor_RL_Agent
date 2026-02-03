# Path: tools/inspect_plan_skeleton.py
# Purpose: Validate and print plan skeletons in a human-readable format.

import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator


def load_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(f"ERROR: File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: Invalid JSON in {path}: {exc}") from exc


def format_error_path(error):
    if not error.path:
        return "<root>"
    return "/".join(str(part) for part in error.path)


def validate_skeleton(skeleton: dict, schema: dict):
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(skeleton), key=lambda err: list(err.path))
    if errors:
        print("Plan skeleton validation FAILED:")
        for error in errors:
            print(f"- {format_error_path(error)}: {error.message}")
        return False
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tools/inspect_plan_skeleton.py <plan_skeleton.json>")
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    schema_path = repo_root / "schemas" / "plan_skeleton.schema.json"
    skeleton_path = Path(sys.argv[1]).resolve()

    schema = load_json(schema_path)
    skeleton = load_json(skeleton_path)

    if not validate_skeleton(skeleton, schema):
        return 1

    print("Plan Skeleton")
    print(f"Intent: {skeleton.get('intent')}")
    print(f"Scope: {skeleton.get('scope')}")

    print("")
    print("Phases:")
    phases = skeleton.get("phases", [])
    for index, phase in enumerate(phases, start=1):
        name = phase.get("phase")
        requires = phase.get("requires_capabilities", [])
        constraints = phase.get("constraints", [])
        print(f"  {index}. {name}")
        if requires:
            print(f"     requires: {', '.join(requires)}")
        if constraints:
            print(f"     constraints: {', '.join(constraints)}")

    explicitly_not_doing = skeleton.get("explicitly_not_doing", [])
    if explicitly_not_doing:
        print("")
        print("Explicitly not doing:")
        for item in explicitly_not_doing:
            print(f"  - {item}")

    notes = skeleton.get("notes")
    if notes:
        print("")
        print("Notes:")
        print(f"  {notes}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
