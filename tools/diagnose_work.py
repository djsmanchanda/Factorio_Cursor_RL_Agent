# Path: tools/diagnose_work.py
# Purpose: Read an explicit offline evidence bundle and print bounded work diagnosis.

"""Offline only: no server discovery, clients, helper models, or runner dispatch."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from orchestrator.work_diagnosis import MAX_ARTIFACTS, diagnose, render_markdown

MAX_FILE_BYTES = 4 * 1024 * 1024


def _read_json(path: Path):
    # Bounded even if a supplied file grows while being read.
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("input exceeds 4 MiB; narrow or copy a smaller snapshot")

    def reject_constant(value):
        raise ValueError(f"nonfinite JSON number {value}")

    return json.loads(raw, parse_constant=reject_constant), hashlib.sha256(raw).hexdigest()


def load_bundle(path: Path) -> dict:
    """Load only paths named by the bundle; never find the 'latest' journal."""
    bundle, _ = _read_json(path)
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be an object")
    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) > MAX_ARTIFACTS:
        raise ValueError(f"artifacts must be a list of at most {MAX_ARTIFACTS} records")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ValueError("artifact must be an object")
        if "path" not in artifact:
            continue
        if "payload" in artifact:
            raise ValueError("artifact must supply path or payload, not both")
        if not isinstance(artifact["path"], str) or not artifact["path"]:
            raise ValueError("artifact path must be a nonempty string")
        source = (path.parent / artifact["path"]).resolve()
        try:
            artifact["payload"], digest = _read_json(source)
            artifact["sha256"] = digest
        except (OSError, ValueError, UnicodeError) as error:
            artifact["load_error"] = f"cannot read artifact: {type(error).__name__}: {str(error)[:220]}"
    return bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="Version 1 evidence envelope; file paths are relative to it")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output", type=Path, help="Create a NEW report file; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        report = diagnose(load_bundle(args.bundle))
        text = (json.dumps(report, sort_keys=True) + "\n" if args.format == "json" else render_markdown(report))
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(text)
        else:
            print(text, end="")
    except (OSError, ValueError) as error:
        parser.exit(2, f"diagnose_work: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
