# Path: tools/generate_electronics_upgrade_plan.py
# Purpose: Emit an explicit premium logistics upgrade plan for a planned factory.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.electronics_block import build_electronics_block
from planners.electronics_upgrades import build_premium_upgrade_plan
from planners.electronics_world import load_electronics_world_spec


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit a later, explicit premium-tier upgrade plan.")
    parser.add_argument("--world-spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--without-processing", action="store_true")
    args = parser.parse_args()
    try:
        world = load_electronics_world_spec(args.world_spec)
        bundle = build_electronics_block(include_processing=not args.without_processing, world=world)
        plan = build_premium_upgrade_plan(bundle)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {len(plan['actions'])} explicit upgrades to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
