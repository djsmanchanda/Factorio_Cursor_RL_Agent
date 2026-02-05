# Path: tools/execution_reporter.py
# Purpose: Validate and summarize execution reports deterministically.

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
        print("Execution report validation FAILED:")
        for error in errors:
            print(f"- {format_error_path(error)}: {error.message}")
        raise SystemExit(1)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tools/execution_reporter.py <execution_report.json>")
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    schema_path = repo_root / "schemas" / "execution_report.schema.json"

    report_path = Path(sys.argv[1]).resolve()
    payload = load_json(report_path)
    validate_report(payload, schema_path)

    print("Execution Report")
    print(f"Tick: {payload['tick']}")
    print(f"Surface: {payload['surface']}")
    for action in payload.get("actions", []):
        status = action.get("status")
        reason = action.get("reason")
        count = action.get("count")
        line = f"- {action.get('action')}: {status}"
        if count is not None:
            line += f" (count={count})"
        if reason:
            line += f" reason={reason}"
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
