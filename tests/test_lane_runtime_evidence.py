# Path: tests/test_lane_runtime_evidence.py | Purpose: Exercise non-authorizing lane evidence composition without RCON.
from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest

from tests.test_lane_identity_verifier import _lane, COMMIT, GitIdentity, RCON_PORT
from tools.lane_identity_verifier import ListenerScan, ListeningSocket
from tools.lane_runtime_evidence import collect_lane_runtime_evidence


MODS = {"base": "2.1.21", "factorio_cursor_rl_agent": "1.0.0"}


class Socket:
    peer = ("127.0.0.1", RCON_PORT)
    local = ("127.0.0.1", 41000)

    def getpeername(self):
        return self.peer

    def getsockname(self):
        return self.local


class Bridge:
    def __init__(self, output, **kwargs):
        self.script_output = output
        self.kwargs = kwargs
        self._rcon = SimpleNamespace(_sock=Socket())
        self.closed = False
        self.calls = []
        self.report = {"ok": True, "ownership_claimed": False,
                       "dispatch_authorized": False, "surface": {"name": "nauvis"},
                       "force": {"name": "player"}, "active_mods": dict(MODS)}

    def runtime_identity_probe(self, surface, force, timeout):
        self.calls.append((surface, force, timeout))
        return self.report

    def close(self):
        self.closed = True


@pytest.fixture
def lane(tmp_path):
    root, _, expectation, probe, processes, scan = _lane(tmp_path)
    digest = hashlib.sha256((root / "episode/current.json").read_bytes()).hexdigest()
    return root, replace(expectation, manifest_sha256=digest), probe, processes


def collect(lane, *, setup=lambda bridge: None, expectation=None, probe=None, mods=MODS):
    root, expected, default_probe, _ = lane
    bridges = []

    def factory(*args, **kwargs):
        bridge = Bridge(*args, **kwargs)
        bridges.append(bridge)
        setup(bridge)
        try:
            kwargs["socket_validator"](bridge._rcon._sock)
        except Exception:
            bridge.close()
            raise
        return bridge

    report = collect_lane_runtime_evidence(
        expectation or expected, password="private", expected_active_mods=mods,
        probe=probe or default_probe, git_reader=lambda path: GitIdentity(COMMIT, False),
        bridge_factory=factory,
    )
    assert "private" not in repr(report)
    return report, bridges


def test_success_is_non_authorizing(lane):
    result, bridges = collect(lane)
    assert result["status"] == "host_and_correlated_probe_consistent"
    assert result["dispatch_authorized"] is False
    assert "loaded_save_identity" in result["unverified"]
    assert bridges[0].calls == [("nauvis", "player", 60.0)]
    assert bridges[0].closed
    assert bridges[0].kwargs["host"] == "127.0.0.1"


def test_factory_must_apply_pre_auth_socket_validator(lane):
    bridges = []

    def factory(*args, **kwargs):
        bridge = Bridge(*args, **kwargs)
        bridges.append(bridge)
        return bridge

    result = collect_lane_runtime_evidence(
        lane[1], password="private", expected_active_mods=MODS, probe=lane[2],
        git_reader=lambda path: GitIdentity(COMMIT, False), bridge_factory=factory,
    )
    assert result["reason"] == "socket_not_checked_before_auth"
    assert bridges[0].closed and not bridges[0].calls


def test_host_change_before_authentication_rejects_socket(lane):
    def setup(_bridge):
        (lane[0] / "saves/mod_playground.zip").write_bytes(b"changed-before-auth")

    result, bridges = collect(lane, setup=setup)
    assert result["reason"] == "host_socket_check_failed"
    assert bridges[0].closed and not bridges[0].calls


@pytest.mark.parametrize("change, reason", [
    ({"manifest_sha256": None}, "manifest_digest_not_pinned"),
    ({"manifest_sha256": "0" * 64}, "host_precheck_failed"),
    ({"server_creation_token": None}, "host_precheck_failed"),
])
def test_precheck_never_connects(lane, change, reason):
    result, bridges = collect(lane, expectation=replace(lane[1], **change))
    assert result["reason"] == reason
    assert not bridges


def test_missing_mod_pin_never_connects(lane):
    result, bridges = collect(lane, mods={})
    assert result["reason"] == "active_mods_not_pinned"
    assert not bridges


@pytest.mark.parametrize("attribute,value,reason", [
    ("peer", ("127.0.0.1", 123), "peer_mismatch"),
    ("peer", ("192.0.2.1", RCON_PORT), "peer_mismatch"),
    ("local", ("192.0.2.1", 123), "local_endpoint_mismatch"),
])
def test_bad_endpoints_do_not_probe(lane, attribute, value, reason):
    result, bridges = collect(lane, setup=lambda bridge: setattr(bridge._rcon._sock, attribute, value))
    assert result["reason"] == reason
    assert bridges[0].closed and not bridges[0].calls


def test_wrong_output_does_not_probe(lane):
    result, bridges = collect(lane, setup=lambda bridge: setattr(bridge, "script_output", lane[0]))
    assert result["reason"] == "output_path_mismatch"
    assert bridges[0].closed and not bridges[0].calls


@pytest.mark.parametrize("key,value", [
    ("active_mods", {"base": "wrong"}), ("force", {"name": "other"}),
    ("dispatch_authorized", True), ("ownership_claimed", True),
])
def test_runtime_mismatch_closes(lane, key, value):
    result, bridges = collect(lane, setup=lambda bridge: bridge.report.update({key: value}))
    assert result["reason"] == "runtime_evidence_mismatch"
    assert bridges[0].closed


def test_probe_exception_closes(lane):
    def setup(bridge):
        def broken(*args, **kwargs):
            raise RuntimeError("private")
        bridge.runtime_identity_probe = broken
    result, bridges = collect(lane, setup=setup)
    assert result["reason"] == "runtime_probe_error"
    assert bridges[0].closed


def test_changed_host_evidence_rejected(lane):
    def setup(bridge):
        original = bridge.runtime_identity_probe
        def changed(*args, **kwargs):
            report = original(*args, **kwargs)
            (lane[0] / "saves/mod_playground.zip").write_bytes(b"changed")
            return report
        bridge.runtime_identity_probe = changed
    result, bridges = collect(lane, setup=setup)
    assert result["reason"] == "host_postcheck_failed"
    assert bridges[0].closed


def test_changed_but_individually_valid_listener_rejected(lane):
    def setup(bridge):
        original = bridge.runtime_identity_probe
        def changed(*args, **kwargs):
            report = original(*args, **kwargs)
            original_socket = lane[2].scan.sockets[0]
            new_inode = original_socket.inode + 1
            lane[2].scan = ListenerScan((ListeningSocket(
                new_inode, original_socket.host, original_socket.port, original_socket.owner_pids,
            ),))
            pid = next(iter(original_socket.owner_pids))
            snapshot = lane[3][pid]
            lane[3][pid] = replace(snapshot, fd_targets=(f"socket:[{new_inode}]", "socket:[5252]"))
            return report
        bridge.runtime_identity_probe = changed
    result, bridges = collect(lane, setup=setup)
    assert result["reason"] == "host_evidence_changed"
    assert bridges[0].closed


def test_changed_but_individually_valid_game_socket_rejected(lane):
    def setup(bridge):
        original = bridge.runtime_identity_probe
        def changed(*args, **kwargs):
            report = original(*args, **kwargs)
            original_socket = lane[2].udp_scan.sockets[0]
            new_inode = original_socket.inode + 1
            lane[2].udp_scan = ListenerScan((replace(original_socket, inode=new_inode),))
            pid = next(iter(original_socket.owner_pids))
            snapshot = lane[3][pid]
            lane[3][pid] = replace(snapshot, fd_targets=("socket:[4242]", f"socket:[{new_inode}]"))
            return report
        bridge.runtime_identity_probe = changed
    result, bridges = collect(lane, setup=setup)
    assert result["reason"] == "host_evidence_changed"
    assert bridges[0].closed


def test_changed_but_matching_network_namespace_rejected(lane):
    def setup(bridge):
        original = bridge.runtime_identity_probe
        def changed(*args, **kwargs):
            report = original(*args, **kwargs)
            namespace = "net:[4026539999]"
            lane[2].network_namespace = lambda: namespace
            pid = next(iter(lane[2].udp_scan.sockets[0].owner_pids))
            lane[3][pid] = replace(lane[3][pid], network_namespace=namespace)
            return report
        bridge.runtime_identity_probe = changed
    result, bridges = collect(lane, setup=setup)
    assert result["reason"] == "host_evidence_changed"
    assert bridges[0].closed


def test_symlink_output_fails_before_connect(lane):
    output = lane[0] / "script-output"
    output.rename(lane[0] / "elsewhere")
    output.symlink_to(lane[0] / "elsewhere", target_is_directory=True)
    result, bridges = collect(lane)
    assert result["reason"] == "host_precheck_failed"
    assert not bridges
