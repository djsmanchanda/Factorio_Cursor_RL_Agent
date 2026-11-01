# Path: tools/lane_identity_verifier.py
# Purpose: Check one deterministic checkpoint lane's host evidence read-only and fail closed.
"""Read-only host-evidence checks for a disposable deterministic checkpoint lane.

This module deliberately performs no RCON command, filesystem mutation, process
launch, systemd action, or dispatch.  It compares a caller-pinned expectation
(run id, commit, ports, executables, and a boot-bound server token) against lane
manifests, deployed artifacts, the live ``/proc`` process table, and loopback
RCON and game socket ownership in the verifier's network namespace.

A passing report means exactly ``status == "host_evidence_checks_passed"``: the
host-visible artifacts and processes are mutually consistent with the supplied
expectation.  It is not proof of disposable-lane identity and never authorizes
dispatch, promotion, or live mutation.  The report always records what remains
unverified: nonce/channel pairing, independent manifest or expectation trust,
loaded engine state, and game-socket ownership.

The important limit is explicit: the server manager's ``factorio.pid`` alone
cannot prove the absence of PID reuse.  Fleet mode also publishes a
``factorio.pid.start-token.json`` sidecar, but this verifier does not consume it
yet.  A lane therefore fails closed unless the caller supplies the server
creation token and boot ID captured when the server started. The runner record
must include its own boot ID; legacy records without one fail closed here.
The boot IDs and tokens remain caller- or process-record assertions, not
independent proof of ownership.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid
from typing import Any, Callable, Mapping, Protocol, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.deterministic_fleet_runtime import directory_hash
from tools.runner_process import _linux_boot_id, _process_creation_token


SCHEMA_VERSION = "1.3.0"
_UNVERIFIED_SCOPE: tuple[tuple[str, str], ...] = (
    (
        "nonce_channel_pairing",
        "No nonce or channel handshake pairs the caller expectation, manifest, "
        "runner, and server; matching values can be coincidental or replayed.",
    ),
    (
        "independent_manifest_expectation_trust",
        "The manifest and expectation are caller-pinned inputs. No independent "
        "authority signed or attested either one, so this is not independent proof.",
    ),
    (
        "loaded_engine_state",
        "No live engine or RCON state was read. The loaded save, active mods, and "
        "world tick are still unproven even when the files match.",
    ),
    (
        "game_socket_ownership",
        "UDP checks cover the verifier's network namespace and same-UID process "
        "descriptors only; cross-UID sharing, namespace changes and scan races "
        "remain unverified.",
    ),
)
_SOCKET_TARGET = re.compile(r"^socket:\[(\d+)\]$")
_REQUIRED_MANIFEST_FIELDS = frozenset({
    "schema_version", "episode_id", "run_id", "source_save",
    "source_save_sha256", "isolated_save", "isolated_save_sha256",
    "baseline_world_fingerprint", "repository_revision",
    "candidate_repository_revision", "candidate_checkout", "fleet_lane_root",
    "deployed_factorio_mod_sha256", "deployed_factorio_training_lab_sha256",
    "surface", "force",
})


class LaneIdentityError(RuntimeError):
    """A supplied expectation or evidence cannot be verified safely."""


@dataclass(frozen=True)
class LaneExpectation:
    """Caller-pinned values, not independently trusted evidence.

    Every field here is supplied by the caller.  Matching lane artifacts to
    these values checks host evidence; it does not prove who chose the values.
    """

    lane_root: Path
    run_id: str
    candidate_commit: str
    game_port: int
    rcon_port: int
    server_executable: Path
    runner_executable: Path
    server_creation_token: int | None = None
    manifest_sha256: str | None = None
    server_boot_id: str | None = None


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    creation_token: int
    executable: str | None
    argv: tuple[str, ...]
    cwd: str | None
    fd_targets: tuple[str, ...] | None
    network_namespace: str | None = None


@dataclass(frozen=True)
class ListeningSocket:
    inode: int
    host: str
    port: int
    owner_pids: frozenset[int]


@dataclass(frozen=True)
class ListenerScan:
    sockets: tuple[ListeningSocket, ...]
    unreadable_same_uid_pids: tuple[int, ...] = ()


@dataclass(frozen=True)
class GitIdentity:
    revision: str
    dirty: bool


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str
    fatal: bool = True


@dataclass(frozen=True)
class LaneIdentityReport:
    lane_root: str
    status: str
    host_evidence_checks_passed: bool
    dispatch_authorized: bool
    findings: tuple[Finding, ...]
    unverified: tuple[str, ...]
    facts: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "lane_root": self.lane_root,
            "status": self.status,
            "host_evidence_checks_passed": self.host_evidence_checks_passed,
            "dispatch_authorized": self.dispatch_authorized,
            "findings": [
                {"code": item.code, "detail": item.detail, "fatal": item.fatal}
                for item in self.findings
            ],
            "unverified": list(self.unverified),
            "facts": dict(self.facts),
        }


class ProcessProbe(Protocol):
    def boot_id(self) -> str | None: ...

    def network_namespace(self) -> str | None: ...

    def process(self, pid: int) -> ProcessSnapshot | None: ...

    def listeners(self, port: int) -> ListenerScan: ...

    def udp_sockets(self, port: int) -> ListenerScan: ...


class ProcfsProbe:
    """Minimal read-only Linux ``/proc`` view used by the verifier."""

    def boot_id(self) -> str | None:
        return _linux_boot_id()

    def network_namespace(self) -> str | None:
        return self._read_link(Path("/proc/self/ns/net"))

    def process(self, pid: int) -> ProcessSnapshot | None:
        token = _process_creation_token(pid)
        if token is None:
            return None
        return ProcessSnapshot(
            pid=pid,
            creation_token=token,
            executable=self._read_exe(pid),
            argv=self._read_argv(pid),
            cwd=self._read_link(Path(f"/proc/{pid}/cwd")),
            fd_targets=self._read_fd_targets(pid),
            network_namespace=self._read_link(Path(f"/proc/{pid}/ns/net")),
        )

    def listeners(self, port: int) -> ListenerScan:
        sockets: list[tuple[int, str, int]] = []
        try:
            for table, family in (("/proc/net/tcp", 4), ("/proc/net/tcp6", 6)):
                sockets.extend(self._read_tcp_table(Path(table), family, port))
        except OSError as error:
            raise LaneIdentityError(f"cannot read Linux TCP tables: {error}") from error
        inodes = {inode for inode, _, _ in sockets}
        owners, unreadable = self._socket_owners(inodes)
        return ListenerScan(
            sockets=tuple(
                ListeningSocket(
                    inode=inode, host=host, port=socket_port,
                    owner_pids=frozenset(owners.get(inode, ())),
                )
                for inode, host, socket_port in sockets
            ),
            unreadable_same_uid_pids=tuple(sorted(unreadable)),
        )

    def udp_sockets(self, port: int) -> ListenerScan:
        try:
            sockets = [entry for table, family in (("/proc/net/udp", 4), ("/proc/net/udp6", 6))
                       for entry in self._read_udp_table(Path(table), family, port)]
        except (OSError, ValueError) as error:
            raise LaneIdentityError(f"cannot read complete Linux UDP tables: {error}") from error
        owners, unreadable = self._socket_owners({inode for inode, _, _ in sockets})
        return ListenerScan(
            sockets=tuple(ListeningSocket(inode=inode, host=host, port=socket_port,
                                          owner_pids=frozenset(owners.get(inode, ())))
                          for inode, host, socket_port in sockets),
            unreadable_same_uid_pids=tuple(sorted(unreadable)),
        )

    @staticmethod
    def _read_udp_table(path: Path, family: int, port: int) -> list[tuple[int, str, int]]:
        lines = path.read_text(encoding="ascii", errors="strict").splitlines()
        if not lines:
            raise ValueError(f"empty UDP table {path}")
        rows = []
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 10:
                raise ValueError(f"malformed UDP table {path}")
            address, separator, port_hex = fields[1].partition(":")
            if not separator:
                raise ValueError(f"malformed UDP address in {path}")
            local_port = int(port_hex, 16)
            if local_port != port:
                continue
            inode = int(fields[9])
            if inode <= 0:
                raise ValueError(f"invalid UDP socket inode in {path}")
            rows.append((inode, _decode_proc_address(address, family), local_port))
        return rows

    @staticmethod
    def _read_link(path: Path) -> str | None:
        try:
            return os.readlink(path)
        except OSError:
            return None

    @classmethod
    def _read_exe(cls, pid: int) -> str | None:
        return cls._read_link(Path(f"/proc/{pid}/exe"))

    @classmethod
    def _read_argv(cls, pid: int) -> tuple[str, ...]:
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return ()
        if not raw:
            return ()
        return tuple(os.fsdecode(item) for item in raw.rstrip(b"\0").split(b"\0"))

    @classmethod
    def _read_fd_targets(cls, pid: int) -> tuple[str, ...] | None:
        directory = Path(f"/proc/{pid}/fd")
        try:
            names = os.listdir(directory)
        except OSError:
            return None
        targets = []
        for name in names:
            target = cls._read_link(directory / name)
            if target is not None:
                targets.append(target)
        return tuple(targets)

    @staticmethod
    def _read_tcp_table(path: Path, family: int, port: int) -> list[tuple[int, str, int]]:
        rows: list[tuple[int, str, int]] = []
        try:
            lines = path.read_text(encoding="ascii", errors="replace").splitlines()
        except FileNotFoundError:
            return rows
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 10 or fields[3].upper() != "0A":
                continue
            address, separator, port_hex = fields[1].partition(":")
            if not separator:
                continue
            try:
                local_port = int(port_hex, 16)
                inode = int(fields[9])
                host = _decode_proc_address(address, family)
            except (ValueError, ipaddress.AddressValueError):
                continue
            if local_port == port:
                rows.append((inode, host, local_port))
        return rows

    @staticmethod
    def _socket_owners(inodes: set[int]) -> tuple[dict[int, set[int]], list[int]]:
        owners: dict[int, set[int]] = {inode: set() for inode in inodes}
        unreadable: list[int] = []
        if not inodes:
            return owners, unreadable
        try:
            entries = list(os.scandir("/proc"))
        except OSError as error:
            raise LaneIdentityError(f"cannot scan /proc for socket owners: {error}") from error
        uid = os.getuid()
        for entry in entries:
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                if entry.stat(follow_symlinks=False).st_uid != uid:
                    continue
            except OSError:
                continue
            fd_directory = Path(f"/proc/{pid}/fd")
            try:
                names = os.listdir(fd_directory)
            except OSError:
                unreadable.append(pid)
                continue
            for name in names:
                try:
                    target = os.readlink(fd_directory / name)
                except OSError:
                    continue
                match = _SOCKET_TARGET.match(target)
                if match is not None and int(match.group(1)) in owners:
                    owners[int(match.group(1))].add(pid)
        return owners, unreadable


def _decode_proc_address(value: str, family: int) -> str:
    raw = bytes.fromhex(value)
    if family == 4:
        if len(raw) != 4:
            raise ValueError("invalid IPv4 address width")
        return str(ipaddress.IPv4Address(raw[::-1]))
    if len(raw) != 16:
        raise ValueError("invalid IPv6 address width")
    words = b"".join(raw[index:index + 4][::-1] for index in range(0, 16, 4))
    return str(ipaddress.IPv6Address(words))


class _Builder:
    def __init__(self, lane_root: Path) -> None:
        self.lane_root = str(lane_root)
        self.findings: list[Finding] = []
        self.unverified: list[str] = [code for code, _ in _UNVERIFIED_SCOPE]
        self.facts: dict[str, Any] = {
            "expectation_trust": {
                "source": "caller_pinned",
                "independently_verified": False,
                "detail": (
                    "All expectations are caller-supplied. A passing check compares "
                    "host evidence to those values; it does not independently prove them."
                ),
            },
            "unverified_facts": {code: detail for code, detail in _UNVERIFIED_SCOPE},
            "dispatch": {
                "authorized": False,
                "detail": (
                    "Host evidence checks only. Passing never authorizes dispatch, "
                    "promotion, or live mutation."
                ),
            },
        }

    def fail(self, code: str, detail: str) -> None:
        self.findings.append(Finding(code=code, detail=detail))

    def unverified_claim(self, claim: str) -> None:
        if claim not in self.unverified:
            self.unverified.append(claim)

    def build(self) -> LaneIdentityReport:
        passed = not any(item.fatal for item in self.findings)
        return LaneIdentityReport(
            lane_root=self.lane_root,
            status="host_evidence_checks_passed" if passed else "failed",
            host_evidence_checks_passed=passed,
            dispatch_authorized=False,
            findings=tuple(self.findings),
            unverified=tuple(self.unverified),
            facts=self.facts,
        )


def _canonical(path: Path) -> Path:
    return Path(os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(path)))))


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _contained(path: Path, root: Path, label: str) -> Path:
    """Resolve a path under a verified lane root, rejecting symlink components."""
    absolute = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    if not _is_within(absolute, root):
        raise LaneIdentityError(f"{label} escapes the lane root: {path}")
    current = root
    try:
        relative = absolute.relative_to(root)
    except ValueError as error:
        raise LaneIdentityError(f"{label} escapes the lane root: {path}") from error
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise LaneIdentityError(f"{label} traverses a symlink: {current}")
    real = absolute.resolve()
    if not _is_within(real, root):
        raise LaneIdentityError(f"{label} resolves outside the lane root: {path}")
    return real


def _read_bytes(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def _read_json(path: Path, root: Path, label: str) -> Any:
    real = _contained(path, root, label)
    if not real.is_file():
        raise LaneIdentityError(f"{label} is not a regular file: {real}")
    try:
        return json.loads(_read_bytes(real).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LaneIdentityError(f"{label} is unreadable: {real}: {error}") from error


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, root: Path, label: str) -> Path:
    real = _contained(path, root, label)
    if not real.is_file():
        raise LaneIdentityError(f"{label} is missing or not a regular file: {real}")
    if not stat.S_ISREG(real.stat().st_mode):
        raise LaneIdentityError(f"{label} is not a regular file: {real}")
    return real


def _regular_directory(path: Path, root: Path, label: str) -> Path:
    real = _contained(path, root, label)
    if not real.is_dir():
        raise LaneIdentityError(f"{label} is missing or not a directory: {real}")
    return real


def _reject_symlinks(root: Path, label: str) -> None:
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            candidate = Path(current) / name
            if candidate.is_symlink():
                raise LaneIdentityError(f"{label} contains a symlink: {candidate}")


def _single_flag(argv: Sequence[str], flag: str) -> list[str]:
    values: list[str] = []
    for index, argument in enumerate(argv):
        if argument == flag:
            if index + 1 < len(argv):
                values.append(argv[index + 1])
        elif argument.startswith(flag + "="):
            values.append(argument[len(flag) + 1:])
    return values


def _flag_command_line(argv: Sequence[str], flag: str) -> list[str]:
    return [argument for argument in argv if argument == flag or argument.startswith(flag + "=")]


def _require_flag(
    builder: _Builder, argv: Sequence[str], flag: str, expected: str, label: str,
) -> None:
    values = _single_flag(argv, flag)
    if len(values) != 1:
        builder.fail("command_flag_missing_or_duplicated", f"{label} {flag} must appear exactly once")
    elif values[0] != expected:
        builder.fail("command_flag_mismatch", f"{label} {flag} does not match the expected lane value")


def _require_executable(
    builder: _Builder, snapshot: ProcessSnapshot | None, expected: Path, label: str,
) -> None:
    if snapshot is None:
        return
    if snapshot.executable is None:
        builder.fail("process_executable_unreadable", f"{label} /proc executable link is unreadable")
        return
    if snapshot.executable.endswith(" (deleted)"):
        builder.fail("process_executable_deleted", f"{label} executable was replaced or deleted")
        return
    if _canonical(Path(snapshot.executable)) != _canonical(expected):
        builder.fail("process_executable_mismatch", f"{label} executable does not match the expected path")


def _check_lane_root(builder: _Builder, expected: Path) -> Path | None:
    path = Path(os.path.abspath(os.path.expanduser(os.fspath(expected))))
    if path.is_symlink():
        builder.fail("lane_root_symlink", f"lane root is a symlink: {path}")
        return None
    try:
        root = path.resolve(strict=True)
    except OSError as error:
        builder.fail("lane_root_missing", f"lane root cannot be resolved: {path}: {error}")
        return None
    if not root.is_dir():
        builder.fail("lane_root_not_directory", f"lane root is not a directory: {root}")
        return None
    builder.facts["lane_root"] = str(root)
    return root


def _check_manifest(
    builder: _Builder, root: Path, expectation: LaneExpectation,
) -> dict[str, Any] | None:
    path = root / "episode" / "current.json"
    try:
        payload = _read_json(path, root, "lane manifest")
    except LaneIdentityError as error:
        builder.fail("manifest_unreadable", str(error))
        return None
    if not isinstance(payload, dict):
        builder.fail("manifest_not_object", "lane manifest must be a JSON object")
        return None
    missing = sorted(item for item in _REQUIRED_MANIFEST_FIELDS if item not in payload)
    if missing:
        builder.fail("manifest_incomplete", "lane manifest is missing: " + ", ".join(missing))
    if expectation.manifest_sha256 is not None:
        try:
            digest = _file_sha256(_regular_file(path, root, "lane manifest"))
        except (LaneIdentityError, OSError) as error:
            builder.fail("manifest_hash_unreadable", str(error))
        else:
            builder.facts["manifest_sha256"] = digest
            if digest.lower() != expectation.manifest_sha256.lower():
                builder.fail("manifest_hash_mismatch", "lane manifest SHA-256 does not match the expected value")
    if payload.get("run_id") != expectation.run_id:
        builder.fail("run_id_mismatch", "lane manifest run_id does not match the expected run")
    if str(payload.get("surface")) != "nauvis" or str(payload.get("force")) != "player":
        builder.fail("workflow_identity_mismatch", "lane manifest is not Nauvis/player")
    if str(payload.get("fleet_lane_root")) != str(root):
        builder.fail("lane_root_mismatch", "lane manifest fleet_lane_root does not match this lane")
    candidate_value = str(payload.get("candidate_repository_revision", "")).lower()
    revision_value = str(payload.get("repository_revision", "")).lower()
    expected_commit = expectation.candidate_commit.lower()
    if candidate_value != expected_commit or revision_value != expected_commit:
        builder.fail("candidate_commit_mismatch", "lane manifest candidate or runner commit does not match the expected commit")
    builder.facts["manifest"] = {
        "run_id": payload.get("run_id"),
        "episode_id": payload.get("episode_id"),
        "candidate_repository_revision": payload.get("candidate_repository_revision"),
        "creator_repository_revision": payload.get("creator_repository_revision"),
        "surface": payload.get("surface"),
        "force": payload.get("force"),
    }
    return payload


def _check_saves(builder: _Builder, root: Path, payload: Mapping[str, Any]) -> None:
    evidence: dict[str, Any] = {}
    for field, relative in (("source_save", Path("saves/checkpoint-input.zip")),
                            ("isolated_save", Path("saves/mod_playground.zip"))):
        expected_path = root / relative
        if str(payload.get(field)) != str(expected_path):
            builder.fail("save_path_mismatch", f"lane manifest {field} is not the expected lane path")
            continue
        try:
            real = _regular_file(expected_path, root, field)
            digest = _file_sha256(real)
        except (LaneIdentityError, OSError) as error:
            builder.fail("save_unreadable", f"{field}: {error}")
            continue
        expected_digest = str(payload.get(f"{field}_sha256", "")).lower()
        if digest.lower() != expected_digest:
            builder.fail("save_hash_mismatch", f"{field} SHA-256 does not match the lane manifest")
        evidence[field] = {"path": str(real), "sha256": digest}
    isolated = evidence.get("isolated_save", {}).get("sha256")
    if isolated is not None:
        if str(payload.get("baseline_world_fingerprint")) != f"sha256:{isolated}":
            builder.fail("baseline_fingerprint_mismatch", "baseline world fingerprint does not match the isolated save")
    builder.facts["saves"] = evidence


def _check_mods(builder: _Builder, root: Path, payload: Mapping[str, Any]) -> None:
    evidence: dict[str, Any] = {}
    for name, field in (
        ("factorio_cursor_rl_agent", "deployed_factorio_mod_sha256"),
        ("factorio_training_lab", "deployed_factorio_training_lab_sha256"),
    ):
        directory = root / "mods" / name
        try:
            real = _regular_directory(directory, root, f"deployed mod {name}")
            _reject_symlinks(real, f"deployed mod {name}")
            digest = directory_hash(real)
        except Exception as error:
            builder.fail("mod_evidence_unverified", f"{name}: {error}")
            continue
        if digest.lower() != str(payload.get(field, "")).lower():
            builder.fail("mod_hash_mismatch", f"{name} directory hash does not match the lane manifest")
        evidence[name] = {"path": str(real), "directory_sha256": digest}
    builder.facts["mods"] = evidence


def _check_output_tree(builder: _Builder, root: Path) -> None:
    output = root / "script-output"
    try:
        real = _regular_directory(output, root, "script-output")
        _reject_symlinks(real, "script-output")
    except LaneIdentityError as error:
        builder.fail("output_tree_unverified", str(error))
        return
    builder.facts["script_output"] = {"path": str(real)}


def _read_pid(path: Path, root: Path, label: str) -> int | None:
    real = _regular_file(path, root, f"{label} PID file")
    text = _read_bytes(real).decode("ascii", errors="strict").strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise LaneIdentityError(f"{label} PID file is not a single positive PID")
    return int(text)


def _read_runner_record(path: Path, root: Path) -> tuple[int, int, str]:
    payload = _read_json(path, root, "runner PID record")
    if not isinstance(payload, dict):
        raise LaneIdentityError("runner PID record must be a JSON object")
    pid = payload.get("pid")
    token = payload.get("creation_token")
    if type(pid) is not int or pid <= 0:
        raise LaneIdentityError("runner PID record has no positive integer pid")
    if type(token) is not int or token <= 0:
        raise LaneIdentityError("runner PID record has no positive creation_token")
    boot_id = _valid_boot_id(payload.get("boot_id"))
    if boot_id is None:
        raise LaneIdentityError("runner PID record has no valid boot_id")
    return pid, token, boot_id


def _valid_boot_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        normalized = str(uuid.UUID(value))
    except ValueError:
        return None
    return normalized if value == normalized else None


def _check_server_argv(
    builder: _Builder,
    root: Path,
    payload: Mapping[str, Any],
    snapshot: ProcessSnapshot | None,
    expectation: LaneExpectation,
    expected_executable: Path,
) -> None:
    if snapshot is None:
        return
    argv = snapshot.argv
    if not argv:
        builder.fail("server_argv_unreadable", "server command line is empty or unreadable")
        return
    if _canonical(Path(argv[0])) != _canonical(expected_executable):
        builder.fail("server_argv0_mismatch", "server argv[0] does not match the expected executable")
    expected_flags = {
        "--config": str(root / "config.ini"),
        "--mod-directory": str(root / "mods"),
        "--start-server": str(root / "saves/mod_playground.zip"),
        "--server-settings": str(root / "server-settings.json"),
        "--bind": f"127.0.0.1:{expectation.game_port}",
        "--rcon-bind": f"127.0.0.1:{expectation.rcon_port}",
        "--console-log": str(root / "logs" / "factorio-console.log"),
    }
    for flag, value in expected_flags.items():
        _require_flag(builder, argv, flag, value, "server")
    secret_path = root / "rcon-password"
    try:
        secret_file = _regular_file(secret_path, root, "server RCON secret")
        mode = stat.S_IMODE(secret_file.stat().st_mode)
        if mode != 0o600:
            builder.fail("rcon_secret_permissions", "server RCON secret is not mode 600")
        secret = _read_bytes(secret_file).decode("ascii", errors="strict")
    except (LaneIdentityError, OSError, UnicodeDecodeError) as error:
        builder.fail("rcon_secret_unverified", str(error))
        secret = None
    values = _single_flag(argv, "--rcon-password")
    if len(values) != 1 or not values[0]:
        builder.fail("server_rcon_password_missing", "server command line does not have one non-empty --rcon-password")
    elif secret is not None and values[0] != secret:
        builder.fail("server_rcon_password_mismatch", "server command line password does not match the lane secret")
    builder.facts["server_command"] = {
        "pid": snapshot.pid,
        "executable": snapshot.executable,
        "flags_verified": sorted(expected_flags),
        "rcon_password_matches_secret": secret is not None and values == [secret],
    }


def _check_runner_argv(
    builder: _Builder,
    root: Path,
    payload: Mapping[str, Any],
    snapshot: ProcessSnapshot | None,
    expectation: LaneExpectation,
    expected_executable: Path,
    candidate_checkout: Path | None,
) -> None:
    if snapshot is None:
        return
    argv = snapshot.argv
    if not argv:
        builder.fail("runner_argv_unreadable", "runner command line is empty or unreadable")
        return
    if _canonical(Path(argv[0])) != _canonical(expected_executable):
        builder.fail("runner_argv0_mismatch", "runner argv[0] does not match the expected executable")
    if candidate_checkout is None:
        builder.fail("runner_checkout_unverified", "candidate checkout path is unavailable")
    else:
        script = candidate_checkout / "tools" / "autonomous_run.py"
        occurrences = [argument for argument in argv if _canonical(Path(argument)) == _canonical(script)]
        if len(occurrences) != 1:
            builder.fail("runner_script_mismatch", "runner command line does not execute autonomous_run.py from the candidate checkout exactly once")
        if snapshot.cwd is None:
            builder.fail("runner_cwd_unreadable", "runner working directory is unreadable")
        elif _canonical(Path(snapshot.cwd)) != _canonical(candidate_checkout):
            builder.fail("runner_cwd_mismatch", "runner working directory is not the candidate checkout")
    expected_flags = {
        "--rcon-host": "127.0.0.1",
        "--rcon-port": str(expectation.rcon_port),
        "--rcon-secret-file": str(root / "rcon-password"),
        "--script-output": str(root / "script-output"),
        "--episode-manifest": str(root / "episode" / "current.json"),
        "--run-id": expectation.run_id,
        "--log-file": str(root / "logs" / "autonomous-run.log"),
        "--surface": "nauvis",
        "--force": "player",
    }
    for flag, value in expected_flags.items():
        _require_flag(builder, argv, flag, value, "runner")
    if _flag_command_line(argv, "--rcon-password"):
        builder.fail("runner_secret_on_command_line", "runner command line exposes --rcon-password instead of a secret file")
    builder.facts["runner_command"] = {
        "pid": snapshot.pid,
        "executable": snapshot.executable,
        "cwd": snapshot.cwd,
        "flags_verified": sorted(expected_flags),
    }


def _check_rcon_listener(
    builder: _Builder,
    probe: ProcessProbe,
    expectation: LaneExpectation,
    server_pid: int | None,
    server_snapshot: ProcessSnapshot | None,
) -> None:
    try:
        scan = probe.listeners(expectation.rcon_port)
    except Exception as error:
        builder.fail("rcon_scan_failed", str(error))
        return
    if scan.unreadable_same_uid_pids:
        builder.fail(
            "socket_owner_scan_incomplete",
            "cannot prove unique RCON ownership; same-UID process fd tables are unreadable: "
            + ", ".join(str(pid) for pid in scan.unreadable_same_uid_pids),
        )
        return
    if not scan.sockets:
        builder.fail("rcon_listener_missing", f"no listening RCON socket found on port {expectation.rcon_port}")
        return
    if len(scan.sockets) != 1:
        builder.fail("rcon_listener_ambiguous", f"multiple listening sockets found on port {expectation.rcon_port}")
        return
    listener = scan.sockets[0]
    if listener.host != "127.0.0.1":
        builder.fail("rcon_listener_not_loopback", f"RCON listener address is not 127.0.0.1: {listener.host}")
    if server_pid is None:
        builder.fail("rcon_owner_unverified", "server PID is unavailable")
    elif listener.owner_pids != frozenset({server_pid}):
        if not listener.owner_pids:
            builder.fail("rcon_owner_unverified", "no same-UID process owns the listening RCON socket")
        elif len(listener.owner_pids) > 1:
            builder.fail("rcon_owner_ambiguous", "multiple processes own the listening RCON socket")
        else:
            builder.fail("rcon_owner_mismatch", "the listening RCON socket is owned by a different process")
    if server_snapshot is None:
        builder.fail("rcon_owner_unverified", "server process snapshot is unavailable")
    elif server_snapshot.fd_targets is None:
        builder.fail("rcon_owner_unverified", "server file descriptor table is unreadable")
    elif f"socket:[{listener.inode}]" not in server_snapshot.fd_targets:
        builder.fail("rcon_socket_not_in_server_fds", "server process does not hold the listening RCON socket inode")
    builder.facts["rcon"] = {
        "expected_port": expectation.rcon_port,
        "host": listener.host,
        "inode": listener.inode,
        "owner_pids": sorted(listener.owner_pids),
    }


def _check_game_socket(
    builder: _Builder, probe: ProcessProbe, expectation: LaneExpectation,
    server_pid: int | None, server_snapshot: ProcessSnapshot | None,
) -> None:
    try:
        namespace = probe.network_namespace()
    except Exception as error:
        builder.fail("game_namespace_unverified", str(error))
        return
    if (not isinstance(namespace, str) or not re.fullmatch(r"net:\[\d+\]", namespace)
            or server_snapshot is None or not isinstance(server_snapshot.network_namespace, str)
            or not re.fullmatch(r"net:\[\d+\]", server_snapshot.network_namespace)):
        builder.fail("game_namespace_unverified", "verifier or server network namespace is unavailable")
        return
    if server_snapshot.network_namespace != namespace:
        builder.fail("game_namespace_mismatch", "server and verifier network namespaces differ")
        return
    try:
        scan = probe.udp_sockets(expectation.game_port)
    except Exception as error:
        builder.fail("game_socket_scan_failed", str(error))
        return
    if scan.unreadable_same_uid_pids:
        builder.fail("game_socket_owner_scan_incomplete", "same-UID process fd tables are unreadable: "
                     + ", ".join(str(pid) for pid in scan.unreadable_same_uid_pids))
        return
    if not scan.sockets:
        builder.fail("game_socket_missing", f"no UDP socket found on port {expectation.game_port}")
        return
    if len(scan.sockets) != 1:
        builder.fail("game_socket_ambiguous", f"multiple UDP sockets found on port {expectation.game_port}")
        return
    sock = scan.sockets[0]
    if sock.port != expectation.game_port or sock.host != "127.0.0.1":
        builder.fail("game_socket_bind_mismatch", "game UDP socket is not bound to the expected loopback endpoint")
    if server_pid is None or not sock.owner_pids:
        builder.fail("game_socket_owner_unverified", "game UDP socket owner is unavailable")
    elif sock.owner_pids != frozenset({server_pid}):
        builder.fail("game_socket_owner_mismatch", "game UDP socket is not owned only by the expected same-UID server")
    if server_snapshot is None or server_snapshot.fd_targets is None:
        builder.fail("game_socket_owner_unverified", "server file descriptor snapshot is unavailable")
    elif f"socket:[{sock.inode}]" not in server_snapshot.fd_targets:
        builder.fail("game_socket_not_in_server_fds", "server snapshot does not hold the game UDP socket inode")
    builder.facts["game_udp"] = {
        "expected_port": expectation.game_port, "host": sock.host, "inode": sock.inode,
        "owner_pids": sorted(sock.owner_pids), "scope": "caller_network_namespace_same_uid",
        "network_namespace": namespace,
    }


def read_git_identity(path: Path) -> GitIdentity:
    """Return the checkout revision and cleanliness using read-only Git commands."""
    try:
        revision = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip().lower()
        status = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=normal"],
            check=True, capture_output=True, text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise LaneIdentityError(f"candidate checkout Git identity is unavailable: {error}") from error
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise LaneIdentityError("candidate checkout does not resolve to a full Git commit")
    return GitIdentity(revision=revision, dirty=bool(status.strip()))


def _check_candidate_checkout(
    builder: _Builder,
    payload: Mapping[str, Any],
    expectation: LaneExpectation,
    git_reader: Callable[[Path], GitIdentity],
) -> Path | None:
    value = payload.get("candidate_checkout")
    if not isinstance(value, str) or not value:
        builder.fail("candidate_checkout_missing", "lane manifest has no candidate_checkout")
        return None
    path = Path(os.path.abspath(os.path.expanduser(value)))
    if path.is_symlink():
        builder.fail("candidate_checkout_symlink", f"candidate checkout is a symlink: {path}")
        return None
    try:
        checkout = path.resolve(strict=True)
    except OSError as error:
        builder.fail("candidate_checkout_missing", f"candidate checkout cannot be resolved: {path}: {error}")
        return None
    if not checkout.is_dir():
        builder.fail("candidate_checkout_not_directory", f"candidate checkout is not a directory: {checkout}")
        return None
    try:
        identity = git_reader(checkout)
    except Exception as error:
        builder.fail("candidate_git_unverified", str(error))
        return checkout
    if identity.revision.lower() != expectation.candidate_commit.lower():
        builder.fail("candidate_git_mismatch", "candidate checkout HEAD does not match the expected commit")
    if identity.dirty:
        builder.fail("candidate_git_dirty", "candidate checkout has uncommitted or untracked changes")
    builder.facts["candidate_checkout"] = {
        "path": str(checkout), "revision": identity.revision, "dirty": identity.dirty,
    }
    return checkout


def verify_lane_identity(
    expectation: LaneExpectation,
    *,
    probe: ProcessProbe | None = None,
    git_reader: Callable[[Path], GitIdentity] | None = None,
) -> LaneIdentityReport:
    """Verify one lane without mutating it, issuing RCON, or starting a process."""
    builder = _Builder(expectation.lane_root)
    if not expectation.run_id or not str(expectation.run_id).strip():
        builder.fail("run_id_not_pinned", "an external run id is required")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", str(expectation.candidate_commit)):
        builder.fail("candidate_commit_not_pinned", "an external full candidate commit is required")
    for name, value in (("game_port", expectation.game_port), ("rcon_port", expectation.rcon_port)):
        if type(value) is not int or not 1 <= value <= 65535:
            builder.fail("port_invalid", f"{name} is not a valid port")
    if expectation.game_port == expectation.rcon_port:
        builder.fail("port_conflict", "game and RCON ports must differ")
    expected_server = _canonical(expectation.server_executable)
    expected_runner = _canonical(expectation.runner_executable)
    if not expected_server.is_file():
        builder.fail("expected_server_executable_missing", f"expected server executable is missing: {expected_server}")
    if not expected_runner.is_file():
        builder.fail("expected_runner_executable_missing", f"expected runner executable is missing: {expected_runner}")

    root = _check_lane_root(builder, expectation.lane_root)
    if root is None:
        return builder.build()
    if expectation.server_creation_token is None:
        builder.unverified_claim("server_creation_token")
        builder.fail(
            "server_creation_token_unavailable",
            "a caller-pinned server creation token captured at startup is required",
        )
    elif type(expectation.server_creation_token) is not int or expectation.server_creation_token <= 0:
        builder.fail("server_creation_token_invalid", "server creation token must be a positive integer")

    payload = _check_manifest(builder, root, expectation)
    if payload is not None:
        _check_saves(builder, root, payload)
        _check_mods(builder, root, payload)
    _check_output_tree(builder, root)
    candidate_checkout = _check_candidate_checkout(
        builder, payload or {}, expectation, git_reader or read_git_identity,
    )

    active_probe = probe or ProcfsProbe()
    expected_boot = _valid_boot_id(expectation.server_boot_id)
    if expected_boot is None:
        builder.fail("server_boot_id_unavailable", "a valid boot ID captured with the server token is required")
    try:
        current_boot = _valid_boot_id(active_probe.boot_id())
    except Exception as error:
        current_boot = None
        builder.fail("boot_id_unverified", str(error))
    if current_boot is None:
        builder.fail("boot_id_unavailable", "the current Linux boot ID cannot be verified")
    elif expected_boot is not None and expected_boot != current_boot:
        builder.fail("server_boot_id_mismatch", "server token was captured during a different boot")
    builder.facts["boot_id"] = {
        "current": current_boot,
        "server_expected": expected_boot,
        "expectation_source": "caller_pinned",
    }
    server_pid: int | None = None
    server_snapshot: ProcessSnapshot | None = None
    try:
        server_pid = _read_pid(root / "factorio.pid", root, "server")
    except (LaneIdentityError, OSError, UnicodeDecodeError) as error:
        builder.fail("server_pid_unverified", str(error))
    if server_pid is not None:
        try:
            server_snapshot = active_probe.process(server_pid)
        except Exception as error:
            builder.fail("server_process_unverified", str(error))
        if server_snapshot is None:
            builder.fail("server_process_missing", f"server PID {server_pid} is not a live process")
        else:
            if expectation.server_creation_token is not None:
                if server_snapshot.creation_token != expectation.server_creation_token:
                    builder.fail("server_pid_reused", "server PID creation token differs from the expected token")
            builder.facts["server_process"] = {
                "pid": server_pid,
                "creation_token": server_snapshot.creation_token,
                "creation_token_verified": (
                    type(expectation.server_creation_token) is int
                    and server_snapshot.creation_token == expectation.server_creation_token
                    and expected_boot is not None and expected_boot == current_boot
                ),
                "executable": server_snapshot.executable,
            }
            _require_executable(builder, server_snapshot, expectation.server_executable, "server")
            _check_server_argv(builder, root, payload or {}, server_snapshot, expectation, expected_server)

    runner_pid: int | None = None
    runner_token: int | None = None
    runner_boot: str | None = None
    runner_snapshot: ProcessSnapshot | None = None
    try:
        runner_pid, runner_token, runner_boot = _read_runner_record(root / "logs" / "autonomous-run.pid", root)
    except (LaneIdentityError, OSError, UnicodeDecodeError) as error:
        builder.fail("runner_pid_unverified", str(error))
    if runner_pid is not None:
        if runner_boot != current_boot or current_boot is None:
            builder.fail("runner_boot_id_mismatch", "runner PID record does not match the current boot")
        try:
            runner_snapshot = active_probe.process(runner_pid)
        except Exception as error:
            builder.fail("runner_process_unverified", str(error))
        if runner_snapshot is None:
            builder.fail("runner_process_missing", f"runner PID {runner_pid} is not a live process")
        else:
            if runner_snapshot.creation_token != runner_token:
                builder.fail("runner_pid_reused", "runner PID creation token differs from its published record")
            builder.facts["runner_process"] = {
                "pid": runner_pid,
                "creation_token": runner_snapshot.creation_token,
                "creation_token_verified": (
                    runner_snapshot.creation_token == runner_token
                    and current_boot is not None and runner_boot == current_boot
                ),
                "boot_id": runner_boot,
                "executable": runner_snapshot.executable,
            }
            _require_executable(builder, runner_snapshot, expectation.runner_executable, "runner")
            _check_runner_argv(
                builder, root, payload or {}, runner_snapshot, expectation,
                expected_runner, candidate_checkout,
            )

    _check_rcon_listener(builder, active_probe, expectation, server_pid, server_snapshot)
    _check_game_socket(builder, active_probe, expectation, server_pid, server_snapshot)
    return builder.build()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--game-port", type=int, required=True)
    parser.add_argument("--rcon-port", type=int, required=True)
    parser.add_argument("--server-executable", type=Path, required=True)
    parser.add_argument("--runner-executable", type=Path, required=True)
    parser.add_argument(
        "--server-creation-token", type=int,
        help="Creation token observed when the lane server started; required to pass.",
    )
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--server-boot-id", help="Boot ID captured with the server token; required to pass.")
    args = parser.parse_args(argv)
    report = verify_lane_identity(LaneExpectation(
        lane_root=args.lane_root,
        run_id=args.run_id,
        candidate_commit=args.candidate_commit,
        game_port=args.game_port,
        rcon_port=args.rcon_port,
        server_executable=args.server_executable,
        runner_executable=args.runner_executable,
        server_creation_token=args.server_creation_token,
        server_boot_id=args.server_boot_id,
        manifest_sha256=args.manifest_sha256,
    ))
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.host_evidence_checks_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
