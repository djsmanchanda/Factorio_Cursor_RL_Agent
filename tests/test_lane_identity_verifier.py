# Path: tests/test_lane_identity_verifier.py
# Purpose: Focused synthetic and manager-contract checks for the read-only lane verifier.
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools.deterministic_fleet_runtime import directory_hash
from tools.lane_identity_verifier import (
    GitIdentity,
    LaneExpectation,
    LaneIdentityError,
    ListenerScan,
    ListeningSocket,
    ProcessSnapshot,
    ProcfsProbe,
    read_git_identity,
    verify_lane_identity,
)

SERVER_PID = 4101
RUNNER_PID = 4102
SERVER_TOKEN = 7001
RUNNER_TOKEN = 7002
COMMIT = "a" * 40
RCON_PORT = 27100
GAME_PORT = 34200
BOOT_ID = "12345678-1234-4234-8234-123456789abc"
OTHER_BOOT_ID = "87654321-4321-4321-8321-cba987654321"
NETWORK_NAMESPACE = "net:[4026531992]"


class FakeProbe:
    def __init__(self, processes, scan, udp_scan=None):
        self.processes = processes
        self.scan = scan
        self.udp_scan = udp_scan if udp_scan is not None else ListenerScan(sockets=(
            ListeningSocket(inode=5252, host="127.0.0.1", port=GAME_PORT,
                            owner_pids=frozenset({SERVER_PID})),
        ))

    def process(self, pid):
        return self.processes.get(pid)

    def boot_id(self):
        return BOOT_ID

    def network_namespace(self):
        return NETWORK_NAMESPACE

    def listeners(self, port):
        return self.scan

    def udp_sockets(self, port):
        return self.udp_scan


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _server_argv(lane: Path, executable: Path) -> list[str]:
    return [
        str(executable), "--config", str(lane / "config.ini"),
        "--mod-directory", str(lane / "mods"),
        "--start-server", str(lane / "saves" / "mod_playground.zip"),
        "--server-settings", str(lane / "server-settings.json"),
        "--bind", f"127.0.0.1:{GAME_PORT}",
        "--rcon-bind", f"127.0.0.1:{RCON_PORT}",
        "--rcon-password", "secret",
        "--console-log", str(lane / "logs" / "factorio-console.log"),
    ]


def _runner_argv(lane: Path, executable: Path, candidate: Path) -> list[str]:
    return [
        str(executable), "-u", str(candidate / "tools" / "autonomous_run.py"),
        "research", "mining-productivity-4", "--surface", "nauvis", "--force", "player",
        "--rcon-host", "127.0.0.1", "--rcon-port", str(RCON_PORT),
        "--rcon-secret-file", str(lane / "rcon-password"),
        "--script-output", str(lane / "script-output"),
        "--episode-manifest", str(lane / "episode" / "current.json"),
        "--run-id", "suite-1-attempt-1",
        "--reference-point", "3", "-1", "--max-iterations", "100",
        "--log-file", str(lane / "logs" / "autonomous-run.log"),
    ]


def _lane(tmp_path: Path):
    lane = (tmp_path / "lane").resolve()
    candidate = lane / "candidate"
    for directory in (
        "saves", "mods/factorio_cursor_rl_agent", "mods/factorio_training_lab",
        "logs", "script-output", "episode",
    ):
        (lane / directory).mkdir(parents=True)
    (candidate / "tools").mkdir(parents=True)
    (candidate / "tools" / "autonomous_run.py").write_text("# runner\n", encoding="utf-8")
    (lane / "mods/factorio_cursor_rl_agent/control.lua").write_text("-- mod\n", encoding="utf-8")
    (lane / "mods/factorio_training_lab/control.lua").write_text("-- lab\n", encoding="utf-8")
    world = b"checkpoint world"
    (lane / "saves/checkpoint-input.zip").write_bytes(world)
    (lane / "saves/mod_playground.zip").write_bytes(world)
    (lane / "config.ini").write_text("[path]\n", encoding="utf-8")
    (lane / "server-settings.json").write_text("{}\n", encoding="utf-8")
    secret = lane / "rcon-password"
    secret.write_text("secret", encoding="ascii")
    secret.chmod(0o600)
    (lane / "logs/factorio-console.log").write_text("", encoding="utf-8")
    (lane / "logs/autonomous-run.log").write_text("", encoding="utf-8")
    (lane / "factorio.pid").write_text(f"{SERVER_PID}\n", encoding="ascii")
    (lane / "logs/autonomous-run.pid").write_text(
        json.dumps({"pid": RUNNER_PID, "creation_token": RUNNER_TOKEN, "boot_id": BOOT_ID}) + "\n",
        encoding="utf-8",
    )
    digest = _sha(world)
    manifest = {
        "schema_version": "1.0.0", "episode_id": "creator-episode",
        "run_id": "suite-1-attempt-1", "source_save": str(lane / "saves/checkpoint-input.zip"),
        "source_save_sha256": digest, "isolated_save": str(lane / "saves/mod_playground.zip"),
        "isolated_save_sha256": digest, "baseline_world_fingerprint": f"sha256:{digest}",
        "repository_revision": COMMIT, "candidate_repository_revision": COMMIT,
        "creator_repository_revision": "b" * 40,
        "candidate_checkout": str(candidate), "fleet_lane_root": str(lane),
        "deployed_factorio_mod_sha256": directory_hash(lane / "mods/factorio_cursor_rl_agent"),
        "deployed_factorio_training_lab_sha256": directory_hash(lane / "mods/factorio_training_lab"),
        "surface": "nauvis", "force": "player", "checkpoint_fleet": {},
    }
    (lane / "episode/current.json").write_text(json.dumps(manifest), encoding="utf-8")

    server_exe = tmp_path / "factorio"
    server_exe.write_bytes(b"")
    server_exe.chmod(0o755)
    runner_exe = tmp_path / "python3"
    runner_exe.write_bytes(b"")
    runner_exe.chmod(0o755)
    processes = {
        SERVER_PID: ProcessSnapshot(
            pid=SERVER_PID, creation_token=SERVER_TOKEN,
            executable=str(server_exe), argv=tuple(_server_argv(lane, server_exe)),
            cwd=str(lane), fd_targets=("socket:[4242]", "socket:[5252]"),
            network_namespace=NETWORK_NAMESPACE,
        ),
        RUNNER_PID: ProcessSnapshot(
            pid=RUNNER_PID, creation_token=RUNNER_TOKEN,
            executable=str(runner_exe), argv=tuple(_runner_argv(lane, runner_exe, candidate)),
            cwd=str(candidate), fd_targets=(),
        ),
    }
    scan = ListenerScan(sockets=(
        ListeningSocket(inode=4242, host="127.0.0.1", port=RCON_PORT,
                        owner_pids=frozenset({SERVER_PID})),
    ))
    expectation = LaneExpectation(
        lane_root=lane, run_id="suite-1-attempt-1", candidate_commit=COMMIT,
        game_port=GAME_PORT, rcon_port=RCON_PORT,
        server_executable=server_exe, runner_executable=runner_exe,
        server_creation_token=SERVER_TOKEN,
        server_boot_id=BOOT_ID,
    )
    return lane, candidate, expectation, FakeProbe(processes, scan), processes, scan


def _verify(tmp_path: Path, expectation=None, probe=None):
    lane, candidate, default, default_probe, processes, scan = _lane(tmp_path)
    return verify_lane_identity(
        expectation or default,
        probe=probe or default_probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )


def _codes(report):
    return {finding.code for finding in report.findings}


def test_success_is_host_evidence_not_identity_or_dispatch(tmp_path: Path):
    report = _verify(tmp_path)
    assert report.host_evidence_checks_passed, report.to_dict()
    assert report.status == "host_evidence_checks_passed"
    assert report.status != "verified"
    assert report.dispatch_authorized is False
    expected_unverified = {
        "nonce_channel_pairing",
        "independent_manifest_expectation_trust",
        "loaded_engine_state",
        "game_socket_ownership",
    }
    assert expected_unverified <= set(report.unverified)
    assert report.facts["expectation_trust"]["source"] == "caller_pinned"
    assert report.facts["expectation_trust"]["independently_verified"] is False
    assert report.facts["dispatch"]["authorized"] is False
    assert set(report.facts["unverified_facts"]) == expected_unverified
    payload = report.to_dict()
    assert payload["schema_version"] == "1.3.0"
    assert payload["status"] == "host_evidence_checks_passed"
    assert payload["host_evidence_checks_passed"] is True
    assert payload["dispatch_authorized"] is False
    assert report.facts["rcon"]["owner_pids"] == [SERVER_PID]
    assert report.facts["game_udp"]["owner_pids"] == [SERVER_PID]
    assert report.facts["game_udp"]["scope"] == "caller_network_namespace_same_uid"
    assert report.facts["game_udp"]["network_namespace"] == NETWORK_NAMESPACE
    assert report.facts["server_process"]["creation_token_verified"] is True
    assert report.facts["runner_process"]["creation_token_verified"] is True


def test_failure_still_reports_unverified_scope_and_no_dispatch(tmp_path: Path):
    lane, candidate, expectation, probe, _, _ = _lane(tmp_path)
    report = verify_lane_identity(
        replace(expectation, server_creation_token=None), probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert report.host_evidence_checks_passed is False
    assert report.status == "failed"
    assert report.dispatch_authorized is False
    assert "server_creation_token" in report.unverified
    assert "nonce_channel_pairing" in report.unverified
    assert "game_socket_ownership" in report.unverified
    assert report.facts["dispatch"]["authorized"] is False
    assert report.to_dict()["host_evidence_checks_passed"] is False


def test_both_ports_are_probed_and_partial_ownership_stays_unverified(tmp_path: Path):
    class RecordingProbe(FakeProbe):
        def __init__(self, processes, scan):
            super().__init__(processes, scan)
            self.ports = []
            self.udp_ports = []

        def listeners(self, port):
            self.ports.append(port)
            return super().listeners(port)

        def udp_sockets(self, port):
            self.udp_ports.append(port)
            return super().udp_sockets(port)

    lane, candidate, expectation, probe, processes, scan = _lane(tmp_path)
    recording = RecordingProbe(processes, scan)
    report = verify_lane_identity(
        expectation, probe=recording,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert report.host_evidence_checks_passed, report.to_dict()
    assert recording.ports == [RCON_PORT]
    assert recording.udp_ports == [GAME_PORT]
    assert "game_socket_ownership" in report.unverified


def test_runner_manager_helper_flags_do_not_break_contract(tmp_path: Path):
    lane, candidate, expectation, probe, processes, _ = _lane(tmp_path)
    argv = list(processes[RUNNER_PID].argv) + [
        "--opencode-helper-data-root", str(lane / "helper" / "data"),
        "--opencode-helper-report-root", str(lane / "helper" / "reports"),
        "--opencode-helper-dashboard-url", "http://127.0.0.1:9137",
        "--no-opencode-helper",
    ]
    processes[RUNNER_PID] = replace(processes[RUNNER_PID], argv=tuple(argv))
    report = verify_lane_identity(
        expectation, probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert report.host_evidence_checks_passed, report.to_dict()


def test_non_fleet_campaign_manifest_fails_closed(tmp_path: Path):
    lane, candidate, expectation, probe, _, _ = _lane(tmp_path)
    manifest_path = lane / "episode" / "current.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in (
        "run_id", "candidate_repository_revision", "candidate_checkout",
        "fleet_lane_root",
    ):
        manifest.pop(key)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = verify_lane_identity(
        expectation, probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert report.host_evidence_checks_passed is False
    assert "manifest_incomplete" in _codes(report)
    assert "candidate_checkout_missing" in _codes(report)


def test_missing_server_creation_token_fails_closed(tmp_path: Path):
    lane, candidate, expectation, probe, _, _ = _lane(tmp_path)
    report = verify_lane_identity(
        replace(expectation, server_creation_token=None), probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert report.host_evidence_checks_passed is False
    assert "server_creation_token_unavailable" in _codes(report)
    assert "server_creation_token" in report.unverified


def test_boot_bound_records_fail_closed_on_missing_or_changed_boot(tmp_path: Path):
    lane, _, expectation, probe, _, _ = _lane(tmp_path)
    git_reader = lambda path: GitIdentity(revision=COMMIT, dirty=False)
    missing = verify_lane_identity(replace(expectation, server_boot_id=None), probe=probe, git_reader=git_reader)
    assert "server_boot_id_unavailable" in _codes(missing)
    changed = verify_lane_identity(replace(expectation, server_boot_id=OTHER_BOOT_ID), probe=probe, git_reader=git_reader)
    assert "server_boot_id_mismatch" in _codes(changed)
    assert not changed.facts["server_process"]["creation_token_verified"]

    record_path = lane / "logs/autonomous-run.pid"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["boot_id"] = OTHER_BOOT_ID
    record_path.write_text(json.dumps(record), encoding="utf-8")
    changed_runner = verify_lane_identity(expectation, probe=probe, git_reader=git_reader)
    assert "runner_boot_id_mismatch" in _codes(changed_runner)
    assert not changed_runner.facts["runner_process"]["creation_token_verified"]
    del record["boot_id"]
    record_path.write_text(json.dumps(record), encoding="utf-8")
    assert "runner_pid_unverified" in _codes(verify_lane_identity(expectation, probe=probe, git_reader=git_reader))


def test_unreadable_current_boot_fails_closed(tmp_path: Path):
    _, _, expectation, probe, _, _ = _lane(tmp_path)
    probe.boot_id = lambda: None
    report = verify_lane_identity(expectation, probe=probe, git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False))
    assert "boot_id_unavailable" in _codes(report)
    assert not report.host_evidence_checks_passed


def test_procfs_probe_uses_linux_boot_identity(monkeypatch):
    monkeypatch.setattr("tools.lane_identity_verifier._linux_boot_id", lambda: BOOT_ID)
    assert ProcfsProbe().boot_id() == BOOT_ID


def test_fleet_start_token_sidecar_does_not_replace_caller_token(tmp_path: Path):
    lane, candidate, expectation, probe, _, _ = _lane(tmp_path)
    (lane / "factorio.pid.start-token.json").write_text(
        json.dumps({"pid": SERVER_PID, "creation_token": SERVER_TOKEN}),
        encoding="utf-8",
    )
    report = verify_lane_identity(
        replace(expectation, server_creation_token=None), probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert report.host_evidence_checks_passed is False
    assert "server_creation_token_unavailable" in _codes(report)


def test_runner_pid_reuse_token_mismatch_fails(tmp_path: Path):
    lane, candidate, expectation, probe, processes, _ = _lane(tmp_path)
    processes[RUNNER_PID] = replace(processes[RUNNER_PID], creation_token=RUNNER_TOKEN + 1)
    report = verify_lane_identity(
        expectation, probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert "runner_pid_reused" in _codes(report)


def test_server_pid_reuse_token_mismatch_fails(tmp_path: Path):
    lane, candidate, expectation, probe, processes, _ = _lane(tmp_path)
    processes[SERVER_PID] = replace(processes[SERVER_PID], creation_token=SERVER_TOKEN + 1)
    report = verify_lane_identity(
        expectation, probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert "server_pid_reused" in _codes(report)


def test_wrong_runner_rcon_port_fails(tmp_path: Path):
    lane, candidate, expectation, probe, processes, _ = _lane(tmp_path)
    argv = list(processes[RUNNER_PID].argv)
    argv[argv.index("--rcon-port") + 1] = str(RCON_PORT + 2)
    processes[RUNNER_PID] = replace(processes[RUNNER_PID], argv=tuple(argv))
    report = verify_lane_identity(
        expectation, probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert any(code == "command_flag_mismatch" and "runner" in finding.detail
               for code, finding in ((f.code, f) for f in report.findings))


def test_rcon_socket_owned_by_other_process_fails(tmp_path: Path):
    lane, candidate, expectation, probe, _, scan = _lane(tmp_path)
    other = ListeningSocket(inode=4242, host="127.0.0.1", port=RCON_PORT,
                            owner_pids=frozenset({9999}))
    report = verify_lane_identity(
        expectation, probe=FakeProbe(probe.processes, ListenerScan(sockets=(other,))),
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert "rcon_owner_mismatch" in _codes(report)


def test_rcon_listener_must_be_loopback(tmp_path: Path):
    lane, candidate, expectation, probe, _, scan = _lane(tmp_path)
    exposed = ListeningSocket(inode=4242, host="0.0.0.0", port=RCON_PORT,
                              owner_pids=frozenset({SERVER_PID}))
    report = verify_lane_identity(
        expectation, probe=FakeProbe(probe.processes, ListenerScan(sockets=(exposed,))),
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert "rcon_listener_not_loopback" in _codes(report)


def test_incomplete_socket_owner_scan_fails_closed(tmp_path: Path):
    lane, candidate, expectation, probe, _, scan = _lane(tmp_path)
    report = verify_lane_identity(
        expectation,
        probe=FakeProbe(probe.processes, ListenerScan(sockets=scan.sockets, unreadable_same_uid_pids=(1,))),
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert "socket_owner_scan_incomplete" in _codes(report)


@pytest.mark.parametrize("case, expected", [
    ("missing", "game_socket_missing"),
    ("multiple", "game_socket_ambiguous"),
    ("exposed", "game_socket_bind_mismatch"),
    ("wrong_port", "game_socket_bind_mismatch"),
    ("other_owner", "game_socket_owner_mismatch"),
    ("shared_owner", "game_socket_owner_mismatch"),
    ("no_owner", "game_socket_owner_unverified"),
    ("unreadable", "game_socket_owner_scan_incomplete"),
    ("missing_fd", "game_socket_not_in_server_fds"),
])
def test_game_socket_rejects_wrong_or_incomplete_evidence(tmp_path, case, expected):
    _, _, expectation, probe, processes, scan = _lane(tmp_path)
    socket = probe.udp_scan.sockets[0]
    if case == "missing":
        udp = ListenerScan(())
    elif case == "multiple":
        udp = ListenerScan((socket, replace(socket, inode=5253)))
    elif case == "unreadable":
        udp = ListenerScan((socket,), unreadable_same_uid_pids=(999,))
    else:
        changes = {
            "exposed": {"host": "0.0.0.0"}, "wrong_port": {"port": GAME_PORT + 1},
            "other_owner": {"owner_pids": frozenset({999})},
            "shared_owner": {"owner_pids": frozenset({SERVER_PID, 999})},
            "no_owner": {"owner_pids": frozenset()},
        }.get(case, {})
        udp = ListenerScan((replace(socket, **changes),))
    if case == "missing_fd":
        processes[SERVER_PID] = replace(processes[SERVER_PID], fd_targets=("socket:[4242]",))
    report = verify_lane_identity(expectation, probe=FakeProbe(processes, scan, udp),
                                  git_reader=lambda _path: GitIdentity(COMMIT, False))
    assert expected in _codes(report)
    assert not report.host_evidence_checks_passed and not report.dispatch_authorized


def test_udp_scan_failure_never_degrades_to_success(tmp_path):
    _, _, expectation, probe, processes, scan = _lane(tmp_path)
    class FailingProbe(FakeProbe):
        def udp_sockets(self, port):
            raise OSError("UDP table unavailable")
    report = verify_lane_identity(expectation, probe=FailingProbe(processes, scan),
                                  git_reader=lambda _path: GitIdentity(COMMIT, False))
    assert "game_socket_scan_failed" in _codes(report)


@pytest.mark.parametrize("namespace,expected", [(None, "game_namespace_unverified"),
                                                  ("not-a-namespace", "game_namespace_unverified"),
                                                  ("net:[99]", "game_namespace_mismatch")])
def test_server_network_namespace_must_match(tmp_path, namespace, expected):
    _, _, expectation, _, processes, scan = _lane(tmp_path)
    processes[SERVER_PID] = replace(processes[SERVER_PID], network_namespace=namespace)
    report = verify_lane_identity(expectation, probe=FakeProbe(processes, scan),
                                  git_reader=lambda _path: GitIdentity(COMMIT, False))
    assert expected in _codes(report)
    assert not report.host_evidence_checks_passed and not report.dispatch_authorized


def test_unreadable_verifier_namespace_fails_before_udp_scan(tmp_path):
    _, _, expectation, _, processes, scan = _lane(tmp_path)
    class NamespaceUnavailable(FakeProbe):
        def network_namespace(self):
            return None
        def udp_sockets(self, port):
            pytest.fail("must not scan a network namespace that cannot be identified")
    report = verify_lane_identity(expectation, probe=NamespaceUnavailable(processes, scan),
                                  git_reader=lambda _path: GitIdentity(COMMIT, False))
    assert "game_namespace_unverified" in _codes(report)


@pytest.mark.parametrize("family,address,expected", [
    (4, "0100007F", "127.0.0.1"),
    (6, "00000000000000000000000001000000", "::1"),
])
def test_udp_parser_checks_all_socket_states(tmp_path, family, address, expected):
    path = tmp_path / "udp"
    path.write_text("header\n" + "\n".join(
        f"1: {address}:{GAME_PORT:04X} 00000000:0000 {state} 0:0 00:0 0 1000 0 {inode}"
        for state, inode in (("07", 5252), ("01", 5253))))
    assert ProcfsProbe._read_udp_table(path, family, GAME_PORT) == [
        (5252, expected, GAME_PORT), (5253, expected, GAME_PORT)]


def test_udp_parser_fails_closed_on_missing_or_malformed_table(tmp_path):
    path = tmp_path / "udp"
    with pytest.raises(FileNotFoundError):
        ProcfsProbe._read_udp_table(path, 4, GAME_PORT)
    path.write_text("header\nmalformed\n")
    with pytest.raises(ValueError, match="malformed"):
        ProcfsProbe._read_udp_table(path, 4, GAME_PORT)


def test_udp_probe_fails_if_either_address_family_is_unreadable(monkeypatch):
    def read_table(path, family, port):
        if family == 6:
            raise FileNotFoundError("IPv6 table unavailable")
        return [(5252, "127.0.0.1", port)]
    monkeypatch.setattr(ProcfsProbe, "_read_udp_table", staticmethod(read_table))
    with pytest.raises(LaneIdentityError, match="complete Linux UDP"):
        ProcfsProbe().udp_sockets(GAME_PORT)


def test_symlinked_manifest_fails_closed(tmp_path: Path):
    lane, candidate, expectation, probe, _, _ = _lane(tmp_path)
    manifest = lane / "episode/current.json"
    outside = tmp_path / "outside.json"
    outside.write_text(manifest.read_text(), encoding="utf-8")
    manifest.unlink()
    manifest.symlink_to(outside)
    report = verify_lane_identity(
        expectation, probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert "manifest_unreadable" in _codes(report)


def test_save_hash_mismatch_fails_closed(tmp_path: Path):
    lane, candidate, expectation, probe, _, _ = _lane(tmp_path)
    (lane / "saves/mod_playground.zip").write_bytes(b"tampered")
    report = verify_lane_identity(
        expectation, probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert "save_hash_mismatch" in _codes(report)


def test_mod_hash_mismatch_fails_closed(tmp_path: Path):
    lane, candidate, expectation, probe, _, _ = _lane(tmp_path)
    (lane / "mods/factorio_cursor_rl_agent/control.lua").write_text("-- changed\n", encoding="utf-8")
    report = verify_lane_identity(
        expectation, probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert "mod_hash_mismatch" in _codes(report)


def test_duplicate_command_flag_fails_closed(tmp_path: Path):
    lane, candidate, expectation, probe, processes, _ = _lane(tmp_path)
    argv = list(processes[RUNNER_PID].argv) + ["--rcon-port", str(RCON_PORT)]
    processes[RUNNER_PID] = replace(processes[RUNNER_PID], argv=tuple(argv))
    report = verify_lane_identity(
        expectation, probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert "command_flag_missing_or_duplicated" in _codes(report)


def test_runner_password_on_command_line_fails_closed(tmp_path: Path):
    lane, candidate, expectation, probe, processes, _ = _lane(tmp_path)
    argv = list(processes[RUNNER_PID].argv) + ["--rcon-password", "secret"]
    processes[RUNNER_PID] = replace(processes[RUNNER_PID], argv=tuple(argv))
    report = verify_lane_identity(
        expectation, probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert "runner_secret_on_command_line" in _codes(report)


def test_server_start_save_outside_lane_fails_closed(tmp_path: Path):
    lane, candidate, expectation, probe, processes, _ = _lane(tmp_path)
    argv = list(processes[SERVER_PID].argv)
    argv[argv.index("--start-server") + 1] = str(tmp_path / "other.zip")
    processes[SERVER_PID] = replace(processes[SERVER_PID], argv=tuple(argv))
    report = verify_lane_identity(
        expectation, probe=probe,
        git_reader=lambda path: GitIdentity(revision=COMMIT, dirty=False),
    )
    assert "command_flag_mismatch" in _codes(report)


def test_default_git_reader_reports_revision_and_dirty_tree(tmp_path: Path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "lane@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Lane Test"], cwd=repo, check=True)
    (repo / "marker").write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "marker"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "candidate"], cwd=repo, check=True)
    expected = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
    ).strip()

    identity = read_git_identity(repo)
    assert identity.revision == expected
    assert identity.dirty is False

    (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    assert read_git_identity(repo).dirty is True
