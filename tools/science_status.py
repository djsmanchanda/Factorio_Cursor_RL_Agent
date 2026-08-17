# Path: tools/science_status.py
# Purpose: Validate and summarize one saved, read-only ScienceStatus report.

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(f"ERROR: File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: Invalid JSON in {path}: {exc}") from exc


def format_error_path(error: Any) -> str:
    if not error.path:
        return "<root>"
    return "/".join(str(part) for part in error.path)


def _sum_inventory(entries: list[dict[str, Any]]) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for entry in entries:
        totals.update(entry["input_inventory"])
    return dict(sorted(totals.items()))


def semantic_errors(payload: dict[str, Any]) -> list[str]:
    """Check aggregate and ordering invariants JSON Schema cannot express."""
    if payload.get("ok") is not True:
        return []

    labs = payload["labs"]
    entries = labs["entries"]
    errors: list[str] = []
    unit_numbers = [entry["unit_number"] for entry in entries]
    if unit_numbers != sorted(unit_numbers) or len(unit_numbers) != len(set(unit_numbers)):
        errors.append("labs.entries must be strictly sorted by unit_number")
    if labs["total"] != len(entries):
        errors.append("labs.total must equal the number of labs.entries")

    statuses = dict(sorted(Counter(entry["status"] for entry in entries).items()))
    if labs["status_counts"] != statuses:
        errors.append("labs.status_counts must equal the statuses in labs.entries")
    if labs["working"] != statuses.get("working", 0):
        errors.append("labs.working must equal the number of working lab entries")
    if labs["input_inventory"] != _sum_inventory(entries):
        errors.append("labs.input_inventory must equal the sum of lab entry inventories")
    return errors


def science_status_errors(payload: Any, schema_path: Path) -> list[str]:
    schema = load_json(schema_path)
    validator = Draft7Validator(schema)
    errors = [
        f"{format_error_path(error)}: {error.message}"
        for error in sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    ]
    if not errors and isinstance(payload, dict):
        errors.extend(semantic_errors(payload))
    return errors


def validate_science_status(payload: Any, schema_path: Path) -> None:
    errors = science_status_errors(payload, schema_path)
    if errors:
        print("Science status validation FAILED:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)


def _print_identity(payload: dict[str, Any]) -> None:
    print(f"Schema version: {payload['schema_version']}")
    print(f"Tick: {payload['tick']}")
    print(f"Surface: {payload['surface']}")
    print(f"Force: {payload['force']}")


def print_summary(payload: dict[str, Any]) -> None:
    if not payload["ok"]:
        print("Science Status Error")
        _print_identity(payload)
        print(f"Error: {payload['error']}")
        return

    research = payload["research"]
    labs = payload["labs"]
    print("Science Status")
    _print_identity(payload)
    print(f"Current research: {research['current'] or '<none>'}")
    print(f"Research progress: {research['progress']:.3f}")
    print("Research queue: " + (", ".join(research["queue"]) or "<empty>"))
    print(f"Labs: {labs['working']} working / {labs['total']} total")
    print("Lab statuses:")
    for status, count in sorted(labs["status_counts"].items()):
        print(f"  - {status}: {count}")
    print("Lab input inventory:")
    if labs["input_inventory"]:
        for item, count in sorted(labs["input_inventory"].items()):
            print(f"  - {item}: {count}")
    else:
        print("  - <empty>")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("Usage: python tools/science_status.py <science_status.json>")
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    schema_path = repo_root / "schemas" / "science_status.schema.json"
    payload = load_json(Path(argv[0]).resolve())
    validate_science_status(payload, schema_path)
    print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
