# Path: tools/construction_reporter.py
# Purpose: Validate and summarize construction reports deterministically.

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


def validate_report(payload: dict, schema_path: Path) -> None:
    schema = load_json(schema_path)
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    if errors:
        print("Construction report validation FAILED:")
        for error in errors:
            print(f"- {format_error_path(error)}: {error.message}")
        raise SystemExit(1)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tools/construction_reporter.py <construction_report.json>")
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    schema_path = repo_root / "schemas" / "construction_report.schema.json"

    report_path = Path(sys.argv[1]).resolve()
    payload = load_json(report_path)
    validate_report(payload, schema_path)

    print("Construction Report")
    print(f"Tick: {payload['tick']}")
    print(f"Surface: {payload['surface']}")
    print(f"Started: {payload['started']}")
    print(f"Completed: {payload['completed']}")
    if payload.get("blocked"):
        print(f"Blocked: {payload.get('blocked_reason', 'unknown')}")
    else:
        print("Blocked: false")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
