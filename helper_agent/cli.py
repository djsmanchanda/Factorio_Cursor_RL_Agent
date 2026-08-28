# Path: helper_agent/cli.py
# Purpose: Run Helper Agent processing and focused-brief generation from a local shell.

from __future__ import annotations

import argparse
from pathlib import Path

from helper_agent import brief, config
from helper_agent.review_service import ReviewService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local Helper Agent.")
    parser.add_argument(
        "--data-root", type=Path, help="Override the local Helper Agent data root.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("process", help="Review all case packets currently in the inbox.")
    brief_parser = subcommands.add_parser("brief", help="Generate a focused-edit brief.")
    brief_parser.add_argument("--run-id", required=True)
    brief_parser.add_argument("--target")
    brief_parser.add_argument(
        "--category", default="validator",
        choices=["observation", "action", "validator", "reward", "curriculum", "telemetry", "operational fix"],
    )
    args = parser.parse_args(argv)
    settings = config.default_config()
    data_root = args.data_root or settings.data_root
    if args.command == "process":
        service = ReviewService(
            data_root,
            model_endpoint=settings.model_endpoint,
            model_name=settings.model_name,
        )
        service.model_timeout_seconds = settings.timeout_seconds
        for packet, report in service.process_inbox():
            print(f"{packet}: {report}")
        return 0
    path, text = brief.generate_brief(data_root, args.run_id, target=args.target, category=args.category)
    print(path)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
