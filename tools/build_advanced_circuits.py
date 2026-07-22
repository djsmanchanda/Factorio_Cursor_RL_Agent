# Path: tools/build_advanced_circuits.py
# Purpose: Produce the managed raw-resource advanced-circuit block without live mutation.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.electronics_block import build_electronics_block
from planners.electronics_world import load_electronics_world_spec


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan the immutable raw-resource advanced-circuit block."
    )
    parser.add_argument("--world-spec", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        world = load_electronics_world_spec(args.world_spec)
        bundle = build_electronics_block(include_processing=False, world=world)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    result = {
        "infrastructure": bundle["infrastructure"],
        "plans": bundle["plans"],
        "scaffolding": bundle["scaffolding"],
        "dependencies": bundle["dependencies"],
        "block_footprint": bundle["block_footprint"],
        "interfaces_reserved": bundle["interfaces_reserved"],
        "throughput_contract": bundle["throughput_contract"],
        "world_spec": bundle["world_spec"],
    }
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"Managed plans: {len(result['infrastructure'])}; "
        f"production plans: {len(result['plans'])}; live execution: disabled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())