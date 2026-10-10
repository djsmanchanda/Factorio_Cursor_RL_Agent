# Path: tests/test_campaign_runtime.py
# Purpose: Laya runtime backend adapter for the campaign orchestrator: model
# defaults, natural run command, Muse helper, fast-gate allowlist, freeze record
# and one mocked end-to-end Laya cycle without models or servers.

import json
import subprocess
from pathlib import Path

import pytest

from tools import opencode_campaign_orchestrator as campaign
from tools import campaign_runtime as runtime
from tools.campaign_loop_log import cycles as loop_cycles


def laya_config(tmp_path: Path, **overrides) -> campaign.Config:
    base = campaign.Config(
        state_root=tmp_path / "state", source_save=tmp_path / "source.zip",
        observations=tmp_path / "notes.md", state_file=tmp_path / "state.json",
        opencode_log_dir=tmp_path / "agents", technology="mining-productivity-4",
        interval_seconds=120, post_run_wait_seconds=120, max_cycles=1, max_runtime_seconds=0,
        model=runtime.LAYA_MODEL, variant=runtime.LAYA_VARIANT, opencode_bin="opencode",
        python=Path("python"), campaign_manager=tmp_path / "manager",
        dashboard_url="http://127.0.0.1:9/api", dry_run=False, resume_active_run=False,
        runtime="laya", world_root=tmp_path / "state" / "laya-world",
        code_root=tmp_path, mods_source=tmp_path / "mods", campaign_worktree=tmp_path,
    )
    return campaign.Config(**{**base.__dict__, **overrides})


def test_laya_cli_defaults_declare_deepseek_fix_and_muse_observer(tmp_path):
    args = campaign._parser().parse_args(["--runtime", "laya", "--dry-run"])
    config = campaign._config(args)
    assert config.runtime == "laya"
    assert config.model == runtime.LAYA_MODEL == "opencode-go/deepseek-v4.1-flash"
    assert config.variant == runtime.LAYA_VARIANT == "high"
    assert config.fixer_model == runtime.LAYA_MODEL and config.fixer_variant == "high"
    assert config.observer_model == "opencode-go/muse-spark-1.3-contributor"
    assert config.observer_variant == "xhigh"
    # One Muse observer, no legacy four-observer team.
    assert config.aspect_observers is False


def test_deterministic_cli_defaults_are_unchanged():
    config = campaign._config(campaign._parser().parse_args(["--dry-run"]))
    assert config.runtime == "deterministic"
    assert config.model == campaign.DEFAULT_MODEL
    assert config.observer_model == "opencode-go/muse-spark-1.3-contributor"


def test_backend_for_selects_runtime(tmp_path):
    assert isinstance(runtime.backend_for(laya_config(tmp_path)), runtime.LayaRuntime)
    deterministic = laya_config(tmp_path, runtime="deterministic")
    assert isinstance(runtime.backend_for(deterministic), runtime.DeterministicRuntime)


def test_fast_gate_accepts_only_known_baseline_failures():
    known = runtime.KNOWN_BASELINE_GATE_FAILURES
    baseline = ("FAILED tests/test_training_isolation.py::"
                "test_current_repository_obeys_import_firewall\n")
    assert runtime.gate_failures(baseline) == {
        "tests/test_training_isolation.py::test_current_repository_obeys_import_firewall"}
    assert runtime.gate_acceptable(baseline, known) is True
    new_failure = baseline + "FAILED tests/test_new_behavior.py::test_broken\n"
    assert runtime.gate_acceptable(new_failure, known) is False
    # A collection/import error with no FAILED lines is never accepted.
    assert runtime.gate_acceptable("ImportError: boom\n", known) is False


def test_laya_verification_tolerates_known_gate_failures(tmp_path):
    backend = runtime.LayaRuntime()
    config = laya_config(tmp_path)
    root = tmp_path / "state"
    root.mkdir(parents=True)
    baseline = ("FAILED tests/test_training_isolation.py::"
                "test_current_repository_obeys_import_firewall\n")
    gate = [str(config.python), "-m", "pytest", *runtime.FAST_GATE]
    assert backend.verification_passed(
        config, gate, subprocess.CompletedProcess(gate, 1, baseline, "")) is True
    assert backend.verification_passed(
        config, gate, subprocess.CompletedProcess(gate, 1, baseline +
        "FAILED tests/test_new_behavior.py::test_broken\n", "")) is False
    assert backend.verification_passed(
        config, gate, subprocess.CompletedProcess(gate, 0, "passed", "")) is True
    # A changed regression file must pass outright; the allowlist does not apply.
    changed = [str(config.python), "-m", "pytest", "tests/test_x.py", "-q"]
    assert backend.verification_passed(
        config, changed, subprocess.CompletedProcess(changed, 1, baseline, "")) is False


def test_laya_live_campaign_refuses_deterministic_default_ports(tmp_path):
    source = tmp_path / "source.zip"
    source.write_bytes(b"baseline")
    config = laya_config(tmp_path, source_save=source, dry_run=False)
    with pytest.raises(ValueError, match="private"):
        campaign.run_campaign(config)
    # Explicit private ports pass the guard (validation stops before any launch).
    config = laya_config(tmp_path, source_save=source, dry_run=False,
                         rcon_port=27555, game_port=34555)
    assert config.rcon_port == 27555 and config.game_port == 34555


def test_laya_briefs_do_not_request_observer_team(tmp_path):
    from tools.campaign_prompts import (
        fixer_bootstrap, primary_bootstrap, primary_run_end, primary_run_transition,
    )
    config = laya_config(tmp_path)
    bootstrap = primary_bootstrap(config, 1)
    assert "docs/rl/Laya_Clean_Runner.md" in bootstrap
    assert "Muse helper" in bootstrap and "NO four-observer team" in bootstrap
    transition = primary_run_transition(config, 2, "run-2", "ses_fixer",
                                        tmp_path / "c.md", tmp_path, None)
    assert "do not create an observer team" in transition
    assert "four-agent observation team" not in transition
    fixer = fixer_bootstrap(config)
    assert "isolated Laya campaign" in fixer and "CAMPAIGN_DECISION:" in fixer
    run_end = primary_run_end("run-2", tmp_path / "board.md", "complete",
                              str(tmp_path / "findings.md"), tmp_path / "c.md",
                              tmp_path / "notes.md", config)
    assert "Muse helper" in run_end and "observer board" not in run_end.lower()


def test_laya_run_command_is_natural_goal_without_default_caps(tmp_path):
    config = laya_config(tmp_path, game_port=34555, rcon_port=27555)
    run_cmd = runtime.LayaRuntime().run_command(config)
    joined = " ".join(run_cmd)
    assert "--speed 2" in joined and "--actions deterministic" in joined
    # The goal is the verified research target; no proxy checkpoint or cap ends a natural run by default.
    assert "--research-goal mining-productivity-4" in joined
    assert "--until-accepted" not in joined and "--max-game-ticks" not in joined and "--max-wall-seconds" not in joined
    assert "--decisions 0" not in joined
    prepare = " ".join(runtime.LayaRuntime().prepare_command(config))
    assert "--game-port 34555" in prepare and "--rcon-port 27555" in prepare
    assert "--source-save" in prepare and "--mods-source" in prepare


def test_explicit_experiment_caps_are_honored_but_not_defaulted(tmp_path):
    args = campaign._parser().parse_args(["--runtime", "laya", "--dry-run"])
    config = campaign._config(args)
    assert config.until_accepted is None and config.max_game_ticks is None and config.run_wall_seconds is None
    explicit = laya_config(tmp_path, until_accepted="C4", max_game_ticks=500_000, run_wall_seconds=3600)
    joined = " ".join(runtime.LayaRuntime().run_command(explicit))
    assert "--until-accepted C4" in joined and "--max-game-ticks 500000" in joined and "--max-wall-seconds 3600" in joined


def test_freeze_manifest_records_identity_and_mod_hashes(tmp_path):
    mods = tmp_path / "mods"
    (mods / "factorio_cursor_rl_agent").mkdir(parents=True)
    (mods / "factorio_cursor_rl_agent" / "info.json").write_text("{}")
    first = runtime.mod_hashes(mods)
    assert first == runtime.mod_hashes(mods) and "factorio_cursor_rl_agent" in first
    manifest = runtime.freeze_manifest(
        tmp_path, revision="rev1", source_save_sha256="save", code_hash="code",
        mod_hashes=first, runtime_name="laya")
    written = json.loads((tmp_path / "freeze.json").read_text())
    assert written == manifest
    assert written["revision"] == "rev1" and written["mod_hashes"] == first
    assert written["runtime"] == "laya"


def test_laya_helper_finding_uses_laya_complete_line_and_report_fallback(tmp_path):
    config = laya_config(tmp_path)
    world = config.world_root
    (world / "logs").mkdir(parents=True)
    report = config.code_root / "docs/rl/laya_helper_runs" / "run-1" / "findings.md"
    report.parent.mkdir(parents=True)
    report.write_text("muse report\n")
    log = world / "logs/laya-runner.log"
    log.write_text("RUN START: ts=2026-10-10T00:00:00Z run_id=run-1\n"
                   f"+10s LAYA HELPER: findings complete {report}\n+20s RUN END\n")
    assert runtime.LayaRuntime().helper_finding(config, "run-1") == (str(report), True)
    # Fallback: report exists but the helper has not announced completion.
    log.write_text("RUN START: ts=2026-10-10T00:00:00Z run_id=run-1\n+20s RUN END\n")
    assert runtime.LayaRuntime().helper_finding(config, "run-1") == (str(report), False)


def test_laya_episode_id_comes_from_run_start(tmp_path):
    config = laya_config(tmp_path)
    (config.world_root / "logs").mkdir(parents=True)
    (config.world_root / "logs/laya-runner.log").write_text(
        "RUN START: ts=2026-10-10T00:00:00Z run_id=world-s2-123\n+5s x\n")
    assert runtime.LayaRuntime().episode_id(config) == "world-s2-123"


class CycleHarness:
    """Drive the real orchestrator in Laya mode with every boundary faked."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.state = tmp_path / "state"
        self.world = self.state / "laya-world"
        self.source = tmp_path / "source.zip"
        self.source.write_bytes(b"baseline")
        self.prompts = []
        self.commits = []
        self.milestones = []
        self.staged = []
        self.fix_started = False
        self.observe_failed = False
        self.regression_ok = True
        self.fresh_calls = 0
        self.head = "cafe0001"

    def install(self, monkeypatch):
        harness = self
        (self.state / "agents").mkdir(parents=True, exist_ok=True)

        def write_log():
            (harness.world / "logs").mkdir(parents=True, exist_ok=True)
            harness.fix_started = False
            (harness.world / "logs/laya-runner.log").write_text(
                "RUN START: ts=2026-10-10T00:00:00Z run_id=world-s2-123\n"
                "+120s GOAL MET: accepted C0\n+1463s RUN END\n")

        def fresh(config):
            harness.fresh_calls += 1
            harness.world = config.world_root
            write_log()
            return subprocess.CompletedProcess(runtime.LayaRuntime().run_command(config), 0, "started", "")

        monkeypatch.setattr(runtime.LayaRuntime, "fresh", staticmethod(fresh))
        monkeypatch.setattr(runtime.LayaRuntime, "snapshot", lambda *a: ("## laya snapshot", 0, None))
        monkeypatch.setattr(runtime.LayaRuntime, "helper_finding", lambda *a: (None, False))
        monkeypatch.setattr(runtime.LayaRuntime, "observe",
                            lambda *a: ({"status": "failed", "path": None, "reason": "terminal helper returncode=1"}
                                        if harness.observe_failed
                                        else {"status": "complete",
                                              "path": str(harness.tmp / "findings.md")}))
        monkeypatch.setattr(runtime.LayaRuntime, "regression_proof", lambda *a: harness.regression_ok)
        monkeypatch.setattr(campaign, "_record_milestone",
                            lambda fields, fix_id: harness.milestones.append(fix_id))
        monkeypatch.setattr(campaign, "index_log", lambda *_: None)

        def run(command, **_):
            if command[:2] == ["git", "ls-files"]:
                return subprocess.CompletedProcess(command, 0 if command[-1] == "fix.py" else 1, "", "")
            if command[:2] == ["git", "add"]:
                harness.staged.append(command[3:])
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:2] == ["git", "commit"]:
                harness.commits.append(command[-1])
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:3] == ["git", "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, harness.head, "")
            if "pytest" in command:
                return subprocess.CompletedProcess(command, 0, "passed", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr(campaign, "_run", run)

        def ask(config, prompt, session, sequence, retry=0):
            harness.prompts.append(prompt)
            if "persistent PRIMARY campaign agent" in prompt:
                return "ses_primary", ""
            if "persistent FINAL FIXER" in prompt:
                return "ses_fixer", ""
            if "RUN END detected for episode" in prompt:
                return "ses_primary", ("SYNTHESIS:\nhypothesis: splitter starvation\n"
                                       "confidence: medium\nsupporting: log:1\ncontradicting: none\n"
                                       "competing: power\nreasonable: yes\ndiscriminating: next run\n")
            if "REVIEW_DECISION" in prompt:
                return "ses_primary", "REVIEW_DECISION:\nstatus: approved\nreason: verified"
            if "has ended" in prompt:
                harness.fix_started = True
                return "ses_fixer", ("CAMPAIGN_DECISION:\nstatus: change\nkind: behavior\n"
                                     "files: fix.py\ntest: pytest passed\n"
                                     "prediction: fewer stalls\nreason: focused fix\n")
            return "ses_primary", ""

        monkeypatch.setattr(campaign, "_ask", ask)
        fingerprints = iter(["before", "after"] * 40)
        monkeypatch.setattr(campaign, "_tree_fingerprint", lambda *_: next(fingerprints))
        monkeypatch.setattr(campaign, "_dirty_paths",
                            lambda: {"fix.py"} if harness.fix_started else set())

    def cli(self):
        return ["--runtime", "laya", "--campaign-worktree", str(self.tmp), "--max-cycles", "1",
                "--state-root", str(self.state), "--world-root", str(self.world),
                "--source-save", str(self.source),
                "--rcon-port", "27555", "--game-port", "34555",
                "--observations", str(self.tmp / "notes.md"),
                "--helper-wait-seconds", "0",
                "--opencode-log-dir", str(self.state / "agents")]


def test_laya_cycle_commits_once_with_laya_message_and_no_observer_team(tmp_path, monkeypatch):
    harness = CycleHarness(tmp_path)
    harness.install(monkeypatch)
    assert campaign.main(harness.cli()) == 0

    state = campaign._load_state(harness.state / "logs/opencode-campaign-state.json")
    assert state.completed_cycles == 1
    assert state.primary_session_id == "ses_primary"
    # One focused fix, owner-scoped, under a Laya-labelled commit.
    assert len(harness.commits) == 1 and "fix(RL Laya)" in harness.commits[0]
    assert harness.staged == [["fix.py", "CURRENT_STATUS.md"]]
    # The four-observer deterministic team is never created for Laya.
    assert not any("observer for isolated deterministic episode" in prompt for prompt in harness.prompts)
    records = loop_cycles(harness.state)
    assert [record["outcome"] for record in records] == ["committed"]
    # Each validated stage is recorded truthfully for this successful cycle.
    record = records[0]
    assert record["helper_status"] == "complete"
    assert record["fix_status"] == "committed"
    assert record["verification_result"] == "passed"
    assert record["commit"] == "cafe0001"


def test_rejected_regression_is_abandoned_and_not_reported_as_committed(tmp_path, monkeypatch):
    harness = CycleHarness(tmp_path)
    harness.regression_ok = False
    harness.install(monkeypatch)
    assert campaign.main(harness.cli()) == 0
    assert harness.commits == []
    record = loop_cycles(harness.state)[0]
    assert record["outcome"] == "abandoned"
    assert record["fix_status"] == "abandoned"
    assert not record["commit"]
    assert record["verification_result"] is None


def test_observer_failure_stops_after_one_bounded_retry_without_new_episode(tmp_path, monkeypatch):
    harness = CycleHarness(tmp_path)
    harness.observe_failed = True
    harness.install(monkeypatch)
    argv = harness.cli()
    argv[argv.index("--max-cycles") + 1] = "5"
    assert campaign.main(argv) == 0
    state = campaign._load_state(harness.state / "logs/opencode-campaign-state.json")
    assert state.phase == "HARNESS_FAILURE"
    assert state.completed_cycles == 1
    assert state.observer_failure and state.observer_failure["terminal"] is True
    assert harness.fresh_calls == 1, "no new episode may start after a terminal observer failure"
    assert [record["outcome"] for record in loop_cycles(harness.state)] == ["observer-failed"]


def test_terminal_harness_failure_is_not_auto_resumed_at_same_revision(tmp_path, monkeypatch):
    harness = CycleHarness(tmp_path)
    harness.observe_failed = True
    harness.install(monkeypatch)
    argv = harness.cli()
    argv[argv.index("--max-cycles") + 1] = "5"
    assert campaign.main(argv) == 0
    assert harness.fresh_calls == 1
    # A restart at the same frozen revision must not spawn another unchanged episode.
    harness.install(monkeypatch)
    assert campaign.main(argv) == 0
    assert harness.fresh_calls == 1
    assert campaign._load_state(harness.state / "logs/opencode-campaign-state.json").phase == "HARNESS_FAILURE"
    # A committed change (new revision) legitimately clears the durable stop.
    harness.head = "cafe0002"
    harness.install(monkeypatch)
    assert campaign.main(argv) == 0
    assert harness.fresh_calls == 2


def test_gate_rejects_known_failure_with_collection_error_or_interrupt(tmp_path):
    backend = runtime.LayaRuntime()
    config = laya_config(tmp_path)
    command = [str(config.python), '-m', 'pytest', *runtime.FAST_GATE]
    baseline = 'FAILED tests/test_training_isolation.py::test_current_repository_obeys_import_firewall\n'
    for rc, extra in [(2, 'ERROR tests/test_broken.py\n'), (1, 'ERROR collecting tests/test_broken.py\n'),
                      (2, 'Interrupted: 1 error during collection\n'), (-9, '')]:
        assert not backend.verification_passed(config, command, subprocess.CompletedProcess(command, rc, baseline + extra, ''))


def test_laya_run_disables_live_helper_and_honors_an_explicit_wall_budget(tmp_path):
    config = laya_config(tmp_path, code_root=tmp_path / 'episode' / 'code')
    command = runtime.LayaRuntime().run_command(config)
    assert command[1] == str(config.code_root / 'tools/run_laya_clean.py')
    assert '--no-helper' in command
    # No wall cap by default; an explicit experiment cap is mirrored to the runner.
    assert '--max-wall-seconds' not in command
    capped = laya_config(tmp_path, code_root=tmp_path / 'episode' / 'code', run_wall_seconds=21_600)
    capped_command = runtime.LayaRuntime().run_command(capped)
    assert capped_command[capped_command.index('--max-wall-seconds') + 1] == '21600'


def test_campaign_uses_writable_git_cwd_separate_from_supervisor(tmp_path):
    config = laya_config(tmp_path, campaign_worktree=tmp_path / 'writable')
    command = campaign._opencode_command(config, 'fix', None)
    assert command[command.index('--dir') + 1] == str(config.campaign_worktree)


def _fake_source_archive(destination, revision):
    import io
    import tarfile
    files = {'factorio_mod/info.json': '{"name":"factorio_cursor_rl_agent","version":"1.0.0"}',
             'factorio_mod/control.lua': revision,
             'factorio_training_lab/info.json': '{"name":"factorio_training_lab","version":"1.0.0"}',
             'docs/rl/laya_helper.json': '{"model":"old","variant":"old","deterministic_reference":{}}',
             'tools/run_laya_clean.py': '# immutable source\n',
             'tools/laya_helper_agent.py': '# immutable source\n'}
    with tarfile.open(destination, 'w') as archive:
        for name, value in files.items():
            data = value.encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


def test_two_real_adapter_cycles_use_distinct_frozen_sources_and_terminal_observers(tmp_path, monkeypatch):
    original_fresh = runtime.LayaRuntime.fresh
    original_observe = runtime.LayaRuntime.observe
    harness = CycleHarness(tmp_path)
    harness.install(monkeypatch)
    monkeypatch.setattr(runtime.LayaRuntime, 'fresh', original_fresh)
    monkeypatch.setattr(runtime.LayaRuntime, 'observe', original_observe)
    existing_run = campaign._run
    events = []
    revision = ['rev-one']
    def run(command, **kwargs):
        if command[:3] == ['git', 'rev-parse', 'HEAD']:
            return subprocess.CompletedProcess(command, 0, revision[0], '')
        if command[:2] == ['git', 'archive']:
            _fake_source_archive(command[command.index('--output') + 1], revision[0])
            return subprocess.CompletedProcess(command, 0, '', '')
        if '--source' in command and '--output' in command:
            import shutil
            source = Path(command[command.index('--source') + 1])
            output = Path(command[command.index('--output') + 1])
            output.mkdir()
            shutil.copytree(source, output / 'factorio_cursor_rl_agent')
            shutil.copytree(source, output / 'factorio_laya_agent')
            events.append(('build', source))
            return subprocess.CompletedProcess(command, 0, 'built', '')
        if '--server-mods' in command:
            assert events[-1][0] == 'build'
            events.append(('stamp', Path(command[command.index('--server-mods') + 1])))
            return subprocess.CompletedProcess(command, 0, 'stamped-current-mods', '')
        if len(command) > 2 and command[2] == 'prepare':
            world = Path(command[command.index('--root') + 1])
            assert events[-1][0] == 'stamp'
            assert (world.parents[3] / 'freeze.json').exists()
            world.mkdir(parents=True)
            (world / 'initial-state.json').write_text('{}')
            (world / 'manifest.json').write_text('{}')
            events.append(('prepare', world))
            return subprocess.CompletedProcess(command, 0, 'prepared', '')
        if '--report-root' in command:
            world_log = Path(command[command.index('--log') + 1])
            assert 'RUN END' in world_log.read_text()
            cfg = json.loads(Path(command[command.index('--config') + 1]).read_text())
            assert (cfg['model'], cfg['variant']) == (runtime.LAYA_OBSERVER_MODEL, 'xhigh')
            report = Path(command[command.index('--report-root') + 1]) / command[command.index('--run-id') + 1] / 'findings.md'
            report.parent.mkdir(parents=True)
            report.write_text('verified evidence')
            events.append(('observer', world_log))
            return subprocess.CompletedProcess(command, 0, 'complete', '')
        if len(command) > 2 and command[2] == 'stop':
            assert events[-1][0] == 'observer'
            events.append(('stop', Path(command[command.index('--root') + 1])))
            return subprocess.CompletedProcess(command, 0, 'owned server stopped', '')
        result = existing_run(command, **kwargs)
        if command[:2] == ['git', 'commit']:
            revision[0] = 'rev-two'
        return result
    monkeypatch.setattr(campaign, '_run', run)
    class Process:
        pid = 12345
        def __init__(self, command, **kwargs):
            world = Path(command[command.index('--root') + 1])
            assert kwargs['cwd'] == world.parents[2]
            harness.world = world
            harness.fix_started = False
            (world / 'logs').mkdir(exist_ok=True)
            (world / 'logs/laya-runner.log').write_text(f'RUN START: ts=2026-10-10T00:00:00Z run_id={world.name}-s0-1\n+1s RUN END\n')
            (world.parent / 'laya-runs.jsonl').write_text(json.dumps({'run_id':f'{world.name}-s0-1'}) + '\n')
            events.append(('run', world))
        def poll(self):
            return 0
    monkeypatch.setattr(runtime.subprocess, 'Popen', Process)
    argv = harness.cli()
    argv[argv.index('--max-cycles') + 1] = '2'
    argv += ['--mod-builder', str(tmp_path / 'build_runtime_mods.py'),
             '--stamp-script', str(tmp_path / 'stamp.py'), '--gui-mods', str(tmp_path / 'gui'),
             '--comparators', str(tmp_path / 'old-pair')]
    assert campaign.main(argv) == 0
    frozen = sorted(harness.state.glob('episodes/*/freeze.json'))
    assert len(frozen) == 2
    identities = [json.loads(path.read_text()) for path in frozen]
    assert {identity['revision'] for identity in identities} == {'rev-one', 'rev-two'}
    assert len({identity['world_root'] for identity in identities}) == 2
    assert all(identity['requested_game_speed'] == 2 for identity in identities)
    # The frozen evidence records the verified research goal and that no proxy checkpoint/cap ended it.
    assert all(identity['research_goal'] == 'mining-productivity-4' for identity in identities)
    assert all(identity['until_accepted'] is None and identity['max_game_ticks'] is None
               and identity['max_wall_seconds'] is None for identity in identities)
    assert all(set(identity['mod_hashes']) == {'factorio_cursor_rl_agent', 'factorio_laya_agent', 'factorio_training_lab'} for identity in identities)
    assert len([event for event in events if event[0] == 'prepare']) == 2
    assert len([event for event in events if event[0] == 'observer']) == 2
    assert len(harness.commits) == 2
    assert not any('checkpoint' in prompt.lower() and 'assessment' in prompt.lower() for prompt in harness.prompts)


def test_observer_completion_is_durable_and_configured_once(tmp_path, monkeypatch):
    config = laya_config(tmp_path, code_root=tmp_path / 'episode/code', observer_model=runtime.LAYA_OBSERVER_MODEL,
                         observer_variant='xhigh', comparator_roots=(tmp_path / 'previous',))
    (config.code_root / 'docs/rl').mkdir(parents=True)
    (config.code_root / 'docs/rl/laya_helper.json').write_text('{"deterministic_reference":{}}')
    index = config.code_root / 'data/laya-saved-world/laya-runs.jsonl'
    index.parent.mkdir(parents=True)
    index.write_text('{"run_id":"actual-run"}\n')
    called = []
    def run(command, **kwargs):
        called.append(command)
        report = Path(command[command.index('--report-root') + 1]) / 'actual-run' / 'findings.md'
        report.parent.mkdir(parents=True)
        report.write_text('final')
        return subprocess.CompletedProcess(command, 0, 'done', '')
    monkeypatch.setattr(campaign, '_run', run)
    backend = runtime.LayaRuntime()
    assert backend.observe(config, 'actual-run')['status'] == 'complete'
    assert backend.observe(config, 'actual-run')['status'] == 'complete'
    assert len(called) == 1 and '--observe-root' not in called[0]
    comparator_file = Path(called[0][called[0].index('--comparators') + 1])
    assert comparator_file.name == 'comparators.json'
    assert isinstance(json.loads(comparator_file.read_text()), list)


def test_resume_after_original_deadline_finishes_active_run_without_reset_or_fix(tmp_path, monkeypatch):
    harness = CycleHarness(tmp_path)
    harness.install(monkeypatch)
    world = harness.world
    (world / 'logs').mkdir(parents=True)
    log = world / 'logs/laya-runner.log'
    log.write_text('RUN START: ts=2026-10-10T00:00:00Z run_id=actual-run\n+1s still progressing\n')
    deadline = '2000-01-01T00:00:00+00:00'
    state_file = harness.state / 'logs/opencode-campaign-state.json'
    state = campaign.State(campaign_id='test-campaign', active_cycle=1, active_episode_id='actual-run',
                           deadline_utc=deadline, phase='OBSERVING',
                           episode_runtime={'experiment':'campaign-experiment', 'code_root':str(tmp_path / 'code'),
                                            'world_root':str(world), 'mods_source':str(tmp_path / 'mods')})
    campaign._save_state(state_file, state)
    monkeypatch.setattr(runtime.LayaRuntime, 'fresh', lambda *args: pytest.fail('must not reset active world'))
    stopped = []
    monkeypatch.setattr(runtime.LayaRuntime, 'stop', lambda self, config: stopped.append(log.read_text()))
    monkeypatch.setattr(campaign.time, 'sleep', lambda *args: log.write_text(log.read_text() + '+10s RUN END\n'))
    assert campaign.main(harness.cli()) == 0
    final = campaign._load_state(state_file)
    assert final.deadline_utc == deadline
    assert final.phase == 'DEADLINE_REACHED'
    assert final.completed_cycles == 1 and final.active_cycle is None
    assert len(stopped) == 1 and 'RUN END' in stopped[0]
    assert harness.prompts == [] and harness.commits == []
    assert loop_cycles(harness.state)[0]['outcome'] == 'deadline-after-natural-run'


def test_resumed_launch_waits_existing_pid_instead_of_duplicate_prepare(tmp_path, monkeypatch):
    config = laya_config(tmp_path, code_root=tmp_path / 'episode/code')
    config.code_root.parent.mkdir()
    (config.world_root / 'logs').mkdir(parents=True)
    backend = runtime.LayaRuntime()
    backend._lifecycle_record(config, phase='launching', pid=123)
    monkeypatch.setattr(runtime.LayaRuntime, 'freeze', lambda *args: {})
    monkeypatch.setattr(runtime.os, 'kill', lambda *args: None)
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('duplicate launch'))
    monkeypatch.setattr(campaign, '_run', lambda *args, **kwargs: pytest.fail('duplicate prepare'))
    monkeypatch.setattr(runtime.time, 'sleep', lambda *args: (config.world_root / 'logs/laya-runner.log').write_text(
        'RUN START: ts=2026-10-10T00:00:00Z run_id=actual-id\n'))
    assert backend.fresh(config).returncode == 0
    assert backend._lifecycle_record(config)['run_id'] == 'actual-id'


def test_behavior_fix_requires_a_declared_focused_regression(tmp_path):
    backend = runtime.LayaRuntime()
    config = laya_config(tmp_path)
    log = tmp_path / 'proof.log'
    assert not backend.regression_proof(config, ['training/change.py'], {'kind':'behavior'}, log)
    assert not backend.regression_proof(config, ['training/change.py'],
                                        {'kind':'behavior', 'regression':'tests/test_regression.py::test_bug'}, log)
    # Renaming a code behavior change to instrumentation cannot bypass the test.
    assert not backend.regression_proof(config, ['training/change.py'], {'kind':'instrumentation'}, log)


def test_prior_world_roots_become_explicit_helper_comparison_records(tmp_path):
    parent = tmp_path / 'old-code/data/laya-saved-world'
    world = parent / 'old-pair'
    world.mkdir(parents=True)
    row = {'run_id':'old-run', 'commit':'old-commit', 'root':str(world),
           'started':'2026-10-10T01:50:00+05:30', 'ended':'2026-10-10T02:00:00+05:30',
           'summary':{'accepted_checkpoints':['C0','C1']}, 'experiment':'old-live-pair'}
    (parent / 'laya-runs.jsonl').write_text(json.dumps(row) + '\n')
    records = runtime.comparator_records(world)
    assert records == [{**row, 'kind':'laya'}]
    # Full exploratory experiment records are retained rather than filtered out.
    assert records[0]['experiment'] == 'old-live-pair'


def test_verified_goal_stops_campaign_without_speculative_fixer(tmp_path, monkeypatch):
    harness=CycleHarness(tmp_path)
    harness.install(monkeypatch)
    monkeypatch.setattr(runtime.LayaRuntime,'goal_completed',lambda *args:True,raising=False)
    config=laya_config(tmp_path,state_root=harness.state,source_save=harness.source,
                       state_file=harness.state/'controller.json',observations=harness.state/'journal.md',
                       opencode_log_dir=harness.state/'agents',technology='mining-productivity-4',max_cycles=1,
                       game_port=34555,rcon_port=27555,helper_wait_seconds=0)
    assert campaign.run_campaign(config)==0
    assert not harness.commits and not harness.prompts
    assert campaign._load_state(config.state_file).phase=='SUCCESS'


def test_goal_completion_requires_owned_indexed_verified_research(tmp_path):
    config=laya_config(tmp_path,technology='mining-productivity-4')
    config.world_root.parent.mkdir(parents=True,exist_ok=True)
    index=config.world_root.parent/'laya-runs.jsonl'
    row={'run_id':'run-1','root':str(config.world_root),'window':{'research_goal':'mining-productivity-4'},
         'summary':{'stop':'goal_completed','goal':{'verified':True,'target_completed':True}}}
    index.write_text(json.dumps(row)+'\n')
    assert runtime.LayaRuntime().goal_completed(config,'run-1') is True
    row['summary']['goal']['verified']=False
    index.write_text(json.dumps(row)+'\n')
    assert runtime.LayaRuntime().goal_completed(config,'run-1') is False


def test_pingpong_two_cycles_keep_deepseek_and_refresh_only_haiku(tmp_path, monkeypatch):
    from tools import campaign_pingpong as pp
    from datetime import datetime, timedelta, timezone
    harness = CycleHarness(tmp_path)
    harness.install(monkeypatch)
    args = campaign._parser().parse_args(harness.cli() + [
        '--agent-topology', 'deepseek-haiku', '--campaign-branch', pp.BRANCH,
        '--fixer-model', pp.HAIKU, '--fixer-variant', 'xhigh',
        '--observer-model', pp.DEEPSEEK, '--observer-variant', 'high'])
    config = campaign._config(args)
    config = campaign.replace(config, max_cycles=2)
    deadline = (datetime.now(timezone.utc)+timedelta(hours=6)).isoformat()
    campaign._save_state(config.state_file, campaign.State(primary_session_id='real-deepseek', deadline_utc=deadline))
    pp.atomic(config.state_root/'campaign.json', {'deadline_utc':deadline,'deepseek_session_id':'real-deepseek'})
    original_run = campaign._run
    def run(cmd, **kw):
        if cmd == ['git','branch','--show-current']:
            return subprocess.CompletedProcess(cmd, 0, pp.BRANCH, '')
        return original_run(cmd, **kw)
    monkeypatch.setattr(campaign, '_run', run)
    deliveries, notifications = [], []
    monkeypatch.setattr(pp, 'relay_completion', lambda config, state, fix, output: deliveries.append((state.primary_session_id, state.fixer_session_id)))
    monkeypatch.setattr(pp, 'notify_run', lambda config, state: notifications.append(state.primary_session_id))
    def ask(config, prompt, session, sequence, retry=0):
        state = campaign._load_state(config.state_file)
        if not config.read_only:
            assert state.fixer_cycle == state.active_cycle
            assert session is None, 'every cycle must begin a fresh Claude session'
            harness.fix_started = True
            return f'actual-haiku-{state.active_cycle}', ('CAMPAIGN_DECISION:\nstatus: change\nkind: behavior\nfiles: fix.py\ntest: passed\nprediction: fewer stalls\nreason: focused\n')
        assert session == 'real-deepseek'
        if 'REVIEW_DECISION' in prompt:
            return session, 'REVIEW_DECISION:\nstatus: approved\nreason: evidence'
        return session, 'SYNTHESIS:\nhypothesis: shortage\nconfidence: high\nsupporting: log'
    monkeypatch.setattr(campaign, '_ask', ask)
    assert campaign.run_campaign(config) == 0
    state = campaign._load_state(config.state_file)
    assert state.primary_session_id == 'real-deepseek' and state.deadline_utc == deadline
    assert state.completed_cycles == 2 and len(harness.commits) == 2
    assert deliveries == [('real-deepseek', 'actual-haiku-1'), ('real-deepseek', 'actual-haiku-2')]
    assert notifications == ['real-deepseek', 'real-deepseek']
