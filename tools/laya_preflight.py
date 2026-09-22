# Path: tools/laya_preflight.py
# Purpose: Report local RTX 4060 readiness before any Laya model allocation.

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.laya_runtime import Laya4060Config, LayaRuntimeError, autocast_dtype, preflight


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value is None else int(value)


def _env_optional_int(name: str) -> int | None:
    value = os.environ.get(name)
    return None if value is None or not value.strip() or value.strip() == "0" else int(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-len", type=int, default=_env_int("LAYA_MAX_LEN", 1024))
    parser.add_argument("--head-max-len", type=int, default=_env_int("LAYA_HEAD_MAX_LEN", 256))
    parser.add_argument("--micro-batch", type=int, default=_env_int("LAYA_MICRO_BATCH", 1))
    parser.add_argument(
        "--gradient-accumulation",
        type=int,
        default=_env_int("LAYA_GRADIENT_ACCUMULATION", 32),
    )
    parser.add_argument(
        "--gradient-checkpointing",
        type=int,
        choices=(0, 1),
        default=1 if _env_bool("LAYA_GRADIENT_CHECKPOINTING", True) else 0,
    )
    parser.add_argument(
        "--torch-compile",
        type=int,
        choices=(0, 1),
        default=1 if _env_bool("LAYA_TORCH_COMPILE", False) else 0,
    )
    parser.add_argument(
        "--attention-implementation",
        choices=("sdpa", "flex_attention"),
        default=os.environ.get(
            "LAYA_ATTN_IMPLEMENTATION",
            os.environ.get("LAYA_ATTENTION_IMPLEMENTATION", "sdpa"),
        ),
    )
    parser.add_argument(
        "--fixed-option-width",
        type=int,
        default=_env_optional_int("LAYA_FIXED_OPTION_WIDTH"),
    )
    args = parser.parse_args()
    config = Laya4060Config(
        max_len=args.max_len,
        head_max_len=args.head_max_len,
        micro_batch=args.micro_batch,
        gradient_accumulation=args.gradient_accumulation,
        gradient_checkpointing=bool(args.gradient_checkpointing),
        torch_compile=bool(args.torch_compile),
        attention_implementation=args.attention_implementation,
        fixed_option_width=args.fixed_option_width,
        fused_adamw=_env_bool("LAYA_FUSED_ADAMW", False),
    )
    try:
        report = preflight(config)
        report["autocast_dtype"] = autocast_dtype()
    except LayaRuntimeError as error:
        report = {
            "profile": config.profile_id,
            "status": "blocked",
            "reason": str(error),
            "micro_batch": config.micro_batch,
            "gradient_accumulation": config.gradient_accumulation,
            "gradient_checkpointing": config.gradient_checkpointing,
            "torch_compile": config.torch_compile,
            "attention_implementation": config.attention_implementation,
            "fixed_option_width": config.fixed_option_width,
            "fused_adamw": config.fused_adamw,
        }
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
