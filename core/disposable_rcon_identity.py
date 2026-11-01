# Path: core/disposable_rcon_identity.py | Purpose: Match a local RCON connection to a recorded disposable server process.
"""Linux host checks only; these do not authenticate a save, mod or runtime."""

import ipaddress
import json
import os
from pathlib import Path
import stat
import uuid


class DisposableIdentityError(RuntimeError):
    pass


def _regular_bytes(path, limit=4096):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("not a regular file")
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("record too large")
        return raw
    except (OSError, ValueError) as error:
        raise DisposableIdentityError("server identity record unavailable") from error


def server_process_identity(server, *, proc=Path("/proc")):
    """Check the fleet manager's PID, boot and Linux process-start record."""
    server, proc = Path(server), Path(proc)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        raw_pid = _regular_bytes(server / "factorio.pid").decode("ascii").strip()
        if not raw_pid.isascii() or not raw_pid.isdigit() or raw_pid.startswith("0"):
            raise ValueError("invalid PID")
        pid = int(raw_pid)
        record = json.loads(_regular_bytes(server / "factorio.pid.start-token.json"), object_pairs_hook=unique)
        if (not isinstance(record, dict) or set(record) != {"pid", "creation_token", "boot_id"}
                or type(record["pid"]) is not int or record["pid"] != pid
                or type(record["creation_token"]) is not int or record["creation_token"] <= 0
                or not isinstance(record["boot_id"], str)):
            raise ValueError("invalid token record")
        boot = str(uuid.UUID(record["boot_id"]))
        current_boot = str(uuid.UUID((proc / "sys/kernel/random/boot_id").read_text(encoding="ascii").strip()))
        process_stat = (proc / str(pid) / "stat").read_text(encoding="ascii")
        marker = process_stat.rfind(")")
        if marker < 0:
            raise ValueError("invalid process stat")
        token = int(process_stat[marker + 2:].split()[19])
        if boot != current_boot or token != record["creation_token"]:
            raise ValueError("process token or boot changed")
    except (OSError, UnicodeError, ValueError, TypeError, IndexError, json.JSONDecodeError) as error:
        raise DisposableIdentityError("server process identity cannot be established") from error
    return pid, token, boot


def _endpoint(value):
    address, port = value.split(":", 1)
    raw = bytes.fromhex(address)
    if len(raw) != 4:
        raise ValueError("invalid IPv4 address")
    return str(ipaddress.IPv4Address(raw[::-1])), int(port, 16)


def verify_connected_server_socket(sock, *, server, expected_port, expected_identity, proc=Path("/proc")):
    """Match this established IPv4 connection to an FD of the recorded server.

    The caller must invoke this before RCON authentication. Same-UID processes,
    inherited descriptors, PID reuse races, report forgery and loaded save
    identity remain outside this check's proof.
    """
    proc, server = Path(proc), Path(server)
    try:
        if (type(expected_port) is not int or not 1 <= expected_port <= 65535
                or server_process_identity(server, proc=proc) != expected_identity):
            raise ValueError("server identity changed")
        peer, local = sock.getpeername(), sock.getsockname()
        if (not isinstance(peer, tuple) or peer != ("127.0.0.1", expected_port)
                or not isinstance(local, tuple) or len(local) != 2 or local[0] != "127.0.0.1"
                or type(local[1]) is not int or not 1 <= local[1] <= 65535):
            raise ValueError("socket endpoints are not expected loopback endpoints")
        pid = expected_identity[0]
        if os.readlink(proc / "self/ns/net") != os.readlink(proc / str(pid) / "ns/net"):
            raise ValueError("network namespace differs")
        lines = (proc / "net/tcp").read_text(encoding="ascii").splitlines()
        if not lines:
            raise ValueError("empty TCP table")
        matching = []
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 10:
                raise ValueError("malformed TCP table")
            if fields[3] == "01" and _endpoint(fields[1]) == peer and _endpoint(fields[2]) == local:
                inode = int(fields[9])
                if inode <= 0:
                    raise ValueError("invalid socket inode")
                matching.append(inode)
        if len(matching) != 1:
            raise ValueError("server-side connection is missing or ambiguous")
        target = f"socket:[{matching[0]}]"
        found = False
        for entry in (proc / str(pid) / "fd").iterdir():
            try:
                found |= os.readlink(entry) == target
            except FileNotFoundError:
                continue  # An unrelated descriptor can close while scanning.
        if not found or server_process_identity(server, proc=proc) != expected_identity:
            raise ValueError("server does not hold the connected socket")
    except (OSError, ValueError, IndexError, TypeError, UnicodeError) as error:
        raise DisposableIdentityError("RCON peer cannot be tied to the recorded server process") from error
