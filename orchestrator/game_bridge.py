# Path: orchestrator/game_bridge.py
# Purpose: Transport between Python and a running Factorio server: RCON commands out, script-output files in.

from __future__ import annotations

import json
import math
import hashlib
import os
import re
import stat
import time
import uuid
from pathlib import Path
from typing import Optional

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
SCIENCE_REPORT_SUBDIR = Path("factorio_mod") / "science_reports"
ROCKET_LAUNCH_REPORT_SUBDIR = Path("factorio_mod") / "rocket_launch_observations"
ROCKET_LAUNCH_EVENT_SUBDIR = Path("factorio_mod") / "rocket_launch_events"
ROCKET_SILO_REPORT_SUBDIR = Path("factorio_mod") / "rocket_silo_observations"
LOGISTIC_INVENTORY_REPORT_SUBDIR = Path("factorio_mod") / "logistic_inventory_reports"
TOPOLOGY_REPORT_SUBDIR = Path("factorio_mod") / "topology_reports"
RECIPE_CATALOG_SUBDIR = Path("factorio_mod") / "recipe_catalogs"
RUNTIME_CATALOG_SUBDIR = Path("factorio_mod") / "runtime_catalogs"
OPTIMIZER_CONSUMER_OBSERVATION_SUBDIR = Path("factorio_mod") / "optimizer_consumer_observations"

# The mod writes one tick-stamped JSON per command and never removes any, while
# every collection globs and stats the whole subdirectory -- once up front and
# again on each poll. Unbounded history therefore makes each command slower than
# the last, forever, across every run sharing one script-output. Only the file a
# command just produced is ever read back, so older reports are pure history:
# keep a generous window for post-mortems and drop the rest. Reports whose
# content repeats the newest file on disk (ignoring the tick stamp) are dropped
# on collection instead: if nothing has changed, no new log is kept.
REPORT_RETENTION = 200
CORRELATED_REPORT_MAX_BYTES = 32 * 1024 * 1024


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
        retain_reports: int = REPORT_RETENTION,
        episode_id: str | None = None,
    ):
        self.script_output = Path(script_output)
        if episode_id is not None and (not isinstance(episode_id, str) or not episode_id):
            raise ValueError("episode_id must be a non-empty string when supplied")
        self.episode_id = episode_id
        if not self.script_output.is_dir():
            raise BridgeError(f"script-output directory not found: {self.script_output}")
        if retain_reports < 1:
            raise ValueError("retain_reports must keep at least the file just collected")
        # Long socket timeout: a megabase /snapshot can block the server for a minute+.
        self._rcon = RconClient(host, port, password, timeout=command_timeout)
        self._poll_interval = poll_interval
        self._retain_reports = retain_reports

    def close(self) -> None:
        self._rcon.close()

    def command(self, text: str) -> str:
        return self._rcon.command(text)

    def _existing_files(self, subdir: Path) -> dict[str, tuple[int, int]]:
        directory = self.script_output / subdir
        if not directory.is_dir():
            return {}
        snapshot: dict[str, tuple[int, int]] = {}
        for item in directory.glob("*.json"):
            try:
                stat = item.stat()
            except OSError:
                continue
            snapshot[item.name] = (stat.st_mtime_ns, stat.st_size)
        return snapshot

    def _wait_for_new_file(
        self, subdir: Path, known: dict[str, tuple[int, int]], timeout: float,
        *, expected_name: str | None = None,
    ) -> Path:
        directory = self.script_output / subdir
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if directory.is_dir():
                fresh: list[tuple[int, str, Path]] = []
                candidates = (
                    [directory / expected_name]
                    if expected_name is not None else directory.glob("*.json")
                )
                for item in candidates:
                    try:
                        stat = item.stat()
                    except OSError:
                        continue
                    signature = (stat.st_mtime_ns, stat.st_size)
                    if known.get(item.name) != signature:
                        fresh.append((stat.st_mtime_ns, item.name, item))
                if fresh:
                    candidate = max(fresh)[2]
                    # The game may still be flushing; accept only parseable JSON.
                    try:
                        with candidate.open("r", encoding="utf-8") as handle:
                            json.load(handle)
                        return candidate
                    except (json.JSONDecodeError, OSError):
                        pass
            time.sleep(self._poll_interval)
        raise BridgeError(
            f"Timed out after {timeout:.0f}s waiting for a new or updated file in {directory}. "
            "Is the mod loaded and the server unpaused (auto_pause off with zero players)?"
        )

    def _prune_reports(self, subdir: Path, keep: Path) -> int:
        """Trim one report subdirectory to the newest ``retain_reports`` files.

        ``keep`` -- the report this command just collected -- is retained
        unconditionally, so a caller can always read what it was handed even if
        the retention window is smaller than the burst that produced it.
        Ordering is (mtime, name) so a filesystem with coarse timestamp
        resolution still evicts deterministically. Deletion failures are
        ignored: losing a pruning race must never fail a live build.
        """
        directory = self.script_output / subdir
        if not directory.is_dir():
            return 0
        candidates: list[tuple[int, str, Path]] = []
        for item in directory.glob("*.json"):
            try:
                candidates.append((item.stat().st_mtime_ns, item.name, item))
            except OSError:
                continue
        if len(candidates) <= self._retain_reports:
            return 0
        candidates.sort(reverse=True)
        removed = 0
        for _mtime, _name, item in candidates[self._retain_reports:]:
            if item == keep:
                continue
            try:
                item.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    @staticmethod
    def _content_signature(payload: object) -> str:
        """Canonical form of a report payload, ignoring the tick stamp."""
        if isinstance(payload, dict):
            payload = {key: value for key, value in payload.items() if key != "tick"}
        return json.dumps(payload, sort_keys=True)

    def _dedupe_unchanged_report(self, subdir: Path, collected: Path) -> Path:
        """Drop ``collected`` when it repeats the newest report already on disk.

        Pollers (e.g. a dashboard refreshing research every few seconds)
        re-issue the same command long after the game state stopped changing,
        and the mod answers every call with a fresh tick-stamped file. Only
        the returned file's content is ever read back, so a repeat is pure
        history: delete it and hand the caller the report it duplicates. A
        genuine transition -- research started, completed, next target queued
        -- always differs, so it is always kept. Anything unparseable is kept:
        a failed comparison must never delete a report.
        """
        try:
            with collected.open("r", encoding="utf-8") as handle:
                fresh = self._content_signature(json.load(handle))
        except (json.JSONDecodeError, OSError, ValueError):
            return collected
        directory = self.script_output / subdir
        try:
            others = [item for item in directory.glob("*.json") if item != collected]
        except OSError:
            return collected
        if not others:
            return collected
        try:
            previous = max(
                others, key=lambda item: (item.stat().st_mtime_ns, item.name)
            )
            with previous.open("r", encoding="utf-8") as handle:
                old = self._content_signature(json.load(handle))
        except (OSError, json.JSONDecodeError, ValueError):
            return collected
        if old != fresh:
            return collected
        try:
            collected.unlink()
        except OSError:
            return collected
        return previous

    def _run_and_collect(
        self, command_text: str, subdir: Path, timeout: float, *,
        expected_name: str | None = None,
    ) -> Path:
        known = self._existing_files(subdir)
        response = self.command(command_text)
        if response.strip():
            lowered = response.lower()
            if "error" in lowered or "blocked" in lowered:
                raise BridgeError(f"Command {command_text!r} failed: {response.strip()}")
        collected = self._wait_for_new_file(
            subdir, known, timeout, expected_name=expected_name,
        )
        if expected_name is None:
            collected = self._dedupe_unchanged_report(subdir, collected)
        self._prune_reports(subdir, collected)
        return collected

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
    def export_recipe_catalog(
        self, timeout: float = 120.0, *, force: str | None = None,
    ) -> Path:
        """Export enabled recipes for the legacy planner or one existing force."""
        command = "/export_recipe_catalog"
        if force is not None:
            if not force:
                raise ValueError("force must be non-empty when supplied")
            command += " " + force
        return self._run_and_collect(command, RECIPE_CATALOG_SUBDIR, timeout)

    def execute_upgrade_plan(
        self, authorization: dict, upgrade_plan: dict, *,
        surface: str, force: str, timeout: float = 120.0,
    ) -> Path:
        """Order upgrades on one explicit existing surface and force."""
        if not isinstance(surface, str) or not surface:
            raise ValueError("surface must be a non-empty string")
        if not isinstance(force, str) or not force:
            raise ValueError("force must be a non-empty string")
        payload = json.dumps({
            "authorization": authorization,
            "upgrade_plan": upgrade_plan,
            "surface": surface,
            "force": force,
        }, separators=(",", ":"))
        return self._run_and_collect(
            f"/execute_upgrade_plan {payload}", EXECUTION_REPORT_SUBDIR, timeout,
        )

    def execute_deconstruction(
        self, authorization: dict, deconstruction_plan: dict, *,
        surface: str, force: str, timeout: float = 120.0,
    ) -> Path:
        payload = json.dumps({
            "authorization": authorization, "deconstruction_plan": deconstruction_plan,
            "surface": surface, "force": force,
        }, separators=(",", ":"))
        return self._run_and_collect(
            f"/execute_deconstruction_plan {payload}", EXECUTION_REPORT_SUBDIR, timeout,
        )

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

    def set_research(
        self, technology: str, timeout: float = 60.0, *, force: str | None = None,
    ) -> Path:
        """Queue one technology on ``force``.

        Omitting ``force`` preserves the legacy planner-force command. Real
        base callers must supply their existing force explicitly.
        """
        payload = {"technology": technology}
        if force is not None:
            payload["force"] = force
        body = json.dumps(payload, separators=(",", ":"))
        return self._run_and_collect(f"/set_research {body}", RESEARCH_REPORT_SUBDIR, timeout)

    def research_status(
        self, timeout: float = 60.0, *, force: str | None = None,
        technology: str | None = None,
    ) -> Path:
        """Export research state, optionally including one technology's state."""
        payload = {key: value for key, value in {
            "force": force, "technology": technology,
        }.items() if value is not None}
        command = "/research_status"
        expected_name = None
        if payload:
            request_id = uuid.uuid4().hex
            payload["request_id"] = request_id
            expected_name = f"research_status_{request_id}.json"
            command += " " + json.dumps(payload, separators=(",", ":"))
        return self._run_and_collect(
            command, RESEARCH_REPORT_SUBDIR, timeout,
            expected_name=expected_name,
        )

    def research_options(
        self, timeout: float = 60.0, *, force: str | None = None,
    ) -> Path:
        """List currently open research targets for an existing force."""
        payload = {"force": force} if force is not None else {}
        command = "/research_options"
        if payload:
            command += " " + json.dumps(payload, separators=(",", ":"))
        return self._run_and_collect(command, RESEARCH_REPORT_SUBDIR, timeout)

    def science_status(self, surface: str, force: str, timeout: float = 60.0) -> Path:
        """Collect read-only research and lab telemetry for one existing target."""
        if not isinstance(surface, str) or not surface:
            raise ValueError("surface must be a non-empty string")
        if not isinstance(force, str) or not force:
            raise ValueError("force must be a non-empty string")
        body = json.dumps({"surface": surface, "force": force}, separators=(",", ":"))
        return self._run_and_collect(f"/science_status {body}", SCIENCE_REPORT_SUBDIR, timeout)

    def rocket_launch_observation(
        self, surface: str, force: str, *, enabled: bool = True,
        timeout: float = 60.0,
    ) -> Path:
        """Arm, disarm, or report actual rocket ascent observation for one target.

        Each request carries a unique filename-safe ``request_id``; the scope
        response is collected by its exact filename and validated before it is
        returned. A fresh caller-generated ``session_id`` identifies every new
        arm, so event filenames stay distinct across save restores as long as
        the caller keeps supplying independent identities. Durable launch
        events live in a separate directory and are never selected, deduped or
        pruned by this command.
        """
        if not isinstance(surface, str) or not surface:
            raise ValueError("surface must be a non-empty string")
        if not isinstance(force, str) or not force:
            raise ValueError("force must be a non-empty string")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        request_id = uuid.uuid4().hex
        session_id = uuid.uuid4().hex
        body = json.dumps(
            {
                "surface": surface, "force": force, "enabled": enabled,
                "request_id": request_id, "session_id": session_id,
            },
            separators=(",", ":"),
        )
        expected_name = f"rocket_launch_scope_{request_id}.json"
        collected = self._run_and_collect(
            f"/rocket_launch_observation {body}", ROCKET_LAUNCH_REPORT_SUBDIR,
            timeout, expected_name=expected_name,
        )
        self._validate_rocket_launch_scope_response(
            collected, request_id=request_id, session_id=session_id,
            surface=surface, force=force, enabled=enabled,
            expected_name=expected_name,
        )
        return collected

    @staticmethod
    def _validate_rocket_launch_scope_response(
        path: Path, *, request_id: str, session_id: str, surface: str,
        force: str, enabled: bool, expected_name: str,
    ) -> None:
        """Reject a scope response that is not this exact successful request."""
        if path.name != expected_name:
            raise BridgeError(
                f"Rocket-launch response {path.name!r} does not match "
                f"expected {expected_name!r}"
            )
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError, ValueError) as error:
            raise BridgeError(
                f"Rocket-launch response {path.name!r} is not readable JSON: {error}"
            ) from error
        if not isinstance(payload, dict):
            raise BridgeError(
                f"Rocket-launch response {path.name!r} is not a JSON object"
            )
        if payload.get("kind") != "rocket_launch_scope":
            raise BridgeError(
                f"Rocket-launch response {path.name!r} has unexpected kind "
                f"{payload.get('kind')!r}"
            )
        if payload.get("request_id") != request_id:
            raise BridgeError(
                f"Rocket-launch response {path.name!r} does not match request_id "
                f"{request_id!r}"
            )
        if payload.get("ok") is not True:
            detail = payload.get("error") or "response did not report ok"
            raise BridgeError(
                f"Rocket-launch command failed for request {request_id!r}: {detail}"
            )
        session_started = payload.get("session_started")
        if not isinstance(session_started, bool):
            raise BridgeError(
                f"Rocket-launch response {path.name!r} has no boolean "
                "session_started"
            )
        reported_session = payload.get("session_id")
        if reported_session is not None and (
            not isinstance(reported_session, str)
            or not reported_session
            or len(reported_session) > 64
            or not all(
                character.isascii()
                and (character.isalnum() or character in "_-")
                for character in reported_session
            )
        ):
            raise BridgeError(
                f"Rocket-launch response {path.name!r} has an unsafe session_id "
                f"{reported_session!r}"
            )
        if session_started and reported_session != session_id:
            raise BridgeError(
                f"Rocket-launch response {path.name!r} started session "
                f"{reported_session!r}, expected {session_id!r}"
            )
        for key, expected in (
            ("surface", surface), ("force", force), ("enabled", enabled),
        ):
            if payload.get(key) != expected:
                raise BridgeError(
                    f"Rocket-launch response {path.name!r} has {key}="
                    f"{payload.get(key)!r}, expected {expected!r}"
                )

    def _collect_correlated_catalog(
        self, *, command: str, subdir: Path, expected_name: str,
        timeout: float, label: str,
    ) -> tuple[bytes, object]:
        expected_path = self.script_output / subdir / expected_name
        if expected_path.exists() or expected_path.is_symlink():
            raise BridgeError(f"{label} response path already exists")
        if not hasattr(os, "O_NOFOLLOW"):
            raise BridgeError(f"{label} collection requires O_NOFOLLOW")
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON field")
                result[key] = value
            return result

        def invalid_number(value):
            raise ValueError("non-finite JSON number")

        response = self.command(command)
        if response.strip() and ("error" in response.lower() or "blocked" in response.lower()):
            raise BridgeError(f"{label} command failed")
        deadline = time.monotonic() + timeout
        while True:
            try:
                descriptor = os.open(expected_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as handle:
                    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                        raise BridgeError(f"{label} response is not a regular file")
                    raw = handle.read(CORRELATED_REPORT_MAX_BYTES + 1)
                    if len(raw) > CORRELATED_REPORT_MAX_BYTES:
                        raise BridgeError(f"{label} response exceeds size limit")
                report = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_number)
                break
            except (FileNotFoundError, json.JSONDecodeError):
                if time.monotonic() >= deadline:
                    raise BridgeError(f"Timed out waiting for {label.lower()} response")
                time.sleep(self._poll_interval)
            except (ValueError, UnicodeDecodeError) as error:
                raise BridgeError(f"{label} response is not valid JSON") from error
            except OSError as error:
                raise BridgeError(f"{label} response is not safely readable") from error
        return raw, report

    def observe_rocket_silos(
        self, surface: str, force: str, *, unit_number: int | None = None,
        timeout: float = 120.0,
    ) -> dict:
        """Collect a scoped silo report, without establishing identity or authority.

        This command writes a report and does not prune prior reports. Request
        correlation does not authenticate the server, save, or output writer.
        """
        from jsonschema import Draft7Validator

        for name, value in (("surface", surface), ("force", force)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if unit_number is not None and (type(unit_number) is not int or unit_number < 0):
            raise ValueError("unit_number must be a non-negative integer")
        request_id = uuid.uuid4().hex
        request = {"request_id": request_id, "surface": surface, "force": force}
        if unit_number is not None:
            request["unit_number"] = unit_number
        body = json.dumps(request, separators=(",", ":"))
        _, report = self._collect_correlated_catalog(
            command=f"/rocket_silo_observation {body}", subdir=ROCKET_SILO_REPORT_SUBDIR,
            expected_name=f"rocket_silo_observation_{request_id}.json",
            timeout=timeout, label="Rocket-silo",
        )
        try:
            schema = json.loads((Path(__file__).resolve().parent.parent / "schemas" /
                                 "rocket_silo_observation.schema.json").read_text(encoding="utf-8"))
            valid = Draft7Validator(schema).is_valid(report)
        except (OSError, ValueError) as error:
            raise BridgeError("Rocket-silo schema is unavailable") from error
        if not valid or report.get("ok") is not True:
            raise BridgeError("Rocket-silo response failed validation or observation")
        if (report["request_id"] != request_id or report["surface"] != surface
                or report["force"] != force or report["silo_count"] != len(report["silos"])):
            raise BridgeError("Rocket-silo response identity, scope or count mismatch")
        requested = report["requested"]["unit_number"]
        if unit_number is None:
            if requested["state"] != "absent":
                raise BridgeError("Rocket-silo response filter mismatch")
        elif (requested["state"] != "available" or requested["value"] != unit_number
              or len(report["silos"]) > 1
              or any(silo["unit_number"].get("state") != "available"
                     or silo["unit_number"].get("value") != unit_number
                     for silo in report["silos"])):
            raise BridgeError("Rocket-silo response filter mismatch")
        identities = [silo["unit_number"]["value"] for silo in report["silos"]
                      if silo["unit_number"]["state"] == "available"]
        if len(identities) != len(set(identities)):
            raise BridgeError("Rocket-silo response contains duplicate identities")
        return report

    def export_recipe_catalog_capture(self, force: str, timeout: float = 120.0) -> bytes:
        """Collect correlated recipe bytes; this does not establish provenance.

        An existing force is required. This writes a report and does not prune
        prior reports; the server, output writer and save remain unverified.
        """
        if not isinstance(force, str) or not force.strip():
            raise ValueError("force must be a non-empty string")
        request_id = uuid.uuid4().hex
        body = json.dumps({"force": force, "request_id": request_id}, separators=(",", ":"))
        raw, report = self._collect_correlated_catalog(
            command=f"/export_recipe_catalog {body}", subdir=RECIPE_CATALOG_SUBDIR,
            expected_name=f"recipe_catalog_{request_id}.json", timeout=timeout, label="Recipe-catalog",
        )
        if (not isinstance(report, dict) or report.get("version") != "1.3.0"
                or report.get("request_id") != request_id or report.get("force") != force
                or type(report.get("tick")) is not int or report["tick"] < 0
                or not isinstance(report.get("recipes"), list)
                or not isinstance(report.get("raw_resources"), list)):
            raise BridgeError("Recipe-catalog response identity or scope mismatch")
        mods = report.get("active_mods")
        if (not isinstance(mods, dict) or not mods
                or any(not isinstance(name, str) or not name or not isinstance(version, str)
                       or not version for name, version in mods.items())
                or type(report.get("active_mods_count")) is not int
                or report["active_mods_count"] != len(mods)):
            raise BridgeError("Recipe-catalog response mod provenance mismatch")
        return raw

    def export_runtime_catalog(
        self, surface: str, force: str, timeout: float = 120.0,
    ) -> bytes:
        """Return correlated catalog bytes without authenticating provenance.

        This writes a report but does not prune older catalog reports. It does
        not verify the whole schema, server, save, output writer or authority.
        """
        if not isinstance(surface, str) or not surface.strip():
            raise ValueError("surface must be a non-empty string")
        if not isinstance(force, str) or not force.strip():
            raise ValueError("force must be a non-empty string")
        request_id = uuid.uuid4().hex
        body = json.dumps({"force": force, "surface": surface, "request_id": request_id},
                          separators=(",", ":"))
        raw, report = self._collect_correlated_catalog(
            command=f"/export_runtime_catalog {body}", subdir=RUNTIME_CATALOG_SUBDIR,
            expected_name=f"runtime_catalog_{request_id}.json", timeout=timeout, label="Runtime-catalog",
        )
        if (not isinstance(report, dict) or report.get("schema_version") != "1.1.0"
                or report.get("kind") != "runtime_catalog" or report.get("request_id") != request_id
                or report.get("ok") is not True):
            raise BridgeError("Runtime-catalog response identity or status mismatch")
        tick = report.get("tick")
        provenance = report.get("provenance")
        if not isinstance(provenance, dict):
            raise BridgeError("Runtime-catalog response lacks provenance")
        scope = provenance.get("scope")
        mods = provenance.get("active_mods")
        if (type(tick) is not int or tick < 0 or type(provenance.get("export_tick")) is not int
                or provenance["export_tick"] != tick
                or provenance.get("export_command") != "export_runtime_catalog"
                or not isinstance(scope, dict) or scope.get("surface_requested") is not True
                or scope.get("surface") != surface or scope.get("force") != force
                or not isinstance(mods, dict) or not mods
                or any(not isinstance(name, str) or not name or not isinstance(version, str)
                       or not version for name, version in mods.items())
                or type(provenance.get("active_mods_count")) is not int
                or provenance["active_mods_count"] != len(mods)):
            raise BridgeError("Runtime-catalog response provenance mismatch")
        return raw

    def optimizer_consumer_observation(self, *, surface: str, force: str, entity: str,
                                       position: dict, unit_number: int, timeout: float = 60.0) -> dict:
        """Collect one read-only consumer counter; the report does not prove delivery or identity."""
        from jsonschema import Draft7Validator

        for label, value in (("surface", surface), ("force", force), ("entity", entity)):
            if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128:
                raise ValueError(f"{label} must be a non-empty string of at most 128 bytes")
        if (type(unit_number) is not int or not 1 <= unit_number <= 9007199254740991
                or not isinstance(position, dict) or set(position) != {"x", "y"}
                or any(type(value) not in (int, float) or abs(value) > 1000000 or not math.isfinite(value)
                       for value in position.values())):
            raise ValueError("consumer unit and position are invalid")
        request_id = uuid.uuid4().hex
        request = {"request_id": request_id, "surface": surface, "force": force, "entity": entity,
                   "position": dict(position), "unit_number": unit_number}
        _, report = self._collect_correlated_catalog(
            command="/optimizer_consumer_observation " + json.dumps(request, separators=(",", ":")),
            subdir=OPTIMIZER_CONSUMER_OBSERVATION_SUBDIR,
            expected_name=f"optimizer_consumer_{request_id}.json", timeout=timeout, label="Optimizer consumer observation")
        try:
            schema = json.loads((Path(__file__).resolve().parent.parent / "schemas" /
                                 "optimizer_consumer_observation.schema.json").read_text(encoding="utf-8"))
            valid = Draft7Validator(schema).is_valid(report)
        except (OSError, ValueError) as error:
            raise BridgeError("Optimizer consumer observation schema is unavailable") from error
        expected = {"entity": entity, "position": position, "unit_number": unit_number}
        if (not valid or report.get("ok") is not True or report.get("request_id") != request_id
                or report.get("surface") != surface or report.get("force") != force
                or report.get("requested") != expected or type(report.get("tick")) is not int
                or type(report["requested"]["unit_number"]) is not int):
            raise BridgeError("Optimizer consumer observation response failed correlation or validation")
        obs = report["observation"]
        if (obs["state"] == "present" and obs["products_finished"]["state"] == "available"
                and type(obs["products_finished"]["value"]) is not int):
            raise BridgeError("Optimizer consumer observation counter is not an integer")
        return report

    def logistic_inventory(self, surface: str, force: str, timeout: float = 60.0) -> Path:
        """Collect the read-only contents of every live logistic network."""
        if not isinstance(surface, str) or not surface:
            raise ValueError("surface must be a non-empty string")
        if not isinstance(force, str) or not force:
            raise ValueError("force must be a non-empty string")
        body = json.dumps({"surface": surface, "force": force}, separators=(",", ":"))
        return self._run_and_collect(
            f"/logistic_inventory {body}", LOGISTIC_INVENTORY_REPORT_SUBDIR, timeout,
        )


def load_json(path: Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)
