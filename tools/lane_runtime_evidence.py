# Path: tools/lane_runtime_evidence.py | Purpose: Compose host checks with a non-authorizing runtime observation.
"""Opt-in evidence collection, not proof of isolation or permission to dispatch.

Calling this connects and authenticates to RCON and writes a probe report; the
bridge may also prune older probe reports. Caller-supplied expectations and
injected implementations are not independent evidence.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
import ipaddress
from pathlib import Path
import re

from orchestrator.game_bridge import GameBridge
from tools.lane_identity_verifier import LaneExpectation, verify_lane_identity


UNVERIFIED = (
    "independent_expectation_trust", "loaded_save_identity",
    "same_uid_report_forgery", "rcon_process_toctou", "game_socket_ownership",
    "runtime_state_continuity", "dispatch_authority",
)


class _SocketCheckError(ValueError):
    pass


def _loopback_endpoint(endpoint, *, port=None):
    if not isinstance(endpoint, tuple) or len(endpoint) < 2:
        return False
    try:
        address = ipaddress.ip_address(endpoint[0])
    except (ValueError, TypeError):
        return False
    return address.is_loopback and (port is None or endpoint[1] == port)


def collect_lane_runtime_evidence(
    expectation: LaneExpectation, *, password: str,
    expected_active_mods: Mapping[str, str], probe=None, git_reader=None,
    bridge_factory=GameBridge, timeout: float = 60.0,
) -> dict:
    """Collect scoped host and runtime evidence; never authorizes execution.

    The default bridge checks socket endpoints and host evidence before RCON
    authentication. Host checks narrow but cannot eliminate races or establish
    that the connected process served the report. No secret is returned.
    """
    result = {"status": "failed", "dispatch_authorized": False,
              "unverified": list(UNVERIFIED)}

    def fail(code):
        return {**result, "reason": code}

    digest = expectation.manifest_sha256
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        return fail("manifest_digest_not_pinned")
    if (not isinstance(expected_active_mods, Mapping) or not expected_active_mods
            or any(not isinstance(key, str) or not key or not isinstance(value, str)
                   or not value for key, value in expected_active_mods.items())):
        return fail("active_mods_not_pinned")
    expected_mods = dict(expected_active_mods)
    try:
        before = verify_lane_identity(expectation, probe=probe, git_reader=git_reader)
    except Exception:
        return fail("host_precheck_error")
    if not before.host_evidence_checks_passed or before.dispatch_authorized:
        return fail("host_precheck_failed")
    before_facts = copy.deepcopy(before.facts)
    output = Path(expectation.lane_root) / "script-output"
    if before_facts.get("script_output", {}).get("path") != str(output):
        return fail("output_path_mismatch")

    checked_socket = None

    def validate_socket(sock):
        nonlocal checked_socket
        peer = sock.getpeername()
        if not _loopback_endpoint(peer, port=expectation.rcon_port) or peer[0] != "127.0.0.1":
            raise _SocketCheckError("peer_mismatch")
        if not _loopback_endpoint(sock.getsockname()):
            raise _SocketCheckError("local_endpoint_mismatch")
        try:
            at_connect = verify_lane_identity(expectation, probe=probe, git_reader=git_reader)
        except Exception as error:
            raise _SocketCheckError("host_socket_check_error") from error
        if not at_connect.host_evidence_checks_passed or at_connect.dispatch_authorized:
            raise _SocketCheckError("host_socket_check_failed")
        if before_facts != at_connect.facts:
            raise _SocketCheckError("host_evidence_changed_before_auth")
        checked_socket = sock

    bridge = None
    try:
        bridge = bridge_factory(output, host="127.0.0.1", port=expectation.rcon_port,
                                password=password, command_timeout=timeout, socket_validator=validate_socket)
        if Path(bridge.script_output) != output or output.is_symlink() or output.resolve() != output:
            return fail("output_path_mismatch")
        sock = bridge._rcon._sock
        if checked_socket is not sock:
            return fail("socket_not_checked_before_auth")
        peer = sock.getpeername()
        if (not _loopback_endpoint(peer, port=expectation.rcon_port)
                or peer[0] != "127.0.0.1"):
            return fail("peer_mismatch")
        if not _loopback_endpoint(sock.getsockname()):
            return fail("local_endpoint_mismatch")
        runtime = bridge.runtime_identity_probe("nauvis", "player", timeout=timeout)
        if (not isinstance(runtime, dict) or runtime.get("ok") is not True
                or runtime.get("ownership_claimed") is not False
                or runtime.get("dispatch_authorized") is not False
                or runtime.get("surface", {}).get("name") != "nauvis"
                or runtime.get("force", {}).get("name") != "player"
                or runtime.get("active_mods") != expected_mods):
            return fail("runtime_evidence_mismatch")
        runtime = copy.deepcopy(runtime)
    except _SocketCheckError as error:
        return fail(str(error))
    except Exception:
        return fail("runtime_probe_error")
    finally:
        if bridge is not None:
            try:
                bridge.close()
            except Exception:
                pass

    try:
        after = verify_lane_identity(expectation, probe=probe, git_reader=git_reader)
    except Exception:
        return fail("host_postcheck_error")
    if not after.host_evidence_checks_passed or after.dispatch_authorized:
        return fail("host_postcheck_failed")
    if before_facts != after.facts:
        return fail("host_evidence_changed")
    return {**result, "status": "host_and_correlated_probe_consistent",
            "host_facts": before_facts, "runtime_observation": runtime}
