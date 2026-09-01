# Path: helper_agent/review_service.py
# Purpose: Validate packets, obtain schema-gated reviews, render reports, and persist casebook data.

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, Mapping
from urllib.parse import urlsplit, urlunsplit

import jsonschema

from helper_agent import casebook, config

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SIDEcar_ROOT = _REPO_ROOT / "Helper_Agent"
_CASE_PACKET_SCHEMA = json.loads((_SIDEcar_ROOT / "schemas" / "case_packet.schema.json").read_text("utf-8"))
_REVIEW_REPORT_SCHEMA = json.loads((_SIDEcar_ROOT / "schemas" / "review_report.schema.json").read_text("utf-8"))
_PROMPT_ROOT = _SIDEcar_ROOT / "config" / "prompts"
_SYSTEM_PROMPT = "\n\n".join(
    path.read_text("utf-8").strip()
    for path in (
        _PROMPT_ROOT / "review_system_prompt.md",
        _PROMPT_ROOT / "observation_rubric.md",
    )
)
_RUN_TEMPLATE = (_SIDEcar_ROOT / "templates" / "run_review.md").read_text("utf-8")
_MOMENT_TEMPLATE = (_SIDEcar_ROOT / "templates" / "notable_moment.md").read_text("utf-8")
_NOW = lambda: datetime.now().astimezone().isoformat(timespec="seconds")
_SLUG = re.compile(r"[^A-Za-z0-9_.-]+")
# A 16K local context needs room for the system rubric and structured response;
# helper packets use this smaller cap rather than the generic global ceiling.
_MAX_MODEL_PROMPT_CHARS = 36_000
_MAX_MODEL_OUTPUT_TOKENS = 1_536


class ModelUnavailable(RuntimeError):
    """The local review model could not be made ready for this packet."""


class ReviewService:
    def __init__(
        self, data_root: Path, *, model_endpoint: str = "", model_name: str = "local-lite",
        restart_command: tuple[str, ...] = (), restart_ready_timeout_seconds: int = 180,
        restart_cooldown_seconds: int = 600,
    ):
        self.data_root = data_root
        self.directories = config.ensure_runtime(data_root)
        self.model_endpoint = model_endpoint
        self.model_name = model_name
        self.model_timeout_seconds = 60
        self.restart_command = restart_command
        self.restart_ready_timeout_seconds = restart_ready_timeout_seconds
        self.restart_cooldown_seconds = restart_cooldown_seconds

    def process_inbox(self) -> list[tuple[str, str]]:
        with _processor_lock(self.directories["state"] / "processor.lock"):
            results: list[tuple[str, str]] = []
            for path in sorted(self.directories["inbox"].glob("*.json")):
                try:
                    report_path = self.process_packet(path)
                    results.append((path.name, str(report_path)))
                except ModelUnavailable as error:
                    self._append_ledger({
                        "event": "model_review_deferred", "packet": str(path),
                        "model": self.model_name, "error": str(error),
                        "created_at": _NOW(),
                    })
                    break
                except (OSError, json.JSONDecodeError, jsonschema.ValidationError) as error:
                    self._append_ledger({
                        "event": "packet_rejected", "packet": str(path),
                        "error": str(error), "created_at": _NOW(),
                    })
            return results

    def process_packet(self, path: Path) -> Path:
        packet = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.validate(packet, _CASE_PACKET_SCHEMA)
        related = self._related_skills(packet)
        processed = self.directories["processed"] / path.name
        model_payload = None
        if self.model_endpoint:
            model_payload = self._call_model(packet, related)
        if isinstance(model_payload, dict):
            report = model_payload
            report["packet_path"] = str(processed)
            try:
                jsonschema.validate(report, _REVIEW_REPORT_SCHEMA)
                if report.get("run_id") != packet.get("run_id"):
                    raise jsonschema.ValidationError(
                        "Model report run_id does not match the case packet"
                    )
            except jsonschema.ValidationError as error:
                self._append_ledger({
                    "event": "model_review_invalid", "model": self.model_name,
                    "packet_hash": self._hash(packet), "error": str(error),
                    "fallback": True, "created_at": _NOW(),
                })
                report = self._fallback_report(packet, related)
        else:
            if model_payload is not None:
                self._append_ledger({
                    "event": "model_review_invalid", "model": self.model_name,
                    "packet_hash": self._hash(packet),
                    "error": "Model response must be a JSON object",
                    "fallback": True, "created_at": _NOW(),
                })
            report = self._fallback_report(packet, related)
        report["packet_path"] = str(processed)
        jsonschema.validate(report, _REVIEW_REPORT_SCHEMA)
        return self._persist_review(packet, report, path, processed)

    def _call_model(self, packet: Mapping[str, object], related: list[str]) -> dict | None:
        prompt = json.dumps({
            "case_packet": packet,
            "relevant_casebook_skills": related,
            "review_report_schema": _REVIEW_REPORT_SCHEMA,
        }, indent=2)
        prompt_chars = len(_SYSTEM_PROMPT) + len(prompt)
        if prompt_chars > _MAX_MODEL_PROMPT_CHARS:
            self._append_ledger({
                "event": "model_input_too_large", "model": self.model_name,
                "packet_hash": self._hash(packet), "prompt_chars": prompt_chars,
                "limit_chars": _MAX_MODEL_PROMPT_CHARS, "fallback": True,
                "created_at": _NOW(),
            })
            return None
        request = urllib.request.Request(
            self.model_endpoint,
            data=json.dumps({
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": _MAX_MODEL_OUTPUT_TOKENS,
                "chat_template_kwargs": {"enable_thinking": False},
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        self._append_ledger({
            "event": "model_request_started", "model": self.model_name,
            "packet_hash": self._hash(packet), "created_at": _NOW(),
        })
        try:
            return self._request_model(request)
        except (
            OSError, ValueError, KeyError, IndexError,
            urllib.error.URLError, json.JSONDecodeError,
        ) as error:
            if not self._endpoint_unavailable(error):
                self._record_model_failure(packet, error)
                return None
            if self._restart_and_wait():
                try:
                    return self._request_model(request)
                except (
                    OSError, ValueError, KeyError, IndexError,
                    urllib.error.URLError, json.JSONDecodeError,
                ) as retry_error:
                    error = retry_error
            detail = self._record_model_failure(packet, error)
            if not self._endpoint_unavailable(error):
                return None
            raise ModelUnavailable(detail) from error

    @staticmethod
    def _endpoint_unavailable(error: BaseException) -> bool:
        if isinstance(error, urllib.error.HTTPError):
            return error.code in {502, 503, 504}
        return isinstance(error, (OSError, urllib.error.URLError))

    def _record_model_failure(
        self, packet: Mapping[str, object], error: BaseException,
    ) -> str:
        detail = f"{type(error).__name__}: {error}"
        if isinstance(error, urllib.error.HTTPError):
            try:
                body = error.read(2048).decode("utf-8", errors="replace")
            except OSError:
                body = ""
            if body:
                detail = f"{detail}; response={body}"
        self._append_ledger({
            "event": "model_review_failed", "model": self.model_name,
            "packet_hash": self._hash(packet), "error": detail,
            "created_at": _NOW(),
        })
        return detail

    def _request_model(self, request: urllib.request.Request) -> dict:
        with urllib.request.urlopen(request, timeout=self._timeout()) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        return json.loads(self._json_payload(content))

    def _restart_and_wait(self) -> bool:
        """Start the local inference server once, then wait only in this worker."""
        if self._model_ready():
            return True
        if not self.restart_command or not self._restart_allowed():
            return False
        command = tuple(os.path.expanduser(part) for part in self.restart_command)
        self._append_ledger({
            "event": "model_restart_started", "model": self.model_name,
            "command": command[0], "created_at": _NOW(),
        })
        try:
            self._launch_model(command)
        except (OSError, subprocess.SubprocessError) as error:
            self._append_ledger({
                "event": "model_restart_failed", "model": self.model_name,
                "error": f"{type(error).__name__}: {error}", "created_at": _NOW(),
            })
            return False
        deadline = time.monotonic() + self.restart_ready_timeout_seconds
        while time.monotonic() < deadline:
            if self._model_ready():
                self._append_ledger({
                    "event": "model_restart_ready", "model": self.model_name,
                    "created_at": _NOW(),
                })
                return True
            time.sleep(2)
        self._append_ledger({
            "event": "model_restart_timeout", "model": self.model_name,
            "timeout_seconds": self.restart_ready_timeout_seconds, "created_at": _NOW(),
        })
        return False

    def _restart_allowed(self) -> bool:
        path = self.directories["state"] / "model-restart.json"
        now = time.time()
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
            previous = float(prior.get("started_at", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            previous = 0
        if now - previous < self.restart_cooldown_seconds:
            self._append_ledger({
                "event": "model_restart_cooldown", "model": self.model_name,
                "remaining_seconds": int(self.restart_cooldown_seconds - (now - previous)),
                "created_at": _NOW(),
            })
            return False
        path.write_text(json.dumps({"started_at": now}) + "\n", encoding="utf-8")
        return True

    def _launch_model(self, command: tuple[str, ...]) -> None:
        output_path = self.directories["state"] / "freetoken-supervisor.log"
        if os.environ.get("INVOCATION_ID"):
            unit = f"factorio-rl-freetoken-{os.getpid()}-{time.time_ns()}.service"
            subprocess.run([
                "systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}",
                f"--working-directory={Path.home()}",
                f"--property=StandardOutput=append:{output_path}",
                f"--property=StandardError=append:{output_path}", *command,
            ], check=True)
            return
        with output_path.open("ab") as output:
            subprocess.Popen(
                command, cwd=Path.home(), stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
            )

    def _model_ready(self) -> bool:
        parts = urlsplit(self.model_endpoint)
        health = urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))
        try:
            with urllib.request.urlopen(health, timeout=5) as response:
                return 200 <= response.status < 300
        except (OSError, urllib.error.URLError):
            return False

    def _timeout(self) -> int:
        return self.model_timeout_seconds

    def _json_payload(self, content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        return stripped

    def _fallback_report(self, packet: Mapping[str, object], related: list[str]) -> dict:
        run_id = str(packet["run_id"])
        blockers = [item for item in packet.get("blockers", []) if isinstance(item, Mapping)]
        terminal = str(packet.get("terminal_class", "no_run_end"))
        moments: list[dict] = []
        telemetry = packet.get("telemetry", {})
        decision = telemetry.get("decision_summary", {}) if isinstance(telemetry, Mapping) else {}
        for blocker in blockers[:8]:
            iteration_cycle = (
                blocker.get("code") == "controller_iteration_limit"
                and isinstance(decision, Mapping)
                and decision.get("alternating_priority_cycle") is True
            )
            loan_shortage = blocker.get("code") == "rationed_mall_no_borrower"
            details = blocker.get("details", {})
            if not isinstance(details, Mapping):
                details = {}
            active_loans = details.get("active_loans", [])
            loan_handoff_blocked = (
                loan_shortage and isinstance(active_loans, list)
                and bool(active_loans)
            )
            moments.append({
                "title": (
                    "Alternating priority livelock"
                    if iteration_cycle else
                    "Bootstrap mall loan handoff failed"
                    if loan_handoff_blocked else
                    "No eligible rationed-mall borrower"
                    if loan_shortage else
                    f"{blocker.get('code', 'unknown')} blocker"
                ),
                "run_id": run_id,
                "time_tick": str(blocker.get("observed_at", "unknown")),
                "what_happened": f"Typed blocker {blocker.get('code', 'unknown')}: {blocker.get('message', '')}",
                "what_was_expected": "The deterministic mission would progress without this blocking state.",
                "cause": (
                    "Two tasks alternated without changing the outstanding-work state, "
                    "so task identity kept the old no-progress detector from accumulating."
                    if iteration_cycle else
                    "A different recipe loan remained active when the next finite batch "
                    "was requested; the controller treated a serial handoff as no capacity."
                    if loan_handoff_blocked else
                    "No active loan remained and the planner found no eligible owned "
                    "assembler/requester cell for the finite batch."
                    if loan_shortage else
                    "The runner recorded this typed blocker; the bounded packet does not establish a deeper cause."
                ),
                "fix_direction": (
                    "Category: controller livelock; compare outstanding work independently "
                    "of the selected task and report the repeating cycle."
                    if iteration_cycle else
                    "Category: bootstrap loan lifecycle; complete or restore the active "
                    "loan, then defer and retry the new batch instead of terminating."
                    if loan_handoff_blocked else
                    "Category: bootstrap capacity; report candidate rejection reasons and "
                    "schedule a core mall cell or another eligible borrower."
                    if loan_shortage else
                    "Category: planner telemetry; typed stage context needs focused review."
                ),
                "confidence": "high" if blocker.get("classification") == "bug" else "medium",
                "evidence": f"{blocker.get('observed_at', '')} {blocker.get('message', '')}".strip(),
                "classification": blocker.get("classification", "bug"),
                "review_status": "unreviewed",
            })
        if not moments:
            excerpt = packet.get("log_excerpt", {})
            if not isinstance(excerpt, Mapping):
                excerpt = {}
            evidence_lines = excerpt.get("tail_lines") or excerpt.get("head_lines") or []
            evidence = "; ".join(str(line) for line in evidence_lines[-3:])
            if not evidence:
                evidence = f"No typed blocker; source log: {packet.get('source_log', 'unknown')}"
            moments.append({
                "title": "Terminal state observed",
                "run_id": run_id,
                "time_tick": str(packet.get("end_tick") or packet.get("duration_seconds")),
                "what_happened": f"The run ended with terminal_class={terminal}.",
                "what_was_expected": "The intended mission outcome is documented before action is taken.",
                "cause": "No typed blocker is present in the bounded packet; deeper cause is not established.",
                "fix_direction": "Category: terminal-transition telemetry; structured evidence is missing.",
                "confidence": "low" if terminal == "completed" else "medium",
                "evidence": evidence,
                "classification": "unclear",
                "review_status": "unreviewed",
            })
        missing = []
        if packet.get("start_tick") is None or packet.get("end_tick") is None:
            missing.append("Structured start and end game ticks are unavailable.")
        mission = telemetry.get("mission", {}) if isinstance(telemetry, Mapping) else {}
        controllers = mission.get("controllers", []) if isinstance(mission, Mapping) else []
        if not isinstance(controllers, list):
            controllers = []
        failed_workflows = [
            f"controller {controller.get('target', 'unknown')} finished {controller.get('status', 'unknown')}"
            for controller in controllers
            if isinstance(controller, Mapping) and controller.get("status") == "failed"
        ]
        return {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": _NOW(),
            "model": "deterministic-fallback",
            "status": "fallback",
            "terminal_outcome": terminal,
            "mission_stage": str(packet.get("mission_stage", "unknown")),
            "timeline_summary": "; ".join(packet.get("log_excerpt", {}).get("matched_pattern_lines", [])[-5:]),
            "notable_moments": moments,
            "successful_workflows": [
                f"controller {controller.get('target', 'unknown')} completed"
                for controller in controllers
                if isinstance(controller, Mapping) and controller.get("status") == "completed"
            ],
            "failed_workflows": failed_workflows,
            "suspected_root_causes": [
                (
                    "alternating priorities masked unchanged outstanding work"
                    if item.get("code") == "controller_iteration_limit"
                    and isinstance(decision, Mapping)
                    and decision.get("alternating_priority_cycle") is True
                    else "rationed mall exhausted eligible borrower capacity"
                    if item.get("code") == "rationed_mall_no_borrower"
                    else f"typed blocker: {item.get('code', 'unknown')}"
                )
                for item in blockers
            ],
            "missing_observations": missing or ["No additional missing observation is required by the fallback reviewer."],
            "recommended_next_probe": "Structured tick telemetry for the terminal transition and blockers.",
            "relevant_casebook_skills": related,
            "confidence": "medium" if blockers else "low",
            "uncertainty": "This is a deterministic fallback summary, not a local-model diagnosis.",
            "review_status": "unreviewed",
            "packet_path": "",
        }

    def _related_skills(self, packet: Mapping[str, object]) -> list[str]:
        run_id = str(packet.get("run_id", ""))
        codes = {str(item.get("code", "")) for item in packet.get("blockers", []) if isinstance(item, Mapping)}
        matches: list[str] = []
        for skill in casebook.list_skills(self.directories["skills"].parent):
            source_runs = [str(item) for item in skill.get("source_runs", [])]
            tags = {str(item) for item in skill.get("tags", [])}
            if run_id in source_runs or tags & codes:
                matches.append(str(skill.get("id", "")))
        return matches

    def _persist_review(
        self, packet: Mapping[str, object], report: dict,
        packet_path: Path, processed: Path,
    ) -> Path:
        safe_run_id = _SLUG.sub("_", str(packet["run_id"]))
        report_path = self.directories["reports"] / f"{safe_run_id}.json"
        casebook.write_incident(self.directories["skills"].parent, packet, report)
        for moment in report.get("notable_moments", []):
            if isinstance(moment, Mapping):
                skill_path = casebook.upsert_skill(
                    self.directories["skills"].parent, moment, packet,
                )
                if skill_path is not None:
                    skill_id = skill_path.stem
                    if skill_id not in report["relevant_casebook_skills"]:
                        report["relevant_casebook_skills"].append(skill_id)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (self.directories["reports"] / f"{safe_run_id}.md").write_text(
            render_report(report), encoding="utf-8",
        )
        packet_path.replace(processed)
        self._append_ledger({
            "event": "review_written", "run_id": packet["run_id"],
            "packet_hash": self._hash(packet), "report_hash": self._hash(report),
            "status": report.get("status"), "created_at": _NOW(),
        })
        return report_path

    def _append_ledger(self, entry: Mapping[str, object]) -> None:
        path = self.directories["state"] / "ledger.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")

    @staticmethod
    def _hash(value: Mapping[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@contextmanager
def _processor_lock(path: Path) -> Iterator[None]:
    """Serialize detached processors without leaving stale lock ownership."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        _lock_file(handle)
        try:
            yield
        finally:
            _unlock_file(handle)


def _lock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def render_report(report: Mapping[str, object]) -> str:
    moment_blocks = [
        _MOMENT_TEMPLATE
        .replace("{{title}}", str(moment.get("title", "")))
        .replace("{{run_id}}", str(moment.get("run_id", "")))
        .replace("{{time_tick}}", str(moment.get("time_tick", "")))
        .replace("{{what_happened}}", str(moment.get("what_happened", "")))
        .replace("{{what_was_expected}}", str(moment.get("what_was_expected", "")))
        .replace("{{cause}}", str(moment.get("cause", "")))
        .replace("{{fix_direction}}", str(moment.get("fix_direction", "")))
        .replace("{{confidence}}", str(moment.get("confidence", "")))
        .replace("{{evidence}}", str(moment.get("evidence", "")))
        .replace("{{classification}}", str(moment.get("classification", "")))
        .replace("{{review_status}}", str(moment.get("review_status", "")))
        for moment in report.get("notable_moments", []) if isinstance(moment, Mapping)
    ]
    replacements = {
        "run_id": str(report.get("run_id", "")),
        "terminal_outcome": str(report.get("terminal_outcome", "")),
        "mission_stage": str(report.get("mission_stage", "")),
        "confidence": str(report.get("confidence", "")),
        "review_status": str(report.get("review_status", "")),
        "timeline_summary": str(report.get("timeline_summary", "")),
        "notable_moments": "\n\n".join(moment_blocks),
        "successful_workflows": "\n".join(f"- {item}" for item in report.get("successful_workflows", [])),
        "failed_workflows": "\n".join(f"- {item}" for item in report.get("failed_workflows", [])),
        "suspected_root_causes": "\n".join(f"- {item}" for item in report.get("suspected_root_causes", [])),
        "missing_observations": "\n".join(f"- {item}" for item in report.get("missing_observations", [])),
        "recommended_next_probe": str(report.get("recommended_next_probe", "")),
        "relevant_casebook_skills": "\n".join(f"- {item}" for item in report.get("relevant_casebook_skills", [])),
        "uncertainty": str(report.get("uncertainty", "")),
    }
    rendered = _RUN_TEMPLATE
    for key, value in replacements.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered
