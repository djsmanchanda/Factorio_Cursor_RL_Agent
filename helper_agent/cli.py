# Path: helper_agent/cli.py
# Purpose: Run Helper Agent processing and focused-brief generation from a local shell.

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from helper_agent import brief, config
from helper_agent.review_service import ReviewService


def launch_processor(data_root: Path) -> str:
    """Launch an independent processor without blocking the runner."""
    directories = config.ensure_runtime(data_root)
    command = [
        sys.executable, "-m", "helper_agent.cli",
        "--data-root", str(data_root), "process",
    ]
    output_path = directories["state"] / "processor.log"
    if os.environ.get("INVOCATION_ID"):
        unit = f"factorio-rl-helper-agent-{os.getpid()}-{time.time_ns()}.service"
        subprocess.run([
            "systemd-run", "--user", "--quiet", "--collect",
            f"--unit={unit}",
            f"--working-directory={config.REPO_ROOT}",
            f"--property=StandardOutput=append:{output_path}",
            f"--property=StandardError=append:{output_path}",
            *command,
        ], check=True)
        return f"unit={unit}"
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    with output_path.open("ab") as output:
        process = subprocess.Popen(
            command,
            cwd=config.REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
    return f"pid={process.pid}"


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
            restart_command=settings.restart_command,
            restart_ready_timeout_seconds=settings.restart_ready_timeout_seconds,
            restart_cooldown_seconds=settings.restart_cooldown_seconds,
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
