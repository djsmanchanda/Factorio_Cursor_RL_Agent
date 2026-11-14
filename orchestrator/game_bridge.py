# Path: orchestrator/game_bridge.py
# Purpose: Transport between Python and a running Factorio server: RCON commands out, script-output files in.

from __future__ import annotations

import json
import hashlib
import re
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
LOGISTIC_INVENTORY_REPORT_SUBDIR = Path("factorio_mod") / "logistic_inventory_reports"
TOPOLOGY_REPORT_SUBDIR = Path("factorio_mod") / "topology_reports"
RECIPE_CATALOG_SUBDIR = Path("factorio_mod") / "recipe_catalogs"

# The mod writes one tick-stamped JSON per command and never removes any, while
# every collection globs and stats the whole subdirectory -- once up front and
# again on each poll. Unbounded history therefore makes each command slower than
# the last, forever, across every run sharing one script-output. Only the file a
# command just produced is ever read back, so older reports are pure history:
# keep a generous window for post-mortems and drop the rest. Reports whose
# content repeats the newest file on disk (ignoring the tick stamp) are dropped
# on collection instead: if nothing has changed, no new log is kept.
REPORT_RETENTION = 200


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

    def _layout_preflight(self, build_plan: dict, protocol: str) -> int:
        from orchestrator.configuration_preconditions import _unique_object, _reject_constant

        if not isinstance(build_plan, dict) or not isinstance(build_plan.get("phases"), list):
            raise ValueError("build plan phases must be an array")
        if "optimizer_ownership" in build_plan:
            raise BridgeError("executor does not support optimizer ownership")
        actions = []
        for phase in build_plan["phases"]:
            if not isinstance(phase, dict) or not isinstance(phase.get("actions"), list):
                raise ValueError("phase actions must be an array")
            for action in phase["actions"]:
                if not isinstance(action, dict) or not isinstance(action.get("action_type"), str):
                    raise ValueError("invalid layout action")
                if "optimizer_ownership" in action:
                    raise BridgeError("executor does not support optimizer ownership")
                if action["action_type"] not in {"place_entity", "place_ghost", "place_tile_ghost", "configure_entity", "remove_entity"}:
                    raise BridgeError("unsupported layout action")
                actions.append(action)
        guarded = any("configuration_preconditions" in action for action in actions)
        if any("configuration_preconditions" in a and a["action_type"] != "configure_entity" for a in actions):
            raise BridgeError("configuration guards require configure_entity")
        # Existing unguarded legacy callers retain their original path. Guarded
        # and correlated operations require explicit, current capabilities.
        if protocol != "legacy-tick-v1" or guarded:
            try:
                caps = json.loads(self.command("/layout_executor_capabilities"),
                                  object_pairs_hook=_unique_object, parse_constant=_reject_constant)
                if not isinstance(caps, dict):
                    raise ValueError("invalid capabilities")
                if guarded and (type(caps.get("configuration_preconditions")) is not int
                                or caps["configuration_preconditions"] != 1):
                    raise ValueError("configuration guards unavailable")
                if protocol != "legacy-tick-v1":
                    advertised = caps.get("actions")
                    if (not isinstance(caps.get("receipt_protocols"), list)
                            or protocol not in caps["receipt_protocols"]
                            or type(caps.get("correlated_identity")) is not int or caps["correlated_identity"] != 1
                            or type(caps.get("idempotent_requests")) is not int or caps["idempotent_requests"] != 1
                            or caps.get("ownership") != "force-surface-v1"
                            or not isinstance(advertised, list)
                            or any(a["action_type"] not in advertised for a in actions)):
                        raise ValueError("required executor capability unavailable")
            except Exception as error:
                raise BridgeError("layout executor capabilities unknown or incompatible") from error
        return sum(a["action_type"] != "remove_entity" for a in actions)

    def build_layout(self, authorization: dict, build_plan: dict, timeout: float = 120.0) -> Path:
        self._layout_preflight(build_plan, "legacy-tick-v1")
        body = json.dumps({"authorization": authorization, "build_plan": build_plan}, separators=(",", ":"))
        return self._run_and_collect(f"/build_layout_plan {body}", LAYOUT_REPORT_SUBDIR, timeout)

    @staticmethod
    def _layout_request(authorization, build_plan, request_id, stream_id, sequence):
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("invalid request id")
        streamed = stream_id is not None or sequence is not None
        if streamed and (not isinstance(stream_id, str) or not re.fullmatch(r"[0-9a-f]{16}", stream_id)
                         or type(sequence) is not int or not 1 <= sequence <= 4294967295
                         or request_id != f"{stream_id}{sequence:016x}"):
            raise ValueError("stream identity mismatch")
        if not isinstance(authorization, dict):
            raise ValueError("authorization must be an object")
        canonical = json.dumps({"authorization": authorization, "build_plan": build_plan},
                               sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        payload = {"authorization": authorization, "build_plan": build_plan,
                   "request_id": request_id, "request_digest": digest}
        if streamed:
            payload.update(stream_id=stream_id, sequence=sequence)
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        if len(body) > 262144:
            raise ValueError("layout request exceeds executor limit")
        return body, digest, "layout-submission-receipt-v2" if streamed else "layout-submission-receipt-v1"

    @staticmethod
    def _validate_layout_receipt(report, request_id, digest, protocol, count, stream_id, sequence):
        if (not isinstance(report, dict) or report.get("request_id") != request_id
                or report.get("request_digest") != digest or report.get("receipt_version") != protocol
                or (stream_id is not None and (report.get("stream_id") != stream_id
                    or type(report.get("sequence")) is not int or report["sequence"] != sequence))
                or (stream_id is None and ("stream_id" in report or "sequence" in report))
                or type(report.get("tick")) is not int or report["tick"] < 0
                or type(report.get("ok")) is not bool):
            raise BridgeError("layout receipt failed correlation or validation")
        if report["ok"]:
            keys = ("attempted_placements", "succeeded_placements", "already_present_placements", "failed_placements")
            if (any(type(report.get(k)) is not int or report[k] < 0 for k in keys)
                    or report["attempted_placements"] != count or report["failed_placements"] != 0
                    or report["attempted_placements"] != report["succeeded_placements"] + report["already_present_placements"]
                    or report.get("placement_failures") != []):
                raise BridgeError("layout receipt has invalid placement counts")
        elif not isinstance(report.get("error"), str) or not report["error"]:
            raise BridgeError("layout receipt lacks failure reason")
        return report

    def build_layout_correlated(self, authorization: dict, build_plan: dict, *, request_id: str,
                                timeout: float = 120.0, stream_id: str | None = None,
                                sequence: int | None = None) -> dict:
        body, digest, protocol = self._layout_request(authorization, build_plan, request_id, stream_id, sequence)
        count = self._layout_preflight(build_plan, protocol)
        path = self._run_and_collect("/build_layout_plan " + body, LAYOUT_REPORT_SUBDIR, timeout,
                                     expected_name=f"layout_{request_id}.json")
        from orchestrator.configuration_preconditions import _unique_object, _reject_constant
        try:
            with path.open(encoding="utf-8") as handle:
                report = json.load(handle, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except (OSError, ValueError) as error:
            raise BridgeError("invalid layout receipt; outcome unknown") from error
        return self._validate_layout_receipt(report, request_id, digest, protocol, count, stream_id, sequence)

    def read_layout_receipt(self, authorization: dict, build_plan: dict, *, request_id: str,
                            stream_id: str | None = None, sequence: int | None = None) -> dict:
        body, digest, protocol = self._layout_request(authorization, build_plan, request_id, stream_id, sequence)
        del body
        count = sum(a["action_type"] != "remove_entity" for p in build_plan["phases"] for a in p["actions"])
        from orchestrator.configuration_preconditions import _unique_object, _reject_constant
        try:
            path = self.script_output / LAYOUT_REPORT_SUBDIR / f"layout_{request_id}.json"
            with path.open(encoding="utf-8") as handle:
                report = json.load(handle, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except (OSError, ValueError) as error:
            raise BridgeError("layout receipt unavailable; outcome unknown") from error
        return self._validate_layout_receipt(report, request_id, digest, protocol, count, stream_id, sequence)

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
