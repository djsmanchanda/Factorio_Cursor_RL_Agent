# Path: tools/validate_snapshot.py
# Purpose: Validate a snapshot JSON file against schemas/snapshot.schema.json.

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


def validate_snapshot_file(snapshot_path: Path, schema_path: Path):
    schema = load_json(schema_path)
    snapshot = load_json(snapshot_path)
    validator = Draft7Validator(schema)
    return sorted(validator.iter_errors(snapshot), key=lambda err: list(err.path))


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tools/validate_snapshot.py <snapshot.json>")
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    schema_path = repo_root / "schemas" / "snapshot.schema.json"
    snapshot_path = Path(sys.argv[1]).resolve()

    errors = validate_snapshot_file(snapshot_path, schema_path)

    if errors:
        print("Snapshot validation FAILED:")
        for error in errors:
            print(f"- {format_error_path(error)}: {error.message}")
        return 1

    print("Snapshot validation OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
