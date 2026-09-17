# Path: tests/test_campaign_lifecycle.py
# Purpose: End-to-end mocked campaign lifecycle: persistent sessions, fresh
# observer teams, RUN END sequencing, helper window, dirty worktree and restarts.
import json
import subprocess
from pathlib import Path

import pytest

from tools import opencode_campaign_orchestrator as campaign
from tools.campaign_loop_log import cycles as loop_cycles, fixes as fix_ledger, loop_root
from tools.campaign_observers import observer_sessions


class Harness:
    """Drive the real controller with every external boundary mocked."""

    def __init__(self, tmp_path, *, helper_wait=0, revisions=2, aspect_observers=True):
        self.root = tmp_path / "state"
        self.source = tmp_path / "source.zip"
        self.source.write_bytes(b"baseline")
        self.notes = tmp_path / "notes.md"
        self.log_path = self.root / "logs/autonomous-run.log"
        self.helper_wait = helper_wait
        self.revisions = revisions
        self.aspect_observers = aspect_observers
        self.prompts = []
        self.staged = []
        self.commits = []
        self.milestones = []
        self.episodes = []
        self.pytest_failures = 0
        self.helper_line = None
        self.helper_file = None
        self.observer_failures = {}  # episode -> set of roles that fail once
        self.fix_started = False  # fixer edited the worktree after its episode turn
        self.user_dirty = set()  # unrelated pre-existing user changes
        self.fixer_decision = ("CAMPAIGN_DECISION:\nstatus: change\nkind: behavior\nfiles: fix.py\n"
                               "test: pytest passed\nprediction: sustained plastic\nreason: focused fix\n")

    def write_run_log(self, episode, index):
        self.fix_started = False  # a fresh episode starts with a clean worktree
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "episode").mkdir(parents=True, exist_ok=True)
        (self.root / "episode/current.json").write_text(json.dumps({"episode_id": episode}))
        manifest = dict(episode_id=episode, repository_revision="rev", source_save_sha256="save",
                        deployed_factorio_mod_sha256="mod", deployed_factorio_training_lab_sha256="lab",
                        bootstrap_profile="reduced-v1", surface="nauvis", force="player")
        (self.root / "episode/current.json").write_text(json.dumps(manifest))
        text = f"RUN START: ts=2026-09-11T0{index}:00:00Z\n+120s stage\n"
        if self.helper_line:
            text += f"OPENCODE HELPER: findings complete {self.helper_file}\n"
        if self.helper_file:
            Path(self.helper_file).parent.mkdir(parents=True, exist_ok=True)
            Path(self.helper_file).write_text("helper terminal diagnosis\n")
        text += "+1463s RUN END\n"
        self.log_path.write_text(text)

    # -- boundary mocks ------------------------------------------------------
    def install(self, monkeypatch):
        harness = self

        def run(command, **_):
            joined = " ".join(str(part) for part in command)
            if "fresh" in command:
                episode = "episode-%02d" % (len(harness.episodes) + 1)
                harness.episodes.append(episode)
                harness.write_run_log(episode, len(harness.episodes))
                return subprocess.CompletedProcess(command, 0, "fresh ok", "")
            if "pytest" in command:
                return subprocess.CompletedProcess(command, harness.pytest_failures, "tests", "")
            if command[:2] == ["git", "ls-files"]:
                return subprocess.CompletedProcess(command, 0 if command[-1] == "fix.py" else 1, "", "")
            if command[:2] == ["git", "add"]:
                harness.staged.append(command[3:])
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:2] == ["git", "commit"]:
                harness.commits.append(command[-1])
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:3] == ["git", "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "cafe%04d" % len(harness.commits), "")
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr(campaign, "_run", run)
        monkeypatch.setattr(campaign, "_snapshot", lambda *a: ("## snapshot", 0, None))
        monkeypatch.setattr(campaign, "_code_hash", lambda: "code")
        monkeypatch.setattr(campaign, "index_log", lambda *_: None)
        monkeypatch.setattr(campaign, "_record_milestone",
                            lambda fields, fix_id: harness.milestones.append((fields, fix_id)))

        def ask(config, prompt, session, sequence, retry=0):
            harness.prompts.append((prompt, session))
            import re
            if "persistent PRIMARY campaign agent" in prompt:
                return "ses_primary", ""
            if "persistent FINAL FIXER" in prompt:
                return "ses_fixer", ""
            if "observer for isolated deterministic episode" in prompt:
                role = re.search(r"READ-ONLY (\S+) observer", prompt).group(1)
                episode = re.search(r"episode (episode-[\w-]+)", prompt).group(1)
                if role in harness.observer_failures.get(episode, set()):
                    harness.observer_failures[episode].discard(role)
                    return None, ""
                return f"ses_obs_{role}-{episode}", json.dumps(
                    {"type": "text", "part": {"text": f"{role} evidence"}})
            stripped = prompt.strip()
            if stripped == "continue":
                if session and session.startswith("ses_obs_"):
                    return session, json.dumps({"type": "text", "part": {"text": "still observing"}})
                if session == "ses_fixer":
                    return session, harness.fixer_decision
                return session or "ses_primary", ""
            if "RUN END detected for episode" in prompt:
                return "ses_primary", ("Terminal synthesis.\n\nSYNTHESIS:\nhypothesis: power bridge\n"
                                       "confidence: high\nsupporting: log:1\ncontradicting: none\n"
                                       "competing: supply\n"
                                       "discriminating: a second observation of the bridge\n")
            if "REVIEW_DECISION" in prompt:
                return "ses_primary", "REVIEW_DECISION:\nstatus: approved\nreason: verified"
            if "has ended" in prompt or "Primary review rejected" in prompt \
                    or "Verification failed" in prompt:
                harness.fix_started = True
                return "ses_fixer", harness.fixer_decision
            return "ses_primary", ""

        harness.ask_impl = ask
        monkeypatch.setattr(campaign, "_ask", ask)
        fingerprints = iter(["before", "after"] * 40)
        monkeypatch.setattr(campaign, "_tree_fingerprint", lambda *_: next(fingerprints))
        monkeypatch.setattr(campaign, "_dirty_paths",
                            lambda: (set(harness.user_dirty)
                                     | ({"fix.py"} if harness.fix_started else set())))

    def cli(self, max_cycles=2):
        args = ["--state-root", str(self.root), "--source-save", str(self.source),
                "--observations", str(self.notes), "--max-cycles", str(max_cycles),
                "--helper-wait-seconds", str(self.helper_wait),
                "--max-fix-revisions", str(self.revisions),
                "--opencode-log-dir", str(self.root / "agents")]
        if self.aspect_observers:
            args.append("--aspect-observers")
        return args

    def observers_of(self, episode):
        from tools.campaign_observers import observer_sessions
        return observer_sessions(episode, self.root / "agents")


def test_two_cycles_reuse_persistent_sessions_with_fresh_observers(tmp_path, monkeypatch):
    harness = Harness(tmp_path, aspect_observers=True)
    harness.install(monkeypatch)
    assert campaign.main(harness.cli(max_cycles=2)) == 0

    state = campaign._load_state(harness.root / "logs/opencode-campaign-state.json")
    assert state.primary_session_id == "ses_primary"
    assert state.fixer_session_id == "ses_fixer"
    assert state.completed_cycles == 2

    # Run 1 and run 2 observer teams are disjoint and fully replaced.
    run1 = harness.observers_of(harness.episodes[0])
    run2 = harness.observers_of(harness.episodes[1])
    assert set(run1) and set(run2)
    assert not (set(run1.values()) & set(run2.values()))
    assert len(run1) == 4 and len(run2) == 4

    # The full bootstrap prompt is sent exactly once; later runs get transitions.
    assert sum(1 for prompt, _ in harness.prompts if "persistent PRIMARY campaign agent" in prompt) == 1
    transitions = [prompt for prompt, _ in harness.prompts if "Starting deterministic run" in prompt]
    assert len(transitions) == 1
    assert "NEW four-agent observation team" in transitions[0]
    assert "Previous cycle result:" in transitions[0]

    # The fixer ran for both episodes through the same session.
    fixer_turns = [prompt for prompt, _ in harness.prompts if "has ended" in prompt]
    assert len(fixer_turns) == 2 and "NEW episode" in fixer_turns[1]

    ledger = fix_ledger(harness.root)
    assert len(ledger) == 2
    assert all(record["status"] == "committed" for record in ledger.values())
    records = loop_cycles(harness.root)
    assert [record["cycle_id"] for record in records] == [1, 2]
    assert records[0]["primary_session_id"] == "ses_primary"
    assert records[1]["commit"] == "cafe0002"
    assert harness.milestones  # CURRENT_STATUS milestone recorded per accepted fix


def test_observer_failure_in_run_two_recovers_without_parking(tmp_path, monkeypatch):
    harness = Harness(tmp_path, aspect_observers=True)
    harness.episodes.append("seed")  # first episode is written by fresh; preload failure key
    harness.install(monkeypatch)
    # The 'supply' observer fails once in run 2's first checkpoint.
    harness.observer_failures = {"episode-02": {"supply"}}
    # after the episode-02 team is created at cycle 2...
    real_write = harness.write_run_log

    def write_run_log(episode, index):
        real_write(episode, index)
        if episode == "episode-02":
            harness.observer_failures.setdefault("episode-02", {"supply"})

    harness.write_run_log = write_run_log
    assert campaign.main(harness.cli(max_cycles=2)) == 0
    state = campaign._load_state(harness.root / "logs/opencode-campaign-state.json")
    assert state.completed_cycles == 2
    board = harness.root / "agents/observers/episode-02/supply/state.json"
    recovered = json.loads(board.read_text())
    assert recovered["status"] == "complete"
    assert recovered["session_id"] == "ses_obs_supply-episode-02"


def test_helper_findings_within_window_are_included(tmp_path, monkeypatch):
    harness = Harness(tmp_path, helper_wait=120)
    harness.helper_file = str(tmp_path / "helper" / "episode-01" / "findings.md")
    harness.helper_line = harness.helper_file
    harness.install(monkeypatch)
    assert campaign.main(harness.cli(max_cycles=1)) == 0
    synthesis_prompt = [prompt for prompt, _ in harness.prompts if "RUN END detected" in prompt][0]
    assert f"OpenCode helper findings: {harness.helper_file}" in synthesis_prompt
    records = loop_cycles(harness.root)
    assert records[0]["helper_status"] == "complete"
    assert records[0]["helper_path"] == harness.helper_file


def test_helper_timeout_proceeds(tmp_path, monkeypatch):
    harness = Harness(tmp_path, helper_wait=0)
    harness.install(monkeypatch)
    assert campaign.main(harness.cli(max_cycles=1)) == 0
    synthesis_prompt = [prompt for prompt, _ in harness.prompts if "RUN END detected" in prompt][0]
    assert "unavailable (status: unavailable)" in synthesis_prompt
    records = loop_cycles(harness.root)
    assert records[0]["helper_status"] == "unavailable"
    # The fixer still ran and the cycle committed.
    assert harness.commits and "FIX-0001" in harness.commits[0]


def test_late_helper_findings_are_preserved_as_late_evidence(tmp_path, monkeypatch):
    import types
    harness = Harness(tmp_path)
    helper_file = tmp_path / "helper" / "episode-01" / "findings.md"
    harness.install(monkeypatch)
    assert campaign.main(harness.cli(max_cycles=1)) == 0
    state = campaign._load_state(harness.root / "logs/opencode-campaign-state.json")
    state.helper = {"episode": "episode-01", "status": "unavailable", "path": None,
                    "reason": "no helper findings within 0s after RUN END"}
    helper_file.parent.mkdir(parents=True, exist_ok=True)
    helper_file.write_text("late findings\n")
    # simulate the helper announcing completion after the flow moved on
    log = harness.log_path.read_text()
    harness.log_path.write_text(log + f"OPENCODE HELPER: findings complete {helper_file}\n")
    helper = campaign._late_helper(types.SimpleNamespace(state_root=harness.root), state, "episode-01")
    assert helper["status"] == "late" and helper["path"] == str(helper_file)


def test_dirty_worktree_commits_only_owned_files(tmp_path, monkeypatch):
    harness = Harness(tmp_path)
    harness.install(monkeypatch)
    # Pre-cycle: runtime artifacts, helper report and unrelated user work are
    # dirty; CURRENT_STATUS.md was already dirty before the controller ran.
    pre_cycle = {"CURRENT_STATUS.md", "saves/mod_playground.zip",
                 "docs/deterministic/opencode_helper_runs/e1/findings.md", "user_note.md"}
    calls = {"n": 0}

    def dirty_paths():
        calls["n"] += 1
        return pre_cycle if calls["n"] == 1 else pre_cycle | {"fix.py"}

    monkeypatch.setattr(campaign, "_dirty_paths", dirty_paths)
    assert campaign.main(harness.cli(max_cycles=1)) == 0
    assert harness.staged == [["fix.py"]]
    assert not any("user_note.md" in " ".join(command) for command in harness.staged)
    assert not any("saves/" in " ".join(command) for command in harness.staged)
    records = loop_cycles(harness.root)
    assert records[0]["outcome"] == "committed"


def test_fixer_cannot_commit_unrelated_user_file(tmp_path, monkeypatch):
    harness = Harness(tmp_path)
    harness.fixer_decision = ("CAMPAIGN_DECISION:\nstatus: change\nkind: behavior\n"
                              "files: user_note.md\nprediction: none\nreason: bad scope\n")
    harness.install(monkeypatch)
    # The declared file is unrelated pre-existing user work: revision, then abandon.
    harness.user_dirty = {"user_note.md"}
    fingerprints = iter(["before", "same"] * 12)
    monkeypatch.setattr(campaign, "_tree_fingerprint", lambda *_: next(fingerprints))
    assert campaign.main(harness.cli(max_cycles=1)) == 0
    assert harness.staged == []
    records = loop_cycles(harness.root)
    assert records[0]["outcome"] == "abandoned"
    ledger = fix_ledger(harness.root)
    assert list(ledger.values())[-1]["status"] == "abandoned"


def test_restart_during_fixing_resumes_without_new_episode(tmp_path, monkeypatch):
    harness = Harness(tmp_path)
    harness.install(monkeypatch)
    args = harness.cli(max_cycles=1)

    def dying_ask(config, prompt, session, sequence, retry=0):
        if "persistent FINAL FIXER" in prompt or "has ended" in prompt:
            raise KeyboardInterrupt
        return harness.ask_impl(config, prompt, session, sequence, retry)

    monkeypatch.setattr(campaign, "_ask", dying_ask)
    with pytest.raises(KeyboardInterrupt):
        campaign.main(args)
    state = campaign._load_state(harness.root / "logs/opencode-campaign-state.json")
    assert state.pending_fix and state.pending_fix["phase"] == "fixing"
    assert state.active_episode_id == "episode-01"
    assert state.primary_session_id == "ses_primary"
    # Restart completes the same fix through the same sessions; no new episode.
    harness.install(monkeypatch)
    assert campaign.main(args) == 0
    assert harness.episodes == ["episode-01"]
    records = loop_cycles(harness.root)
    assert len(records) == 1 and records[0]["outcome"] == "committed"
    restored = campaign._load_state(harness.root / "logs/opencode-campaign-state.json")
    assert restored.primary_session_id == "ses_primary"
    assert restored.fixer_session_id == "ses_fixer"


def test_restart_after_commit_does_not_duplicate_commit(tmp_path, monkeypatch):
    harness = Harness(tmp_path)
    harness.install(monkeypatch)
    assert campaign.main(harness.cli(max_cycles=1)) == 0
    commits_after_run1 = list(harness.commits)
    harness.install(monkeypatch)
    assert campaign.main(harness.cli(max_cycles=1)) == 0
    records = loop_cycles(harness.root)
    assert len([record for record in records if record["cycle_id"] == 1]) == 1
    assert harness.commits == commits_after_run1


def test_restart_between_run_end_and_synthesis_skips_finished_steps(tmp_path, monkeypatch):
    harness = Harness(tmp_path, aspect_observers=True)
    harness.install(monkeypatch)
    args = harness.cli(max_cycles=1)
    # First controller dies right after RUN END evidence collection completed.
    def dying_ask(config, prompt, session, sequence, retry=0):
        if "RUN END detected for episode" in prompt:
            raise KeyboardInterrupt
        return harness.ask_impl(config, prompt, session, sequence, retry)

    monkeypatch.setattr(campaign, "_ask", dying_ask)
    with pytest.raises(KeyboardInterrupt):
        campaign.main(args)
    state = campaign._load_state(harness.root / "logs/opencode-campaign-state.json")
    assert state.run_end and state.helper, "run end evidence must be persisted"
    # Restart: helper is not re-waited and observers are not re-finalized.
    harness.install(monkeypatch)
    assert campaign.main(args) == 0
    synthesis_prompts = [prompt for prompt, _ in harness.prompts
                         if "RUN END detected for episode" in prompt]
    assert len(synthesis_prompts) == 1, "synthesis must not restart from scratch"
    fixer_prompts = [prompt for prompt, _ in harness.prompts if "has ended" in prompt]
    assert len(fixer_prompts) == 1
    records = loop_cycles(harness.root)
    assert records[0]["outcome"] == "committed"
