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
# Must match the correlated executor's command-parameter limit in layout_executor.lua.
LAYOUT_REQUEST_MAX_BYTES = 262144
SHARED_OPTIMIZER_TAG_PROTOCOL = "optimizer-shared-tags-v1"
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
RUNTIME_IDENTITY_REPORT_SUBDIR = Path("factorio_mod") / "runtime_identity_probes"
RESEARCH_LAB_OWNERSHIP_SUBDIR = Path("factorio_mod") / "research_lab_ownership"
OPTIMIZER_BUILD_EVIDENCE_SUBDIR = Path("factorio_mod") / "optimizer_build_evidence"
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
        socket_validator=None,
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
        connection_options = {"timeout": command_timeout}
        if socket_validator is not None:
            connection_options["socket_validator"] = socket_validator
        self._rcon = RconClient(host, port, password, **connection_options)
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

    @staticmethod
    def _reject_uncorrelated_shared_tags(plan) -> None:
        # Executors without the correlated protocol ignore plan-level tags and
        # would place untagged ghosts, so shared tags never take this path.
        if isinstance(plan, dict) and "optimizer_ownership" in plan:
            raise ValueError("shared optimizer tags require a correlated layout request")

    def execute_ghost_plan(self, authorization: dict, ghost_plan: dict, timeout: float = 120.0) -> Path:
        self._reject_uncorrelated_shared_tags(ghost_plan)
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
        self._reject_uncorrelated_shared_tags(build_plan)
        body = json.dumps({"authorization": authorization, "build_plan": build_plan}, separators=(",", ":"))
        return self._run_and_collect(f"/build_layout_plan {body}", LAYOUT_REPORT_SUBDIR, timeout)

    @staticmethod
    def _validate_optimizer_tag_bytes(tags):
        """Match the executor's UTF-8 byte limits before allocating a request ID."""
        if not isinstance(tags, dict) or set(tags) != {
            "optimizer_episode", "optimizer_project", "optimizer_transaction", "optimizer_plan_hash"
        }:
            raise ValueError("optimizer ownership tags are invalid")
        for field in ("optimizer_episode", "optimizer_project"):
            value = tags[field]
            try:
                size = len(value.encode("utf-8")) if isinstance(value, str) else 0
            except UnicodeEncodeError as error:
                raise ValueError("optimizer ownership tag is not valid UTF-8") from error
            if not 1 <= size <= 128:
                raise ValueError("optimizer ownership tag exceeds its UTF-8 byte limit")
        for field, length in (("optimizer_transaction", 32), ("optimizer_plan_hash", 64)):
            value = tags[field]
            if not isinstance(value, str) or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
                raise ValueError("optimizer ownership tag digest is invalid")

    @staticmethod
    def _layout_receipt_request(authorization: dict, build_plan: dict, request_id: str, *,
                                stream_id: str | None = None, sequence: int | None = None):
        from jsonschema import Draft7Validator

        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("request_id must be 32 lowercase hexadecimal characters")
        if stream_id is not None or sequence is not None:
            if (not isinstance(stream_id, str) or not re.fullmatch(r"[0-9a-f]{16}", stream_id)
                    or type(sequence) is not int or not 1 <= sequence <= 4294967295
                    or request_id != f"{stream_id}{sequence:016x}"):
                raise ValueError("stream ID, sequence, and derived request ID do not match")
        if not isinstance(authorization, dict) or not isinstance(build_plan, dict):
            raise ValueError("authorization and build_plan must be objects")
        shared_tags = "optimizer_ownership" in build_plan
        if shared_tags:
            from planners.plan_validation import validate_build_plan
            validate_build_plan(build_plan)
            GameBridge._validate_optimizer_tag_bytes(build_plan["optimizer_ownership"])
        placements = {"place_ghost", "place_ghost_no_reconcile", "place_tile_ghost", "place_entity"}
        allowed = placements | {"configure_entity", "configure_entity_if_unit_number", "remove_entity"}
        phases = build_plan.get("phases")
        if not isinstance(phases, list):
            raise ValueError("build_plan phases must be an array")
        expected_placements = 0
        for phase in phases:
            if not isinstance(phase, dict) or not isinstance(phase.get("actions"), list):
                raise ValueError("build_plan phase actions must be an array")
            for action in phase["actions"]:
                if (not isinstance(action, dict) or not isinstance(action.get("action_type"), str)
                        or action["action_type"] not in allowed):
                    raise ValueError("build_plan contains an unsupported action")
                if "optimizer_ownership" in action:
                    GameBridge._validate_optimizer_tag_bytes(action["optimizer_ownership"])
                # The Lua executor includes guarded and unguarded configuration
                # in attempted_entities/attempted_placements, but not removal.
                expected_placements += action["action_type"] != "remove_entity"
        try:
            schema = json.loads((Path(__file__).resolve().parent.parent / "schemas" /
                                 "layout_submission_receipt.schema.json").read_text(encoding="utf-8"))
            validator = Draft7Validator(schema)
        except (OSError, ValueError) as error:
            raise BridgeError("Layout receipt schema is unavailable") from error
        canonical = json.dumps({"authorization": authorization, "build_plan": build_plan},
                               sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        payload = {"authorization": authorization, "build_plan": build_plan,
                   "request_id": request_id, "request_digest": digest}
        if stream_id is not None:
            payload.update(stream_id=stream_id, sequence=sequence)
        if shared_tags:
            # A legacy correlated executor rejects this new envelope field
            # instead of silently placing untagged ghosts.
            payload["optimizer_tag_protocol"] = SHARED_OPTIMIZER_TAG_PROTOCOL
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        size = len(body.encode("ascii"))
        if size > LAYOUT_REQUEST_MAX_BYTES:
            raise ValueError(f"correlated layout request exceeds the executor's size limit: "
                             f"{size} > {LAYOUT_REQUEST_MAX_BYTES} bytes")
        return body, digest, expected_placements, validator

    @staticmethod
    def _validate_layout_receipt(report, request_id, digest, expected_placements, validator, *,
                                 stream_id=None, sequence=None):
        version = "layout-submission-receipt-v2" if stream_id is not None else "layout-submission-receipt-v1"
        if (not validator.is_valid(report) or report.get("request_id") != request_id
                or report.get("request_digest") != digest
                or report.get("receipt_version") != version
                or (stream_id is not None and (report.get("stream_id") != stream_id or report.get("sequence") != sequence))
                or type(report.get("tick")) is not int or report["tick"] < 0
                or type(report.get("ok")) is not bool):
            raise BridgeError("Layout receipt failed correlation or validation")
        if report["ok"]:
            count_keys = ("attempted_placements", "succeeded_placements", "already_present_placements", "failed_placements")
            if (any(type(report.get(key)) is not int or report[key] < 0 for key in count_keys)
                    or report["failed_placements"] != 0 or report.get("placement_failures") != []
                    or report["attempted_placements"] != expected_placements
                    or report["attempted_placements"] != report["succeeded_placements"] + report["already_present_placements"]):
                raise BridgeError("Layout receipt has invalid successful placement counts")
        elif not isinstance(report.get("error"), str) or not report["error"]:
            raise BridgeError("Layout receipt lacks a failure reason")
        return report

    def build_layout_correlated(self, authorization: dict, build_plan: dict, *,
                                request_id: str, timeout: float = 120.0,
                                stream_id: str | None = None, sequence: int | None = None) -> dict:
        """Submit one layout with a caller-persisted ID and collect its correlated receipt.

        This method grants no authorization, does not retry, and does not prove
        runtime identity or bot construction. A missing receipt is an unknown
        outcome. The Lua cache survives only as long as its save state does.
        """
        body, digest, expected_placements, validator = self._layout_receipt_request(
            authorization, build_plan, request_id, stream_id=stream_id, sequence=sequence)
        _, report = self._collect_correlated_catalog(
            command="/build_layout_plan " + body, subdir=LAYOUT_REPORT_SUBDIR,
            expected_name=f"layout_{request_id}.json", timeout=timeout, label="Layout receipt",
        )
        return self._validate_layout_receipt(report, request_id, digest, expected_placements, validator,
                                             stream_id=stream_id, sequence=sequence)

    def read_layout_receipt(self, authorization: dict, build_plan: dict, *, request_id: str,
                            stream_id: str | None = None, sequence: int | None = None) -> dict:
        """Read an existing correlated receipt without sending a command or retrying."""
        _, digest, expected_placements, validator = self._layout_receipt_request(
            authorization, build_plan, request_id, stream_id=stream_id, sequence=sequence)
        if not hasattr(os, "O_NOFOLLOW"):
            raise BridgeError("Layout receipt collection requires O_NOFOLLOW")
        path = self.script_output / LAYOUT_REPORT_SUBDIR / f"layout_{request_id}.json"
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON field")
                result[key] = value
            return result
        def invalid_number(_value):
            raise ValueError("non-finite JSON number")
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise BridgeError("Layout receipt is not a regular file")
                raw = handle.read(CORRELATED_REPORT_MAX_BYTES + 1)
            if len(raw) > CORRELATED_REPORT_MAX_BYTES:
                raise BridgeError("Layout receipt exceeds size limit")
            report = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_number)
        except (OSError, ValueError, UnicodeError) as error:
            raise BridgeError("Layout receipt is unavailable or invalid; submission outcome is unknown") from error
        return self._validate_layout_receipt(report, request_id, digest, expected_placements, validator,
                                             stream_id=stream_id, sequence=sequence)

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
        expected_current: str | None = None, expected_queue: list[str] | None = None,
        replace_queue: bool = False,
    ) -> Path:
        """Queue one technology on ``force``.

        Omitting ``force`` preserves the legacy planner-force command. Real
        base callers must supply their existing force explicitly. Supplying
        ``expected_queue`` opts into an atomic guard inside the mod: the live
        current/queue must match the observation, and a non-empty queue is
        replaced only when ``replace_queue`` is true.
        """
        payload = {"technology": technology}
        if force is not None:
            payload["force"] = force
        expected_name = None
        request_id = None
        if expected_queue is not None:
            if (not isinstance(expected_queue, list)
                    or any(not isinstance(name, str) or not name for name in expected_queue)):
                raise ValueError("expected_queue must be a list of non-empty strings")
            if expected_current is not None and (
                    not isinstance(expected_current, str) or not expected_current):
                raise ValueError("expected_current must be a non-empty string or None")
            if not isinstance(replace_queue, bool):
                raise ValueError("replace_queue must be a boolean")
            payload["expected_queue"] = list(expected_queue)
            payload["expected_current"] = expected_current
            payload["replace_queue"] = replace_queue
            request_id = uuid.uuid4().hex
            payload["request_id"] = request_id
            expected_name = f"research_{request_id}.json"
        elif expected_current is not None or replace_queue:
            raise ValueError("expected_current and replace_queue require expected_queue")
        body = json.dumps(payload, separators=(",", ":"))
        collected = self._run_and_collect(
            f"/set_research {body}", RESEARCH_REPORT_SUBDIR, timeout,
            expected_name=expected_name,
        )
        if request_id is not None:
            self._validate_set_research_response(
                collected, request_id=request_id, expected_name=expected_name,
                technology=technology,
                force=force if force is not None else "planner",
            )
        return collected

    @staticmethod
    def _validate_set_research_response(
        path: Path, *, request_id: str, expected_name: str,
        technology: str, force: str | None,
    ) -> None:
        """Reject a guarded setter response that is not this exact successful call."""
        if path.name != expected_name:
            raise BridgeError(
                f"Research response {path.name!r} does not match "
                f"expected {expected_name!r}"
            )
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError, ValueError) as error:
            raise BridgeError(
                f"Research response {path.name!r} is not readable JSON: {error}"
            ) from error
        if not isinstance(payload, dict):
            raise BridgeError(
                f"Research response {path.name!r} is not a JSON object"
            )
        if payload.get("request_id") != request_id:
            raise BridgeError(
                f"Research response {path.name!r} does not match request_id "
                f"{request_id!r}"
            )
        if payload.get("ok") is not True:
            detail = payload.get("error") or "response did not report ok"
            raise BridgeError(
                f"Research command failed for request {request_id!r}: {detail}"
            )
        if payload.get("force") != force:
            raise BridgeError(
                f"Research response {path.name!r} has force="
                f"{payload.get('force')!r}, expected {force!r}"
            )
        if payload.get("technology") != technology:
            raise BridgeError(
                f"Research response {path.name!r} has technology="
                f"{payload.get('technology')!r}, expected {technology!r}"
            )
        canonical = payload.get("canonical_name")
        if not isinstance(canonical, str) or not canonical:
            raise BridgeError(
                f"Research response {path.name!r} has no canonical_name"
            )
        tick = payload.get("tick")
        if not isinstance(tick, int) or isinstance(tick, bool):
            raise BridgeError(
                f"Research response {path.name!r} has no integer tick"
            )

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

    def runtime_identity_probe(
        self, surface: str, force: str, timeout: float = 60.0,
    ) -> dict:
        """Collect one nonce-correlated, read-only runtime identity report.

        Every call generates a fresh UUID nonce, so the response filename is
        unique to this request and a previous report cannot be reused. The
        nonce is only a correlation token: it does not identify a save, a
        server process, a host, or a lane, and a successful probe authorizes
        no dispatch or game mutation. The response is read from the bridge's
        own ``script-output`` tree; this method cannot verify who owns that
        tree or whether the local server is the caller's intended target. It
        returns the validated in-memory report, not a path to reread later.
        """
        if not isinstance(surface, str) or not surface:
            raise ValueError("surface must be a non-empty string")
        if not isinstance(force, str) or not force:
            raise ValueError("force must be a non-empty string")
        nonce = uuid.uuid4().hex
        expected_name = f"runtime_identity_{nonce}.json"
        body = json.dumps(
            {"nonce": nonce, "surface": surface, "force": force},
            separators=(",", ":"),
        )

        script_output = getattr(self, "script_output", None)
        expected_path = None
        if script_output is not None:
            expected_path = (
                Path(script_output) / RUNTIME_IDENTITY_REPORT_SUBDIR / expected_name
            )
            # A fresh nonce cannot legitimately have a response yet. An
            # existing path is a collision or a stale/planted file, never this
            # request's output, so refuse it before issuing the command.
            if expected_path.exists() or expected_path.is_symlink():
                raise BridgeError(
                    "Runtime-identity response path already exists before the "
                    f"probe: {expected_path}"
                )

        try:
            collected = self._run_and_collect(
                f"/runtime_identity_probe {body}", RUNTIME_IDENTITY_REPORT_SUBDIR,
                timeout, expected_name=expected_name,
            )
        except BridgeError as error:
            # The generic wait accepts only parseable JSON, so a different
            # write can look like "no response". If this exact path exists,
            # let the response validator report why it is unusable.
            if ("Timed out" in str(error) and expected_path is not None
                    and (expected_path.exists() or expected_path.is_symlink())):
                return self._validate_runtime_identity_response(
                    expected_path, expected_name=expected_name, nonce=nonce,
                    surface=surface, force=force,
                )
            raise
        return self._validate_runtime_identity_response(
            collected, expected_name=expected_name, nonce=nonce,
            surface=surface, force=force,
        )

    @staticmethod
    def _validate_runtime_identity_response(
        path: Path, *, expected_name: str, nonce: str,
        surface: str, force: str,
    ) -> dict:
        """Return the exact decoded report after checking this probe's identity."""
        path = Path(path)
        if path.name != expected_name:
            raise BridgeError(
                f"Runtime-identity response {path.name!r} does not match "
                f"expected {expected_name!r}"
            )
        if path.is_symlink():
            raise BridgeError(
                f"Runtime-identity response {path.name!r} is a symbolic link; "
                "refusing to read it"
            )
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise BridgeError(
                f"Runtime-identity response {path.name!r} is not readable: {error}"
            ) from error
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError, ValueError) as error:
            raise BridgeError(
                f"Runtime-identity response {path.name!r} is not readable JSON: "
                f"{error}"
            ) from error
        if not isinstance(payload, dict):
            raise BridgeError(
                f"Runtime-identity response {path.name!r} is not a JSON object"
            )
        if payload.get("schema_version") != "1.0.0":
            raise BridgeError(
                f"Runtime-identity response {path.name!r} has unexpected "
                f"schema_version {payload.get('schema_version')!r}"
            )
        if payload.get("kind") != "runtime_identity_probe":
            raise BridgeError(
                f"Runtime-identity response {path.name!r} has unexpected kind "
                f"{payload.get('kind')!r}"
            )
        if payload.get("nonce") != nonce:
            raise BridgeError(
                f"Runtime-identity response {path.name!r} does not match nonce "
                f"{nonce!r}"
            )
        if payload.get("ok") is not True:
            detail = payload.get("error") or "response did not report ok"
            raise BridgeError(
                f"Runtime-identity probe failed for nonce {nonce!r}: {detail}"
            )
        tick = payload.get("tick")
        if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
            raise BridgeError(
                f"Runtime-identity response {path.name!r} has no non-negative "
                "integer tick"
            )
        for key, expected in (("surface", surface), ("force", force)):
            block = payload.get(key)
            if not isinstance(block, dict):
                raise BridgeError(
                    f"Runtime-identity response {path.name!r} has no {key} object"
                )
            if block.get("name") != expected:
                raise BridgeError(
                    f"Runtime-identity response {path.name!r} has {key}.name="
                    f"{block.get('name')!r}, expected {expected!r}"
                )
            index = block.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                raise BridgeError(
                    f"Runtime-identity response {path.name!r} has no non-negative "
                    f"integer {key}.index"
                )
        active_mods = payload.get("active_mods")
        if not isinstance(active_mods, dict) or not active_mods:
            raise BridgeError(
                f"Runtime-identity response {path.name!r} has no active_mods "
                "object"
            )
        for name, version in active_mods.items():
            if (not isinstance(name, str) or not name
                    or not isinstance(version, str) or not version):
                raise BridgeError(
                    f"Runtime-identity response {path.name!r} has malformed "
                    f"active_mods entry {name!r}: {version!r}"
                )
        count = payload.get("active_mods_count")
        if (not isinstance(count, int) or isinstance(count, bool)
                or count != len(active_mods)):
            raise BridgeError(
                f"Runtime-identity response {path.name!r} has "
                f"active_mods_count={count!r}, expected {len(active_mods)}"
            )
        for flag in ("ownership_claimed", "dispatch_authorized"):
            if payload.get(flag) is not False:
                raise BridgeError(
                    f"Runtime-identity response {path.name!r} must explicitly "
                    f"report {flag}=false"
                )
        return payload

    def optimizer_build_evidence(self, *, surface: str, force: str, episode_id: str,
                                 project_id: str, transaction_id: str, plan_hash: str,
                                 timeout: float = 60.0, include_unidentified: bool = False) -> dict:
        """Collect tagged events; unidentified events never establish current ownership."""
        from jsonschema import Draft7Validator

        for label, value in (("surface", surface), ("force", force), ("episode_id", episode_id),
                             ("project_id", project_id)):
            if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128:
                raise ValueError(f"{label} must be a non-empty string of at most 128 bytes")
        for label, value, length in (("transaction_id", transaction_id, 32), ("plan_hash", plan_hash, 64)):
            if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
                raise ValueError(f"{label} must be {length} lowercase hexadecimal characters")
        if type(include_unidentified) is not bool:
            raise ValueError("include_unidentified must be a boolean")
        request_id = uuid.uuid4().hex
        tags = {"optimizer_episode": episode_id, "optimizer_project": project_id,
                "optimizer_transaction": transaction_id, "optimizer_plan_hash": plan_hash}
        request = {"request_id": request_id, "surface": surface, "force": force, "tags": tags}
        _, report = self._collect_correlated_catalog(
            command=("/optimizer_build_evidence_v3 " if include_unidentified else "/optimizer_build_evidence ")
                    + json.dumps(request, separators=(",", ":")),
            subdir=OPTIMIZER_BUILD_EVIDENCE_SUBDIR,
            expected_name=f"optimizer_build_evidence_{request_id}.json", timeout=timeout,
            label="Optimizer build evidence",
        )
        try:
            schema = json.loads((Path(__file__).resolve().parent.parent / "schemas" /
                                 ("optimizer_build_evidence_v3.schema.json" if include_unidentified
                                  else "optimizer_build_evidence.schema.json")).read_text(encoding="utf-8"))
            valid = Draft7Validator(schema).is_valid(report)
        except (OSError, ValueError) as error:
            raise BridgeError("Optimizer build evidence schema is unavailable") from error
        if (not valid or report.get("ok") is not True
                or any(report.get(key) != value for key, value in request.items())
                or any(entity["tick"] > report["tick"] for entity in report["entities"])):
            raise BridgeError("Optimizer build evidence response failed correlation or validation")
        return report

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

    def research_lab_ownership(self, *, surface: str, force: str, episode_id: str,
                               packet_id: str, transaction_id: str, timeout: float = 60.0) -> dict:
        """Collect correlated robot-build evidence, not runtime ownership or authority."""
        for label, value in (("surface", surface), ("force", force),
                             ("episode_id", episode_id), ("packet_id", packet_id)):
            if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128:
                raise ValueError(f"{label} must be a non-empty string of at most 128 bytes")
        if not isinstance(transaction_id, str) or not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
            raise ValueError("transaction_id must be a 32-character lowercase hex identifier")
        request_id = uuid.uuid4().hex
        expected_name = f"research_lab_ownership_{request_id}.json"
        expected = self.script_output / RESEARCH_LAB_OWNERSHIP_SUBDIR / expected_name
        if expected.exists() or expected.is_symlink():
            raise BridgeError("Research lab ownership response path already exists")
        request = {"request_id": request_id, "surface": surface, "force": force,
                   "episode_id": episode_id, "packet_id": packet_id, "transaction_id": transaction_id}
        path = Path(self._run_and_collect(
            "/research_lab_ownership " + json.dumps(request, separators=(",", ":")),
            RESEARCH_LAB_OWNERSHIP_SUBDIR, timeout, expected_name=expected_name,
        ))
        if path != expected or path.is_symlink():
            raise BridgeError("Research lab ownership response path mismatch or symbolic link")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                report = json.load(handle)
        except (OSError, ValueError, UnicodeError) as error:
            raise BridgeError("Research lab ownership response is unreadable") from error
        if (not isinstance(report, dict) or report.get("ok") is not True
                or report.get("version") != "research-lab-ownership-v1"
                or report.get("source") != "robot_built_entity_tags"
                or report.get("ownership_claimed") is not False
                or report.get("dispatch_authorized") is not False
                or any(report.get(key) != value for key, value in request.items())
                or type(report.get("tick")) is not int or report["tick"] < 0
                or not isinstance(report.get("entities"), list)):
            raise BridgeError("Research lab ownership response failed correlation or validation")
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
