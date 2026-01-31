# Path: tools/inspect_intents.py
# Purpose: Validate and print supervisor intents in a human-readable format.

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


def validate_intent(intent: dict, schema: dict):
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(intent), key=lambda err: list(err.path))
    if errors:
        print("Intent validation FAILED:")
        for error in errors:
            print(f"- {format_error_path(error)}: {error.message}")
        return False
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tools/inspect_intents.py <intent.json>")
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    schema_path = repo_root / "schemas" / "intent.schema.json"
    intent_path = Path(sys.argv[1]).resolve()

    schema = load_json(schema_path)
    intent = load_json(intent_path)

    if not validate_intent(intent, schema):
        return 1

    print(f"Intent: {intent.get('intent')}")
    print(f"Scope: {intent.get('scope')}")
    print(f"Urgency: {intent.get('urgency')}")
    confidence = intent.get("confidence", 1.0)
    print(f"Confidence: {confidence:.2f}")

    evidence = intent.get("evidence", [])
    if evidence:
        print("Evidence:")
        for item in evidence:
            source = item.get("source")
            value = item.get("value")
            print(f"  - {source} = {value}")

    notes = intent.get("notes")
    if notes:
        print("Notes:")
        print(f"  {notes}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
