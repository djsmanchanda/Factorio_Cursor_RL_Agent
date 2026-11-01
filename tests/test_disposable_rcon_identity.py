# Path: tests/test_disposable_rcon_identity.py | Purpose: Check local saved-world RCON process binding without Factorio.
import json
import os
from pathlib import Path
import socket

import pytest

from core.disposable_rcon_identity import DisposableIdentityError, server_process_identity, verify_connected_server_socket
from training import laya_saved_world as saved_world


@pytest.fixture
def server_record(tmp_path):
    pid = os.getpid()
    process_stat = Path(f"/proc/{pid}/stat").read_text()
    token = int(process_stat[process_stat.rfind(")") + 2:].split()[19])
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    (tmp_path / "factorio.pid").write_text(str(pid))
    (tmp_path / "factorio.pid.start-token.json").write_text(
        json.dumps({"pid": pid, "creation_token": token, "boot_id": boot}))
    return tmp_path, (pid, token, boot)


@pytest.mark.loopback
def test_matching_live_loopback_socket_is_owned_by_recorded_process(server_record):
    server, identity = server_record
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            with listener.accept()[0]:
                assert server_process_identity(server) == identity
                verify_connected_server_socket(client, server=server, expected_port=port, expected_identity=identity)
                with pytest.raises(DisposableIdentityError):
                    verify_connected_server_socket(client, server=server, expected_port=port + 1,
                                                   expected_identity=identity)


@pytest.mark.loopback
def test_connection_to_another_process_is_rejected(server_record):
    server, _ = server_record
    pid = os.getppid()
    process_stat = Path(f"/proc/{pid}/stat").read_text()
    token = int(process_stat[process_stat.rfind(")") + 2:].split()[19])
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    (server / "factorio.pid").write_text(str(pid))
    (server / "factorio.pid.start-token.json").write_text(
        json.dumps({"pid": pid, "creation_token": token, "boot_id": boot}))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            with listener.accept()[0], pytest.raises(DisposableIdentityError, match="RCON peer"):
                verify_connected_server_socket(client, server=server, expected_port=port,
                                               expected_identity=(pid, token, boot))


@pytest.mark.parametrize("change", ["pid", "token", "boot", "duplicate", "missing", "symlink"])
def test_invalid_or_untrusted_start_record_fails_closed(server_record, change):
    server, _ = server_record
    path = server / "factorio.pid.start-token.json"
    record = json.loads(path.read_text())
    if change == "pid": record["pid"] += 1
    elif change == "token": record["creation_token"] += 1
    elif change == "boot": record["boot_id"] = "00000000-0000-0000-0000-000000000000"
    if change in {"pid", "token", "boot"}: path.write_text(json.dumps(record))
    elif change == "duplicate": path.write_text('{"pid":1,"pid":1,"creation_token":1,"boot_id":"bad"}')
    elif change == "missing": path.unlink()
    else:
        path.unlink()
        path.symlink_to(server / "factorio.pid")
    with pytest.raises(DisposableIdentityError):
        server_process_identity(server)


@pytest.mark.parametrize("bad_peer", [False, True])
def test_adapter_validates_peer_before_bridge_authentication(tmp_path, monkeypatch, bad_peer):
    server = tmp_path / "server"
    server.mkdir()
    (server / "rcon-password").write_text("secret")
    identity = (12, 34, "boot")
    manifest = {"rcon_port": 27018}
    events = []
    monkeypatch.setattr(saved_world, "owned_root", lambda root: root)
    monkeypatch.setattr(saved_world, "verify_instance", lambda *_args, **_kwargs: manifest)
    monkeypatch.setattr(saved_world, "server_process_identity", lambda _server: identity)
    def verify(sock, **kwargs):
        events.append(("validated", kwargs["expected_identity"]))
        if bad_peer: raise DisposableIdentityError("bad peer")
    monkeypatch.setattr(saved_world, "verify_connected_server_socket", verify)
    class Bridge:
        def __init__(self, _output, **kwargs):
            kwargs["socket_validator"](object())
            events.append(("authenticated", None))
    monkeypatch.setattr(saved_world, "GameBridge", Bridge)
    if bad_peer:
        with pytest.raises(saved_world.SavedWorldError, match="RCON peer is unverified"):
            saved_world.SavedWorldAdapter(tmp_path)
        assert events == [("validated", identity)]
    else:
        saved_world.SavedWorldAdapter(tmp_path)
        assert events == [("validated", identity), ("authenticated", None)]


def test_running_adapter_rejects_changed_process_identity(tmp_path, monkeypatch):
    adapter = object.__new__(saved_world.SavedWorldAdapter)
    adapter.root, adapter.manifest = tmp_path, {"rcon_port": 27018}
    adapter._server_identity = (12, 34, "boot")
    monkeypatch.setattr(saved_world, "verify_instance", lambda *_args, **_kwargs: adapter.manifest)
    monkeypatch.setattr(saved_world, "server_process_identity", lambda _server: (12, 35, "boot"))
    with pytest.raises(saved_world.SavedWorldError, match="identity changed"):
        adapter._check_instance()
