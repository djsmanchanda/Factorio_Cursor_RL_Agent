# Path: tests/test_helper_agent.py
# Purpose: Verify packet extraction, fallback review, feedback isolation, and brief generation.

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from helper_agent import cli, config, dashboard, feedback
from helper_agent.brief import generate_brief
from helper_agent.packet_builder import _decision_summary, build_case_packet, write_packet
from helper_agent.review_service import ReviewService
from tools import autonomous_run


def _write_run(log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "2026-08-28T10:00:00+05:30 RUN START: command=research target=mining-productivity-4 "
        "surface=nauvis force=player bootstrap_profile=reduced-v1 log=/tmp/run.log\n"
        "2026-08-28T10:00:05+05:30 MISSION STATE: profile=reduced-v1 ledger=/tmp/mission\n"
        "2026-08-28T10:00:10+05:30 STUCK: construction supply shortage\n"
        "2026-08-28T10:00:15+05:30 RUN END\n",
        encoding="utf-8",
    )


def _packet(tmp_path: Path) -> dict:
    log = tmp_path / "autonomous-run.log"
    mission = tmp_path / "mission.json"
    blockers = tmp_path / "blockers.jsonl"
    manifest = tmp_path / "manifest.json"
    _write_run(log)
    mission.write_text(json.dumps({
        "mission_id": "episode-test", "attempt": 1, "status": "stuck",
        "stage": "construction_supply_shortage", "current_target": "iron-plate",
        "started_at": "2026-08-28T10:00:00+05:30",
        "ended_at": "2026-08-28T10:00:15+05:30", "controllers": [],
    }), encoding="utf-8")
    blocker = {
        "blocker_id": "blocker-0001", "mission_id": "episode-test", "attempt": 1,
        "observed_at": "2026-08-28T10:00:10+05:30", "code": "construction_supply_shortage",
        "classification": "intended_difficulty", "state": "supply_wait",
        "stage": "construction_supply_shortage", "target": "iron-plate",
        "message": "iron-plate requires 10 more", "details": {"missing": {"iron-plate": 10}},
    }
    unrelated = {
        **blocker,
        "blocker_id": "blocker-old",
        "mission_id": "other-mission",
        "attempt": 7,
        "code": "unrelated_failure",
        "message": "must not leak into this run",
    }
    blockers.write_text(
        json.dumps(unrelated) + "\n" + json.dumps(blocker) + "\n",
        encoding="utf-8",
    )
    manifest.write_text(json.dumps({
        "episode_id": "episode-test", "isolated_save_sha256": "a" * 64,
        "deployed_factorio_mod_sha256": "b" * 64, "repository_revision": "c" * 40,
        "initial_game_tick": 1000,
    }), encoding="utf-8")
    return build_case_packet(
        log_path=log,
        mission_state_path=mission,
        blocker_events_path=blockers,
        episode_manifest_path=manifest,
    )


def test_packet_builder_extracts_bounded_evidence_and_typed_blocker(tmp_path: Path) -> None:
    packet = _packet(tmp_path)

    assert packet["run_id"] == "episode-test-attempt-001"
    assert packet["terminal_class"] == "stuck"
    assert packet["start_tick"] == 1000
    assert packet["duration_seconds"] == 15
    assert len(packet["blockers"]) == 1
    assert packet["blockers"][0]["code"] == "construction_supply_shortage"
    assert "RUN START: command=research" in packet["log_excerpt"]["head_lines"][0]
    assert "RUN END" in packet["log_excerpt"]["head_lines"][-1]
    assert any("STUCK:" in line for line in packet["log_excerpt"]["matched_pattern_lines"])


def test_run_logger_writes_compact_structured_events(tmp_path: Path) -> None:
    logger = autonomous_run._RunLogger(tmp_path / "autonomous-run.log")
    logger.emit("PRIORITY: steel-plate rating=45/100")
    logger.close()

    event = json.loads(
        (tmp_path / "deterministic-events.jsonl").read_text(encoding="utf-8")
    )
    assert event["event_type"] == "priority"
    assert event["sequence"] == 1
    assert event["message"].startswith("PRIORITY: steel-plate")


def test_decision_summary_retains_recent_mall_loan_transitions() -> None:
    summary = _decision_summary([
        {"message": "  MALL BOOTSTRAP LOAN: borrowed cable for splitter"},
        {"message": "  MALL BOOTSTRAP LOAN HANDOFF: drills wait for splitter"},
        {"message": "  MALL BOOTSTRAP LOAN RESTORED: copper-cable"},
    ])

    assert summary["mall_loan_tail"] == [
        "  MALL BOOTSTRAP LOAN: borrowed cable for splitter",
        "  MALL BOOTSTRAP LOAN HANDOFF: drills wait for splitter",
        "  MALL BOOTSTRAP LOAN RESTORED: copper-cable",
    ]


def test_fallback_explains_an_active_loan_handoff_blocker(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    packet["blockers"] = [{
        **packet["blockers"][0],
        "code": "rationed_mall_no_borrower",
        "details": {
            "item": "electric-mining-drill",
            "target": 6,
            "available_stock": 5,
            "active_loans": [{"target_item": "splitter"}],
        },
    }]

    report = ReviewService(tmp_path)._fallback_report(packet, [])

    assert report["notable_moments"][0]["title"] == (
        "Bootstrap mall loan handoff failed"
    )
    assert "serial handoff" in report["notable_moments"][0]["cause"]


def test_packet_builder_preserves_unprefixed_traceback_frames(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    log = Path(packet["source_log"])
    log.write_text(
        "2026-08-28T10:00:00+05:30 RUN START: command=research "
        "target=mining-productivity-4 surface=nauvis force=player "
        "bootstrap_profile=reduced-v1 log=/tmp/run.log\n"
        "2026-08-28T10:00:10+05:30 ERROR: TypeError: bad origin\n"
        "Traceback (most recent call last):\n"
        "  File \"orchestrator/autonomous_builder.py\", line 291, in provision\n"
        "    mine_origin[0]\n"
        "TypeError: 'NoneType' object is not subscriptable\n"
        "2026-08-28T10:00:15+05:30 RUN END\n",
        encoding="utf-8",
    )

    rebuilt = build_case_packet(log_path=log)

    assert any(
        "autonomous_builder.py" in line
        for line in rebuilt["log_excerpt"]["tail_lines"]
        or rebuilt["log_excerpt"]["head_lines"]
    )
    assert any(
        "TypeError: 'NoneType'" in line
        for line in rebuilt["log_excerpt"]["matched_pattern_lines"]
    )


def test_fallback_review_schema_valid_and_silence_remains_unreviewed(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    inbox = tmp_path / "inbox"
    path = write_packet(packet, inbox)
    service = ReviewService(tmp_path)
    report_path = service.process_packet(path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["status"] == "fallback"
    assert report["review_status"] == "unreviewed"
    assert report["notable_moments"][0]["classification"] == "intended_difficulty"
    assert report["notable_moments"][0]["fix_direction"].startswith("Category:")
    assert report["packet_path"].endswith(path.name)
    assert (tmp_path / "processed" / path.name).is_file()
    assert (report_path.with_suffix(".md")).exists()
    assert "## Notable Moment:" in (report_path.with_suffix(".md")).read_text(encoding="utf-8")


def test_invalid_model_report_falls_back_instead_of_rejecting_packet(tmp_path: Path) -> None:
    class InvalidModelReviewService(ReviewService):
        def _call_model(self, packet, related):
            return {"schema_version": 1, "run_id": packet["run_id"]}

    packet = _packet(tmp_path)
    path = write_packet(packet, tmp_path / "inbox")
    service = InvalidModelReviewService(tmp_path, model_endpoint="http://model.invalid")

    report_path = service.process_packet(path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    ledger = (tmp_path / "state" / "ledger.jsonl").read_text(encoding="utf-8")

    assert report["status"] == "fallback"
    assert "model_review_invalid" in ledger


def test_model_request_supplies_review_schema_and_bounds_output(
    tmp_path: Path, monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "{}"}}],
            }).encode()

    def fake_urlopen(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("helper_agent.review_service.urllib.request.urlopen", fake_urlopen)
    service = ReviewService(
        tmp_path, model_endpoint="http://model.invalid", model_name="test-model",
    )

    assert service._call_model({"run_id": "run-1"}, []) == {}
    body = json.loads(captured["request"].data)
    prompt = json.loads(body["messages"][1]["content"])

    assert prompt["review_report_schema"]["title"] == "HelperAgentReviewReport"
    assert body["max_tokens"] == 4096
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "response_format" not in body
    system_prompt = body["messages"][0]["content"]
    normalized_prompt = " ".join(system_prompt.split())
    assert "Review exactly one bounded case packet" in normalized_prompt
    assert "Apply this order to the supplied packet only" in normalized_prompt
    assert "Never call tools, schedule work, retry" in normalized_prompt
    assert "Co-occurrence and sequence alone do not prove causality" in normalized_prompt
    assert "Start every `fix_direction` with `Category:`" in normalized_prompt
    assert "Provide seed stock or adjust the bootstrap profile" in normalized_prompt


def test_short_completed_run_without_blockers_has_valid_fallback_evidence(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    packet["terminal_class"] = "completed"
    packet["blockers"] = []
    packet["telemetry"]["mission"]["controllers"] = None
    path = write_packet(packet, tmp_path / "inbox")

    report_path = ReviewService(tmp_path).process_packet(path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["status"] == "fallback"
    assert report["notable_moments"][0]["evidence"]


def test_process_inbox_uses_a_runtime_lock(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    write_packet(packet, tmp_path / "inbox")

    results = ReviewService(tmp_path).process_inbox()

    assert len(results) == 1
    assert (tmp_path / "state" / "processor.lock").is_file()


def test_detached_processor_uses_current_python_and_runtime_log(
    tmp_path: Path, monkeypatch,
) -> None:
    launched: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched.update(kwargs)
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.delenv("INVOCATION_ID", raising=False)

    assert cli.launch_processor(tmp_path) == "pid=4321"
    assert launched["command"] == [
        cli.sys.executable, "-m", "helper_agent.cli",
        "--data-root", str(tmp_path), "process",
    ]
    assert launched["cwd"] == config.REPO_ROOT
    assert launched["stdin"] is cli.subprocess.DEVNULL
    assert launched["stderr"] is cli.subprocess.STDOUT
    assert (tmp_path / "state" / "processor.log").is_file()


def test_systemd_runner_launches_processor_in_an_independent_unit(
    tmp_path: Path, monkeypatch,
) -> None:
    launched: dict[str, object] = {}

    def fake_run(command, **kwargs):
        launched["command"] = command
        launched.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("INVOCATION_ID", "runner-service")
    monkeypatch.setattr(cli.os, "getpid", lambda: 321)
    monkeypatch.setattr(cli.time, "time_ns", lambda: 654)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    identity = cli.launch_processor(tmp_path)

    assert identity == "unit=factorio-rl-helper-agent-321-654.service"
    assert launched["check"] is True
    assert launched["command"][:6] == [
        "systemd-run", "--user", "--quiet", "--collect",
        "--unit=factorio-rl-helper-agent-321-654.service",
        f"--working-directory={config.REPO_ROOT}",
    ]
    assert launched["command"][-6:] == [
        cli.sys.executable, "-m", "helper_agent.cli",
        "--data-root", str(tmp_path), "process",
    ]


def test_runner_queues_packet_and_launches_processor(tmp_path: Path, monkeypatch) -> None:
    packet = {"run_id": "run-1"}
    queued = tmp_path / "inbox" / "run-1.json"
    launched: list[Path] = []
    messages: list[str] = []
    monkeypatch.setattr(autonomous_run, "build_case_packet", lambda **_kwargs: packet)
    monkeypatch.setattr(
        autonomous_run, "write_packet", lambda value, inbox: queued,
    )
    monkeypatch.setattr(
        autonomous_run, "launch_helper_agent_processor",
        lambda data_root: launched.append(data_root) or "pid=9876",
    )

    result = autonomous_run._queue_helper_agent_review(
        log_path=tmp_path / "run.log",
        mission_state_path=tmp_path / "mission.json",
        blocker_events_path=tmp_path / "blockers.jsonl",
        episode_manifest_path=None,
        emit=messages.append,
        data_root=tmp_path,
    )

    assert result == queued
    assert launched == [tmp_path]
    assert messages == [
        f"HELPER AGENT: queued post-run review packet {queued}",
        "HELPER AGENT: started post-run processor pid=9876",
    ]


def test_feedback_is_append_only_and_can_promote_skill(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    path = write_packet(packet, tmp_path / "inbox")
    ReviewService(tmp_path).process_packet(path)
    report_path = tmp_path / "reports" / "episode-test-attempt-001.json"
    before = report_path.read_bytes()
    skill_path = tmp_path / "casebook" / "skills" / "blocker-construction-supply-shortage.md"
    assert skill_path.is_file()

    feedback_path, changed = feedback.record_feedback(tmp_path, {
        "run_id": "episode-test-attempt-001",
        "verdict": "confirmed",
        "missed_issues": [],
        "corrections": [],
        "extra_observations": ["Check tick timestamps next run."],
        "skill_actions": [{"action": "promote", "skill_id": "blocker-construction-supply-shortage"}],
        "comment": "Matches the observed supply wait.",
    })

    assert changed == ["blocker-construction-supply-shortage"]
    assert feedback_path.parent == tmp_path / "casebook" / "feedback"
    assert report_path.read_bytes() == before
    assert "status: confirmed" in skill_path.read_text(encoding="utf-8")
    assert dashboard.helper_view(tmp_path)["latest_feedback"]["verdict"] == "confirmed"
    assert dashboard.helper_view(tmp_path)["review_status"] == "confirmed"


def test_feedback_submissions_never_overwrite_each_other(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    path = write_packet(packet, tmp_path / "inbox")
    ReviewService(tmp_path).process_packet(path)
    payload = {
        "run_id": "episode-test-attempt-001", "verdict": "partial",
        "missed_issues": [], "corrections": [], "extra_observations": [],
        "skill_actions": [], "comment": "First observation.",
    }

    first, _ = feedback.record_feedback(tmp_path, payload)
    second, _ = feedback.record_feedback(tmp_path, payload)

    assert first != second
    assert len(list((tmp_path / "casebook" / "feedback").glob("*.json"))) == 2


def test_brief_pulls_report_feedback_and_skills(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    path = write_packet(packet, tmp_path / "inbox")
    ReviewService(tmp_path).process_packet(path)
    feedback.record_feedback(tmp_path, {
        "run_id": "episode-test-attempt-001", "verdict": "partial",
        "missed_issues": [], "corrections": ["Cause is supply capacity."],
        "extra_observations": [], "skill_actions": [], "comment": "Needs more tick data.",
    })

    output, text = generate_brief(
        tmp_path, "episode-test-attempt-001",
        target="Reduce starter-era supply livelock", category="validator",
    )

    assert output.name == "next-edit-brief.md"
    assert "Reduce starter-era supply livelock" in text
    assert "construction_supply_shortage" in text
    assert "Latest feedback verdict: partial" in text
    assert "blocker-construction-supply-shortage" in text
