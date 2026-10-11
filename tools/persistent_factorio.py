# Path: tools/persistent_factorio.py | Purpose: Own stable isolated Factorio servers independently of episode runners.
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from tools.rcon_client import RconClient
from tools.runtime_namespace import resolve

ROOT = Path(__file__).resolve().parents[1]
PROTECTED_PORTS = {34199, 27017}


def validate(profile):
    namespace = resolve(profile["runtime"])
    root = Path(profile["server_root"]).resolve()
    data = (ROOT / "data").resolve()
    if not root.is_relative_to(data) or len(root.relative_to(data).parts) < 2:
        raise ValueError("persistent servers require a dedicated isolated data root")
    ports = [int(profile[key]) for key in ("game_port", "rcon_port")]
    if len(set(ports)) != 2 or any(p in PROTECTED_PORTS or not 1 <= p <= 65535 for p in ports):
        raise ValueError("invalid or protected persistent server ports")
    if not 0 < float(profile["speed"]) <= 2:
        raise ValueError("persistent speed must be within (0, 2]")
    return root, namespace


def manager_command(profile, action):
    root, _ = validate(profile)
    return ["bash", str(ROOT / "scripts/manage_linux_deterministic_server.sh"), action,
            "--root", str(root), "--runtime-root", profile["runtime_root"],
            "--game-port", str(profile["game_port"]), "--rcon-port", str(profile["rcon_port"]),
            "--fleet-mode"]


def verify_process(profile):
    """A stale PID file cannot authorize operations on another server."""
    root, _ = validate(profile)
    path = root / "factorio.pid"
    if not path.exists():
        return None
    pid = int(path.read_text())
    if pid == 1502810:
        raise ValueError("protected Factorio process")
    proc = Path(f"/proc/{pid}/cmdline")
    if not proc.exists():
        return None
    argv = proc.read_bytes().decode().split("\0")
    expected = {"--config": str(root / "config.ini"), "--start-server": str(root / "saves/mod_playground.zip"),
                "--mod-directory": str(root / "mods"), "--bind": f"127.0.0.1:{profile['game_port']}",
                "--rcon-bind": f"127.0.0.1:{profile['rcon_port']}"}
    if any(flag not in argv or argv.index(flag) + 1 >= len(argv) or argv[argv.index(flag) + 1] != value
           for flag, value in expected.items()):
        raise ValueError("persistent server PID does not match the configured instance")
    return pid


def unit_text(profile_path, profile, python=sys.executable):
    root, _ = validate(profile)
    def quote(value):
        value = str(value)
        if any(c in value for c in "\r\n\0"):
            raise ValueError("invalid systemd argument")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace('%', '%%') + '"'
    base = [python, "-m", "tools.persistent_factorio"]
    command = lambda action: " ".join(quote(a) for a in [*base, action, "--profile", profile_path])
    return ("[Unit]\nDescription=Persistent isolated Factorio " + profile["runtime"] + " server\n"
            "\n[Service]\nType=forking\nWorkingDirectory=" + quote(ROOT)[1:-1] + "\n"
            "PIDFile=" + quote(root / "factorio.pid")[1:-1] + "\n"
            "ExecStart=" + command("start") + "\nExecStop=" + command("stop") + "\n"
            "Restart=on-failure\nRestartSec=5\nTimeoutStartSec=150\nTimeoutStopSec=90\n"
            "KillMode=control-group\nKillSignal=SIGINT\nRuntimeMaxSec=infinity\n"
            "\n[Install]\nWantedBy=default.target\n")


def operate(profile, action):
    root, namespace = validate(profile)
    if action == "start":
        verify_process(profile)
        # Fleet mode preserves GUI files and the repository source save.
        subprocess.run(manager_command(profile, "start"), check=True)
    pid = verify_process(profile)
    if action == "stop":
        subprocess.run(manager_command(profile, "stop"), check=True)
        return {"running": False}
    if pid is None:
        raise RuntimeError("persistent Factorio server is not running")
    client = RconClient("127.0.0.1", int(profile["rcon_port"]), (root / "rcon-password").read_text().strip())
    try:
        if action == "start":
            other = resolve("laya" if profile["runtime"] == "deterministic" else "deterministic")
            client.command(namespace.command("activate_runtime"))
            client.command(other.command("deactivate_runtime"))
            client.command(f"/sc game.tick_paused=false;game.ticks_to_run=0;game.speed={float(profile['speed'])};rcon.print(game.speed)")
            client.command("/server-save")
        raw = client.command('/sc local ok,v=pcall(function() return helpers.table_to_json{tick=game.tick,speed=game.speed,'
                             'mods=script.active_mods,deterministic=settings.global["factorio_cursor_rl_agent_active"].value,'
                             'laya=settings.global["factorio_laya_agent_active"].value} end);rcon.print(ok and v or tostring(v))')
        facts = json.loads(raw)
    finally:
        client.close()
    if not facts.get(profile["runtime"]) or facts.get("laya" if profile["runtime"] == "deterministic" else "deterministic"):
        raise RuntimeError("persistent runtime activation does not match its profile")
    result = {"running": True, "pid": pid, "server": f"127.0.0.1:{profile['game_port']}", "facts": facts}
    (root.parent / "server-status.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "probe", "unit"))
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    if args.action == "unit":
        print(unit_text(args.profile.resolve(), profile), end="")
    else:
        print(json.dumps(operate(profile, args.action)))


if __name__ == "__main__":
    main()
