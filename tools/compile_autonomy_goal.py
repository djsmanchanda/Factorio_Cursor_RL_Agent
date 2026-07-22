# Path: tools/compile_autonomy_goal.py
# Purpose: Compile a goal and offline observations into a deterministic plan-only autonomy program.

from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from core.autonomy_program import compile_autonomy_program

def main() -> int:
    parser = argparse.ArgumentParser(description="Compile only; never connects to or mutates Factorio.")
    parser.add_argument("--goal", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--world-spec", type=Path)
    parser.add_argument("--block-bounds", nargs=4, type=float, metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    goal = json.loads(args.goal.read_text(encoding="utf-8"))
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    world = json.loads(args.world_spec.read_text(encoding="utf-8")) if args.world_spec else None
    bounds = None if args.block_bounds is None else dict(zip(("x1", "y1", "x2", "y2"), args.block_bounds))
    program = compile_autonomy_program(goal, catalog, world_spec=world, snapshot=snapshot, block_bounds=bounds)
    text = json.dumps(program, indent=2, sort_keys=True) + "\n"
    if args.output: args.output.write_text(text, encoding="utf-8")
    else: print(text, end="")
    return 0

if __name__ == "__main__": raise SystemExit(main())
