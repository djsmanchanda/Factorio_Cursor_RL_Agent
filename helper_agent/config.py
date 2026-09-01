# Path: helper_agent/config.py
# Purpose: Resolve Helper Agent repository contracts and mutable local runtime paths.

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDEcar_ROOT = REPO_ROOT / "Helper_Agent"


@dataclass(frozen=True)
class HelperConfig:
    data_root: Path
    model_endpoint: str
    model_name: str
    api_key_env: str
    timeout_seconds: int
    restart_command: tuple[str, ...]
    restart_ready_timeout_seconds: int
    restart_cooldown_seconds: int


def default_config() -> HelperConfig:
    """Return repository defaults with user-local mutable data paths."""
    payload = tomllib.loads((SIDEcar_ROOT / "config" / "agent.toml").read_text("utf-8"))
    data_root = Path(payload["paths"]["data_root"]).expanduser()
    model = payload.get("model", {})
    restart_command = model.get("restart_command", [])
    if not isinstance(restart_command, list) or not all(
        isinstance(item, str) for item in restart_command
    ):
        raise ValueError("model.restart_command must be an array of strings")
    return HelperConfig(
        data_root=data_root,
        model_endpoint=str(model.get("endpoint", "")).strip(),
        model_name=str(model.get("model", "local-lite")),
        api_key_env=str(model.get("api_key_env", "")).strip(),
        timeout_seconds=int(model.get("timeout_seconds", 60)),
        restart_command=tuple(restart_command),
        restart_ready_timeout_seconds=int(
            model.get("restart_ready_timeout_seconds", 180)
        ),
        restart_cooldown_seconds=int(model.get("restart_cooldown_seconds", 600)),
    )


def runtime_directories(data_root: Path) -> dict[str, Path]:
    return {
        "inbox": data_root / "inbox",
        "processed": data_root / "processed",
        "reports": data_root / "reports",
        "incidents": data_root / "casebook" / "incidents",
        "skills": data_root / "casebook" / "skills",
        "feedback": data_root / "casebook" / "feedback",
        "state": data_root / "state",
    }


def ensure_runtime(data_root: Path) -> dict[str, Path]:
    directories = runtime_directories(data_root)
    for path in directories.values():
        path.mkdir(parents=True, exist_ok=True)
    (data_root / "state" / "index.jsonl").touch(exist_ok=True)
    (data_root / "state" / "ledger.jsonl").touch(exist_ok=True)
    return directories
