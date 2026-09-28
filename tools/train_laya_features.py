#!/usr/bin/env python3
# Path: tools/train_laya_features.py
# Purpose: Fit the features-only policy on counterfactual cases; each policy-iteration round retrains it.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.laya_feature_baseline import accuracy, train


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="+", type=Path, help="Case JSONL from tools/laya_labels_to_cases.py")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=400)
    args = parser.parse_args(argv)
    cases = [json.loads(line) for path in args.cases for line in path.read_text(encoding="utf-8").splitlines() if line]
    model = train(cases, epochs=args.epochs)
    model["sources"] = [str(path) for path in args.cases]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(model, indent=1), encoding="utf-8")
    print(json.dumps({"cases": model["cases"], "train_top1": round(accuracy(model, cases), 3), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
