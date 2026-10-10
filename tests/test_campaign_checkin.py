# Path: tests/test_campaign_checkin.py | Purpose: Verify bounded campaign check-ins, deadline-preserving resume, and evidence deltas without real systemd.
import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from tools import campaign_checkin


class FakeRunner:
    def __init__(self, *, active=False, commits=8, start_stays_inactive=False):
        self.active = active
        self.commits = commits
        self.start_stays_inactive = start_stays_inactive
        self.commands = []

    def __call__(self, command, **_kwargs):
        self.commands.append(command)
        if command[:3] == ["systemctl", "--user", "is-active"]:
            return CompletedProcess(command, 0 if self.active else 3, "active\n" if self.active else "inactive\n", "")
        if command[:3] == ["systemctl", "--user", "start"]:
            if not self.start_stays_inactive:
                self.active = True
            return CompletedProcess(command, 0, "", "")
        if len(command) > 3 and command[0:2] == ["git", "-C"] and command[3] == "rev-list":
            return CompletedProcess(command, 0, f"{self.commits}\n", "")
        if len(command) > 3 and command[0:2] == ["git", "-C"] and command[3] == "log":
            return CompletedProcess(command, 0, "abc123 campaign fix\n", "")
        if command[0] == "free":
            return CompletedProcess(command, 0, "Mem: 8G free\n", "")
        if command[0] == "uptime":
            return CompletedProcess(command, 0, "up 2 days, load average: 0.1\n", "")
        raise AssertionError(f"unexpected command {command}")


@pytest.fixture
def setup(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"runtime": "laya", "deadline_epoch": 2000}))
    run_dir = tmp_path / "campaign"
    run_dir.mkdir()
    queue = tmp_path / "next-round.md"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (run_dir / "controller.json").write_text('{"completed_cycles": 1}')
    (run_dir / "progress.json").write_text('{"phase": "running"}')
    (run_dir / "status.json").write_text('{"phase": "running"}')
    return settings, run_dir, queue, worktree


def test_inactive_unit_resumes_before_original_deadline_and_appends_both_notes(setup):
    settings, run_dir, queue, worktree = setup
    runner = FakeRunner(active=False)
    result = campaign_checkin.checkin(settings, "laya-loop-example.service", run_dir, queue, worktree,
                                      runner=runner, now=lambda: 1000)
    starts = [cmd for cmd in runner.commands if cmd[:3] == ["systemctl", "--user", "start"]]
    assert starts == [["systemctl", "--user", "start", "laya-loop-example.service"]]
    assert result["resumed"] is True and result["active"] is True
    assert json.loads((run_dir / "last-checkin.json").read_text())["checked_at"] == 1000
    assert "resumed=true" in (run_dir / "progress-notes.md").read_text()
    assert (run_dir / "progress-notes.md").read_text() == queue.read_text()


def test_at_original_deadline_records_final_state_without_restart(setup):
    settings, run_dir, queue, worktree = setup
    runner = FakeRunner(active=False)
    result = campaign_checkin.checkin(settings, "laya-loop-example.service", run_dir, queue, worktree,
                                      runner=runner, now=lambda: 2000)
    assert not any(cmd[:3] == ["systemctl", "--user", "start"] for cmd in runner.commands)
    assert result["at_deadline"] is True
    assert result["active"] is False
    assert "deadline=reached" in queue.read_text()


def test_successful_start_is_rechecked_and_failure_is_recorded_in_both_notes(setup):
    settings, run_dir, queue, worktree = setup
    runner = FakeRunner(active=False, start_stays_inactive=True)
    result = campaign_checkin.checkin(settings, "laya-loop-example.service", run_dir, queue, worktree,
                                      runner=runner, now=lambda: 1000)
    checks = [cmd for cmd in runner.commands if cmd[:3] == ["systemctl", "--user", "is-active"]]
    assert len(checks) == 2
    assert result["resumed"] is True
    assert result["active"] is False
    assert "unit remained inactive" in result["recovery_error"]
    assert result["note"].rstrip() == (run_dir / "progress-notes.md").read_text().rstrip()
    assert result["note"].rstrip() == queue.read_text().rstrip()
    assert "unit remained inactive" in queue.read_text()


def test_counts_new_run_findings_heartbeat_and_commit_delta(setup):
    settings, run_dir, queue, worktree = setup
    runner = FakeRunner(active=True, commits=8)
    campaign_checkin.checkin(settings, "laya-loop-example.service", run_dir, queue, worktree,
                             runner=runner, now=lambda: 1000)
    episode = run_dir / "runs/episode-2"
    (episode / "agents/muse").mkdir(parents=True)
    (episode / "freeze.json").write_text('{"commit": "abc"}')
    (episode / "agents/muse/findings.md").write_text("read-only evidence")
    (episode / "agents/muse/helper-state.json").write_text(json.dumps({"status":"complete"}))
    (episode / "heartbeat.json").write_text('{"phase":"running"}')
    runner.commits = 9
    result = campaign_checkin.checkin(settings, "laya-loop-example.service", run_dir, queue, worktree,
                                      runner=runner, now=lambda: 1100)
    assert result["new_runs"] == ["runs/episode-2"]
    assert set(result["changed_evidence"]) == {
        "runs/episode-2/freeze.json", "runs/episode-2/agents/muse/findings.md",
        "runs/episode-2/heartbeat.json",
    }
    assert "1 run dirs (+1: runs/episode-2)" in result["note"]
    assert "findings=1 (1 new/updated; completed=1 (+1), placeholder=0)" in result["note"]
    assert "heartbeats=1 (1 new/updated)" in result["note"]
    assert "commits=9 (+1)" in result["note"]
    assert (run_dir / "progress-notes.md").read_text().count("## ") == 2
    assert queue.read_text().count("## ") == 2


@pytest.mark.parametrize("unit,settings_update", [
    ("factorio-loop.service", {}),
    ("laya-loop-example.service", {"runtime": "deterministic"}),
])
def test_refuses_non_laya_runtime_or_unowned_unit(setup, unit, settings_update):
    settings, run_dir, queue, worktree = setup
    if settings_update:
        settings.write_text(json.dumps({"runtime": "deterministic", "deadline_epoch": 2000}))
    with pytest.raises(ValueError):
        campaign_checkin.checkin(settings, unit, run_dir, queue, worktree,
                                 runner=FakeRunner(), now=lambda: 1000)


def test_placeholder_findings_are_not_counted_as_completed(setup):
    settings, run_dir, queue, worktree = setup
    runner = FakeRunner(active=True, commits=8)
    placeholder = run_dir / "runs/episode-1/agents/muse/findings.md"
    placeholder.parent.mkdir(parents=True)
    placeholder.write_text(
        "<!-- Path: x | Purpose: Read-only Laya Helper findings for run. -->\n\n"
        "# Laya Helper findings — run\n\n_Review in progress (started 2026-10-10T13:35:54+05:30)._\n")
    substantive = run_dir / "runs/episode-2/agents/muse/findings.md"
    substantive.parent.mkdir(parents=True)
    substantive.write_text("# Findings\n\n## Root cause\nSplitter starvation at the loan gate.\n")
    (substantive.parent/"helper-state.json").write_text(json.dumps({"status":"complete"}))
    result = campaign_checkin.checkin(settings, "laya-loop-example.service", run_dir, queue, worktree,
                                      runner=runner, now=lambda: 1000)
    assert "findings=2 (2 new/updated; completed=1 (+1), placeholder=1)" in result["note"]


def test_validated_stages_are_reported_separately_from_gameplay(setup):
    settings, run_dir, queue, worktree = setup
    runner = FakeRunner(active=True, commits=8)
    loop = run_dir / "logs/campaign-loop/loop.jsonl"
    loop.parent.mkdir(parents=True)
    loop.write_text(json.dumps({
        "campaign_id": "c", "cycle_id": 1, "outcome": "committed", "helper_status": "complete",
        "fix_status": "committed", "verification_result": "passed", "commit": "cafe0001"}) + "\n")
    (run_dir / "runs/episode-1/heartbeat.json").parent.mkdir(parents=True)
    (run_dir / "runs/episode-1/heartbeat.json").write_text('{"phase":"running"}')
    result = campaign_checkin.checkin(settings, "laya-loop-example.service", run_dir, queue, worktree,
                                      runner=runner, now=lambda: 1000)
    assert "Gameplay activity (actor continuity, not validated stages):" in result["note"]
    assert ("last_cycle[outcome=committed; observer=complete; fixer=committed; "
            "tests/gate=passed; commit=cafe0001]") in result["note"]


def test_deliberate_harness_failure_is_not_auto_resumed(setup):
    settings, run_dir, queue, worktree = setup
    (run_dir / "progress.json").write_text('{"phase": "HARNESS_FAILURE"}')
    runner = FakeRunner(active=False)
    result = campaign_checkin.checkin(settings, "laya-loop-example.service", run_dir, queue, worktree,
                                      runner=runner, now=lambda: 1000)
    assert not any(cmd[:3] == ["systemctl", "--user", "start"] for cmd in runner.commands)
    assert result["resumed"] is False and result["active"] is False
    assert result["resume_suppressed"] is True
    assert "auto-resume suppressed (deliberate terminal failure)" in result["note"]


def test_substantive_draft_with_failed_machine_state_is_not_completed(tmp_path):
    report=tmp_path/'observer/reports/run/findings.md'
    report.parent.mkdir(parents=True)
    report.write_text('# Findings\n\nA substantive draft was produced but final verification failed.\n')
    state=tmp_path/'observer/state.json'
    state.write_text('{"status":"failed","path":null}')
    assert campaign_checkin._findings_completed(report) is False
    state.write_text(json.dumps({'status':'complete','path':str(report)}))
    assert campaign_checkin._findings_completed(report) is True
