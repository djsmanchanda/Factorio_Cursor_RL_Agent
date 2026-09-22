# Path: tools/train_laya_4060.py
# Purpose: Run local single-GPU Laya LoRA/RLCD fine-tuning after explicit preflight.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.laya_finetune import LayaFineTuneError, train_local


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = train_local(args.cases, args.model_dir, args.output_dir)
    except LayaFineTuneError as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, indent=2))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
