# Path: training/factorio_bridge.py
# Purpose: Bind Python training episodes to explicit loopback Factorio workers.

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Mapping

from jsonschema import Draft7Validator

from tools.rcon_client import RconClient
from planners.plan_validation import validate_build_plan
from training.contracts import validate_scenario
from training.isolation import assert_training_identity

_REPORT_SUBDIR = Path("factorio_training_lab") / "reports"
_LAYOUT_SUBDIR = Path("factorio_mod") / "layout_reports"
_COMMAND_VERSION = "1.0.0"


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
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "training_episode_report.schema.json"
        self._report_validator = Draft7Validator(json.loads(schema_path.read_text(encoding="utf-8")))

    def close(self) -> None:
        self._rcon.close()

    def handshake(self) -> None:
        for name in ("training_provision", "training_observe", "training_recycle", "build_layout_plan"):
            response = self._rcon.command(f"/help {name}")
            if "unknown command" in response.lower():
                raise TrainingBridgeError(f"required Factorio command is missing: {name}")

    def _files(self, subdir: Path) -> dict[Path, tuple[int, int]]:
        directory = self.script_output / subdir
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

    def _fresh_json(self, subdir: Path, known: Mapping[Path, tuple[int, int]]) -> list[dict]:
        reports = []
        for path, signature in self._files(subdir).items():
            if known.get(path) == signature:
                continue
            try:
                reports.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return reports

    def _wait_report(
        self, subdir: Path, known: Mapping[Path, tuple[int, int]], predicate,
        timeout: float | None = None,
    ) -> dict:
        deadline = time.monotonic() + (timeout or self._command_timeout)
        while time.monotonic() < deadline:
            matches = [report for report in self._fresh_json(subdir, known) if predicate(report)]
            if matches:
                return max(matches, key=lambda report: int(report.get("tick", -1)))
            time.sleep(self._poll_interval)
        raise TrainingBridgeError(f"timed out waiting for a matching report in {subdir}")

    def _training_command(
        self, name: str, episode_id: str, extra: Mapping | None = None,
        *, final_status: str | None = None, timeout: float | None = None,
    ) -> dict:
        request_id = uuid.uuid4().hex
        payload = {"version": _COMMAND_VERSION, "request_id": request_id, "episode_id": episode_id}
        payload.update(dict(extra or {}))
        known = self._files(_REPORT_SUBDIR)
        response = self._rcon.command(f"/{name} " + json.dumps(payload, separators=(",", ":")))
        if "error" in response.lower():
            raise TrainingBridgeError(response.strip())
        kind = name.removeprefix("training_")
        report = self._wait_report(
            _REPORT_SUBDIR, known,
            lambda item: item.get("request_id") == request_id
            and item.get("episode_id") == episode_id and item.get("kind") == kind
            and (final_status is None or item.get("status") == final_status),
            timeout,
        )
        errors = list(self._report_validator.iter_errors(report))
        if errors:
            raise TrainingBridgeError(f"invalid {kind} report: {errors[0].message}")
        if not report["ok"]:
            raise TrainingBridgeError(report.get("error", f"{kind} failed"))
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

    def execute(self, authorization: Mapping, plan: Mapping) -> dict:
        validate_build_plan(dict(plan))
        assert_training_identity(str(plan.get("surface", "")), str(plan.get("force", "")))
        known = self._files(_LAYOUT_SUBDIR)
        body = json.dumps({"authorization": dict(authorization), "build_plan": dict(plan)}, separators=(",", ":"))
        self._rcon.command("/build_layout_plan " + body)
        return self._wait_report(_LAYOUT_SUBDIR, known, lambda report: "ok" in report)

    def recycle(self, episode_id: str) -> dict:
        return self._training_command("training_recycle", episode_id, {
            "confirmation_token": "RECYCLE_TRAINING_EPISODE",
        }, final_status="completed")
