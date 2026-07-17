# Path: orchestrator/game_bridge.py
# Purpose: Transport between Python and a running Factorio server: RCON commands out, script-output files in.

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional, Set

from tools.rcon_client import RconClient

SNAPSHOT_SUBDIR = Path("factorio_mod") / "snapshots"
GHOST_OBSERVATION_SUBDIR = Path("factorio_mod") / "ghost_observations"
EXECUTION_REPORT_SUBDIR = Path("factorio_mod") / "execution_reports"


class BridgeError(RuntimeError):
    pass


class GameBridge:
    """One live game connection: sends console commands via RCON and
    ingests the JSON files the mod writes into script-output."""

    def __init__(
        self,
        script_output: Path,
        host: str = "127.0.0.1",
        port: int = 27015,
        password: str = "",
        poll_interval: float = 0.5,
        command_timeout: float = 300.0,
    ):
        self.script_output = Path(script_output)
        if not self.script_output.is_dir():
            raise BridgeError(f"script-output directory not found: {self.script_output}")
        # Long socket timeout: a megabase /snapshot can block the server for a minute+.
        self._rcon = RconClient(host, port, password, timeout=command_timeout)
        self._poll_interval = poll_interval

    def close(self) -> None:
        self._rcon.close()

    def command(self, text: str) -> str:
        return self._rcon.command(text)

    def _existing_files(self, subdir: Path) -> Set[str]:
        directory = self.script_output / subdir
        if not directory.is_dir():
            return set()
        return {item.name for item in directory.glob("*.json")}

    def _wait_for_new_file(self, subdir: Path, known: Set[str], timeout: float) -> Path:
        directory = self.script_output / subdir
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if directory.is_dir():
                fresh = sorted(set(item.name for item in directory.glob("*.json")) - known)
                if fresh:
                    candidate = directory / fresh[-1]
                    # The game may still be flushing; accept only parseable JSON.
                    try:
                        with candidate.open("r", encoding="utf-8") as handle:
                            json.load(handle)
                        return candidate
                    except (json.JSONDecodeError, OSError):
                        pass
            time.sleep(self._poll_interval)
        raise BridgeError(
            f"Timed out after {timeout:.0f}s waiting for a new file in {directory}. "
            "Is the mod loaded and the server unpaused (auto_pause off with zero players)?"
        )

    def _run_and_collect(self, command_text: str, subdir: Path, timeout: float) -> Path:
        known = self._existing_files(subdir)
        response = self.command(command_text)
        if response.strip():
            lowered = response.lower()
            if "error" in lowered or "blocked" in lowered:
                raise BridgeError(f"Command {command_text!r} failed: {response.strip()}")
        return self._wait_for_new_file(subdir, known, timeout)

    def request_snapshot(self, timeout: float = 300.0) -> Path:
        return self._run_and_collect("/snapshot", SNAPSHOT_SUBDIR, timeout)

    def export_ghost_observation(self, timeout: float = 120.0) -> Path:
        return self._run_and_collect("/export_ghost_observation", GHOST_OBSERVATION_SUBDIR, timeout)

    def execute_ghost_plan(self, authorization: dict, ghost_plan: dict, timeout: float = 120.0) -> Path:
        payload = json.dumps({"authorization": authorization, "ghost_plan": ghost_plan}, separators=(",", ":"))
        return self._run_and_collect(f"/execute_ghost_plan {payload}", EXECUTION_REPORT_SUBDIR, timeout)


def load_json(path: Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)
