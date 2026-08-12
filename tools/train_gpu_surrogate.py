# Path: tools/train_gpu_surrogate.py
# Purpose: Train an offline CUDA-capable reward/failure surrogate from SQLite evidence.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.surrogate import load_terminal_examples, save_surrogate, torch_runtime, train_surrogate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train an offline reward/failure surrogate; it never controls Factorio."
    )
    parser.add_argument("--database", type=Path, default=Path("data/training/experience.db"))
    parser.add_argument("--output", type=Path, required=True, help="PyTorch checkpoint path")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--min-examples", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--hidden-width", type=int, default=128)
    parser.add_argument("--require-cuda", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict:
    examples = load_terminal_examples(args.database, args.limit)
    if len(examples) < args.min_examples:
        raise ValueError(
            f"need at least {args.min_examples} usable terminal transitions; found {len(examples)}"
        )
    summary, model = train_surrogate(
        examples, epochs=args.epochs, batch_size=args.batch_size,
        learning_rate=args.learning_rate, hidden_width=args.hidden_width,
        require_cuda=args.require_cuda,
    )
    torch, _device = torch_runtime(require_cuda=args.require_cuda)
    save_surrogate(args.output, summary, model, torch)
    return {**summary, "database": str(args.database), "output": str(args.output)}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        print(json.dumps(run(args), sort_keys=True))
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"surrogate training failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
