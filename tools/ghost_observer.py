# Path: tools/ghost_observer.py
# Purpose: Validate and summarize GhostPlan observations deterministically.

import json
import sys
from collections import Counter
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


def validate_observation(payload: dict, schema_path: Path) -> None:
    schema = load_json(schema_path)
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    if errors:
        print("Ghost observation validation FAILED:")
        for error in errors:
            print(f"- {format_error_path(error)}: {error.message}")
        raise SystemExit(1)


def build_ghost_observation(payload: dict) -> dict:
    counts_by_block = Counter()
    counts_by_phase = Counter()
    counts_by_capacity = Counter()

    ghosts = payload.get("ghosts", [])
    for ghost in ghosts:
        tags = ghost.get("tags")
        if not tags:
            raise SystemExit("Ghost observation error: missing tags")

        counts_by_block[tags.get("block")] += 1
        counts_by_phase[tags.get("phase")] += 1
        counts_by_capacity[tags.get("capacity_slice")] += 1

    aggregates = {
        "counts_by_block": dict(sorted(counts_by_block.items())),
        "counts_by_phase": dict(sorted(counts_by_phase.items())),
        "counts_by_capacity_slice": dict(sorted(counts_by_capacity.items())),
        "total": len(ghosts),
    }

    output = {
        "tick": payload.get("tick"),
        "surface": payload.get("surface"),
        "ghosts": payload.get("ghosts"),
        "aggregates": aggregates,
    }

    return output


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tools/ghost_observer.py <ghost_observation.json>")
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    schema_path = repo_root / "schemas" / "ghost_observation.schema.json"

    observation_path = Path(sys.argv[1]).resolve()
    payload = load_json(observation_path)
    validate_observation(payload, schema_path)

    output = build_ghost_observation(payload)
    validate_observation(output, schema_path)

    print("Ghost Observation")
    print(f"Tick: {output['tick']}")
    print(f"Surface: {output['surface']}")
    print("Counts by block:")
    for key, value in output["aggregates"]["counts_by_block"].items():
        print(f"  - {key}: {value}")
    print("Counts by phase:")
    for key, value in output["aggregates"]["counts_by_phase"].items():
        print(f"  - {key}: {value}")
    print("Counts by capacity slice:")
    for key, value in output["aggregates"]["counts_by_capacity_slice"].items():
        print(f"  - {key}: {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
