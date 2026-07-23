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
CONSTRUCTION_REPORT_SUBDIR = Path("factorio_mod") / "construction_reports"
SCAFFOLD_REPORT_SUBDIR = Path("factorio_mod") / "scaffold_reports"
ORE_SEED_REPORT_SUBDIR = Path("factorio_mod") / "ore_seed_reports"
WATER_SEED_REPORT_SUBDIR = Path("factorio_mod") / "water_seed_reports"
LAYOUT_REPORT_SUBDIR = Path("factorio_mod") / "layout_reports"
LIVE_EXECUTION_REPORT_SUBDIR = Path("factorio_mod") / "live_execution_reports"
RESEARCH_REPORT_SUBDIR = Path("factorio_mod") / "research_reports"
TOPOLOGY_REPORT_SUBDIR = Path("factorio_mod") / "topology_reports"
RECIPE_CATALOG_SUBDIR = Path("factorio_mod") / "recipe_catalogs"


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

    def request_snapshot(self, timeout: float = 300.0, surface: Optional[str] = None) -> Path:
        command = f"/snapshot {surface}" if surface else "/snapshot"
        return self._run_and_collect(command, SNAPSHOT_SUBDIR, timeout)

    def export_ghost_observation(self, timeout: float = 120.0) -> Path:
        return self._run_and_collect("/export_ghost_observation", GHOST_OBSERVATION_SUBDIR, timeout)

    def execute_ghost_plan(self, authorization: dict, ghost_plan: dict, timeout: float = 120.0) -> Path:
        payload = json.dumps({"authorization": authorization, "ghost_plan": ghost_plan}, separators=(",", ":"))
        return self._run_and_collect(f"/execute_ghost_plan {payload}", EXECUTION_REPORT_SUBDIR, timeout)

    def execute_construction(self, authorization: dict, execution_report: dict, timeout: float = 120.0) -> Path:
        payload = json.dumps(
            {"authorization": authorization, "execution_report": execution_report}, separators=(",", ":")
        )
        return self._run_and_collect(f"/execute_construction {payload}", CONSTRUCTION_REPORT_SUBDIR, timeout)

    def inspect_sandbox_topology(self, timeout: float = 60.0) -> Path:
        return self._run_and_collect(
            "/inspect_sandbox_topology", TOPOLOGY_REPORT_SUBDIR, timeout
        )

    def reconcile_sandbox_topology(
        self, mode: str, *, confirm: bool = False, timeout: float = 120.0
    ) -> Path:
        if mode not in {"reset", "reconcile"}:
            raise ValueError("topology mode must be reset or reconcile")
        payload = json.dumps({"mode": mode, "confirm": confirm}, separators=(",", ":"))
        return self._run_and_collect(
            f"/reconcile_sandbox_topology {payload}", TOPOLOGY_REPORT_SUBDIR, timeout
        )
    def export_recipe_catalog(self, timeout: float = 120.0) -> Path:
        return self._run_and_collect("/export_recipe_catalog", RECIPE_CATALOG_SUBDIR, timeout)

    def execute_upgrade_plan(self, authorization: dict, upgrade_plan: dict, timeout: float = 120.0) -> Path:
        payload = json.dumps({"authorization": authorization, "upgrade_plan": upgrade_plan}, separators=(",", ":"))
        return self._run_and_collect(f"/execute_upgrade_plan {payload}", EXECUTION_REPORT_SUBDIR, timeout)

    def ensure_scaffolding(self, payload: dict, timeout: float = 120.0) -> Path:
        body = json.dumps(payload, separators=(",", ":"))
        return self._run_and_collect(f"/ensure_sandbox_scaffolding {body}", SCAFFOLD_REPORT_SUBDIR, timeout)

    def seed_ore_patches(self, payload: dict, timeout: float = 120.0) -> Path:
        body = json.dumps(payload, separators=(",", ":"))
        return self._run_and_collect(f"/seed_ore_patches {body}", ORE_SEED_REPORT_SUBDIR, timeout)

    def seed_water_lakes(self, payload: dict, timeout: float = 120.0) -> Path:
        body = json.dumps(payload, separators=(",", ":"))
        return self._run_and_collect(f"/seed_water_lakes {body}", WATER_SEED_REPORT_SUBDIR, timeout)

    def build_layout(self, authorization: dict, build_plan: dict, timeout: float = 120.0) -> Path:
        body = json.dumps({"authorization": authorization, "build_plan": build_plan}, separators=(",", ":"))
        return self._run_and_collect(f"/build_layout_plan {body}", LAYOUT_REPORT_SUBDIR, timeout)

    def verify_electronics_execution(self, timeout: float = 120.0) -> Path:
        return self._run_and_collect(
            "/verify_electronics_execution", LIVE_EXECUTION_REPORT_SUBDIR, timeout
        )

    def save_game(self) -> str:
        """Persist mutations without stopping a server owned by another process."""
        response = self.command("/server-save")
        if "error" in response.lower():
            raise BridgeError(f"Server save failed: {response.strip()}")
        return response

    def set_research(self, technology: str, timeout: float = 60.0) -> Path:
        payload = json.dumps({"technology": technology}, separators=(",", ":"))
        return self._run_and_collect(f"/set_research {payload}", RESEARCH_REPORT_SUBDIR, timeout)

    def research_status(self, timeout: float = 60.0) -> Path:
        return self._run_and_collect("/research_status", RESEARCH_REPORT_SUBDIR, timeout)


def load_json(path: Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)
