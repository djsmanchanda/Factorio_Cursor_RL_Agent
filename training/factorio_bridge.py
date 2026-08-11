# Path: training/factorio_bridge.py
# Purpose: Bind Python training episodes to explicit loopback Factorio workers.

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Mapping

from jsonschema import Draft7Validator

from planners.plan_validation import actions, validate_build_plan
from tools.rcon_client import RconClient
from training.contracts import validate_scenario
from training.isolation import assert_training_identity

_REPORT_SUBDIR = Path("factorio_training_lab") / "reports"
_COMMAND_VERSION = "1.0.0"
_UPLOAD_CHUNK_BYTES = 1_600
_MAX_UPLOAD_CHUNKS = 256
_REPORT_RETENTION = 512
_REPORT_KINDS = {
    "training_execute": "execution",
    "training_observe": "observation",
}


class TrainingBridgeError(RuntimeError):
    pass


class FactorioTrainingBridge:
    """One explicitly configured training worker; never discovers live servers."""

    def __init__(
        self, script_output: Path, *, host: str, port: int, password: str,
        poll_interval: float = 0.25, command_timeout: float = 120.0, rcon=None,
    ):
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("training bridge only permits loopback workers")
        self.script_output = Path(script_output)
        if not self.script_output.is_dir():
            raise TrainingBridgeError(f"script-output directory not found: {self.script_output}")
        self._rcon = rcon or RconClient(host, port, password, timeout=command_timeout)
        self._poll_interval = poll_interval
        self._command_timeout = command_timeout
        self._retain_reports = _REPORT_RETENTION
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "training_episode_report.schema.json"
        self._report_validator = Draft7Validator(json.loads(schema_path.read_text(encoding="utf-8")))

    def close(self) -> None:
        self._rcon.close()

    def handshake(self) -> None:
        for name in (
            "training_provision", "training_observe", "training_recycle",
            "training_upload", "training_execute",
        ):
            response = self._rcon.command(f"/help {name}")
            if "unknown command" in response.lower():
                raise TrainingBridgeError(f"required Factorio command is missing: {name}")

    def _files(self) -> dict[Path, tuple[int, int]]:
        directory = self.script_output / _REPORT_SUBDIR
        if not directory.is_dir():
            return {}
        result = {}
        for path in directory.glob("*.json"):
            try:
                stat = path.stat()
                result[path] = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                continue
        return result

    def _prune_reports(self, keep: Path | None = None) -> int:
        """Bound the shared training report directory to recent history.

        All logical slots share one script-output directory. Without retention,
        every bridge poll stats the entire lifetime of the batch and concurrent
        slots turn report collection into an I/O bottleneck.
        """
        directory = self.script_output / _REPORT_SUBDIR
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
            if keep is not None and item == keep:
                continue
            try:
                item.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def _wait_report(self, known: Mapping[Path, tuple[int, int]], predicate) -> tuple[dict, Path]:
        deadline = time.monotonic() + self._command_timeout
        while time.monotonic() < deadline:
            for path, signature in self._files().items():
                if known.get(path) == signature:
                    continue
                try:
                    report = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if predicate(report):
                    return report, path
            time.sleep(self._poll_interval)
        raise TrainingBridgeError("timed out waiting for a matching training report")

    def _validate_report(self, report: Mapping, kind: str) -> None:
        errors = list(self._report_validator.iter_errors(report))
        if errors:
            raise TrainingBridgeError(f"invalid {kind} report: {errors[0].message}")
        if report.get("kind") != kind:
            raise TrainingBridgeError(f"unexpected training report kind: {report.get('kind')}")
        if not report["ok"] and not (kind == "execution" and "execution" in report):
            raise TrainingBridgeError(report.get("error", f"{kind} failed"))

    def _training_command(
        self, name: str, episode_id: str, extra: Mapping | None = None,
        *, final_status: str | None = None,
    ) -> dict:
        request_id = uuid.uuid4().hex
        payload = {"version": _COMMAND_VERSION, "request_id": request_id, "episode_id": episode_id}
        payload.update(dict(extra or {}))
        self._prune_reports()
        known = self._files()
        response = self._rcon.command(f"/{name} " + json.dumps(payload, separators=(",", ":")))
        if "error" in response.lower():
            raise TrainingBridgeError(response.strip())
        kind = _REPORT_KINDS.get(name, name.removeprefix("training_"))
        report, report_path = self._wait_report(
            known,
            lambda item: item.get("request_id") == request_id
            and item.get("episode_id") == episode_id
            and (item.get("kind") != kind or final_status is None or item.get("status") == final_status),
        )
        self._prune_reports(report_path)
        self._validate_report(report, kind)
        return report

    def provision(self, episode_id: str, scenario: Mapping) -> dict:
        validate_scenario(scenario)
        environment = scenario["environment"]
        assert_training_identity(environment["surface_name"], environment["force_name"])
        return self._training_command("training_provision", episode_id, {
            "scenario_hash": scenario["scenario_hash"],
            "confirmation_token": "PROVISION_TRAINING_EPISODE", "scenario": dict(scenario),
        })

    def observe(self, episode_id: str) -> dict:
        return self._training_command("training_observe", episode_id)

    def _upload_plan(self, episode_id: str, package: Mapping) -> str:
        encoded = json.dumps(package, separators=(",", ":"), sort_keys=True)
        chunks = [encoded[index:index + _UPLOAD_CHUNK_BYTES]
                  for index in range(0, len(encoded), _UPLOAD_CHUNK_BYTES)]
        if not chunks:
            raise TrainingBridgeError("cannot upload an empty training plan")
        if len(chunks) > _MAX_UPLOAD_CHUNKS:
            raise TrainingBridgeError("training plan exceeds the upload chunk limit")
        upload_id = uuid.uuid4().hex
        for index, chunk in enumerate(chunks, start=1):
            payload = {
                "version": _COMMAND_VERSION, "request_id": uuid.uuid4().hex,
                "episode_id": episode_id, "upload_id": upload_id,
                "chunk_index": index, "chunk_count": len(chunks), "chunk": chunk,
            }
            response = self._rcon.command("/training_upload " + json.dumps(payload, separators=(",", ":")))
            if "error" in response.lower():
                raise TrainingBridgeError(response.strip())
        return upload_id

    def execute(self, episode_id: str, authorization: Mapping, plan: Mapping) -> dict:
        validate_build_plan(dict(plan))
        assert_training_identity(str(plan.get("surface", "")), str(plan.get("force", "")))
        if any(action["action_type"] != "place_entity" for action in actions(dict(plan))):
            raise TrainingBridgeError("training execution requires physical place_entity actions")
        upload_id = self._upload_plan(episode_id, {
            "authorization": dict(authorization), "build_plan": dict(plan),
        })
        return self._training_command("training_execute", episode_id, {
            "upload_id": upload_id, "confirmation_token": "EXECUTE_TRAINING_PLAN",
        })["execution"]

    def recycle(self, episode_id: str) -> dict:
        return self._training_command("training_recycle", episode_id, {
            "confirmation_token": "RECYCLE_TRAINING_EPISODE",
        }, final_status="completed")